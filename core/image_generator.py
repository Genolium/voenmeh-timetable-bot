import os
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Экспортируемая ссылка на async_playwright (для тестов она мокается)
try:
    from playwright.async_api import async_playwright as _async_playwright
except Exception:
    _async_playwright = None
async_playwright = _async_playwright

def print_progress_bar(current: int, total: int, prefix: str = "Прогресс", suffix: str = "", length: int = 30):
    """
    Выводит прогресс-бар в консоль для генерации изображений.
    
    Args:
        current: Текущий прогресс
        total: Общее количество
        prefix: Префикс сообщения
        suffix: Суффикс сообщения
        length: Длина прогресс-бара
    """
    filled_length = int(length * current // total)
    bar = '█' * filled_length + '-' * (length - filled_length)
    percent = f"{100 * current // total}%"
    print(f'\r{prefix} |{bar}| {percent} {suffix}', end='', flush=True)
    if current == total:
        print()  # Новая строка в конце

# Глобальные кэши для ускорения
_bg_images_cache = {}
_template_cache = None
_template_mtime: float | None = None
_browser_instance = None


def _time_to_minutes(t: str) -> int:
    """Конвертирует время в минуты для сортировки."""
    try:
        parts = t.strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 24 * 60 + 59


def _prepare_days(schedule_data: dict) -> List[dict]:
    order = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]
    upper = {k.upper(): v for k, v in schedule_data.items()}
    prepared: List[dict] = []
    for name in order:
        lessons = upper.get(name.upper(), [])
        # Определяем первую пару по минимальному времени (в минутах)
        first = ''
        if lessons:
            try:
                first = sorted(
                    lessons,
                    key=lambda l: _time_to_minutes(l.get('start_time_raw', '23:59'))
                )[0].get('start_time_raw', '')
            except Exception:
                first = ''
        items = []
        for l in lessons:
            title = f"{l.get('subject', '')}{' (' + l.get('type','') + ')' if l.get('type') else ''}"
            room_raw = (l.get('room', '') or '').strip('; ')  # Удаляем только точку с запятой и пробелы, сохраняем звездочки
            room_lower = room_raw.lower()
            # Считаем такие значения отсутствием кабинета
            if not room_raw or ('кабинет' in room_lower and 'не указан' in room_lower):
                room_fmt = ''
            else:
                room_fmt = f"{room_raw}"
            items.append({
                'title': title,
                'room': room_fmt,
                'time': l.get('time','')
            })
        # Добавляем оба ключа для совместимости с тестами и шаблонами: 'name' и 'title'
        prepared.append({'name': name, 'title': name, 'firstStart': first, 'lessons': items})
    return prepared

async def generate_schedule_image(
    schedule_data: dict,
    week_type: str,
    group: str,
    output_path: str,
    viewport_size: Optional[Dict[str, int]] = None,
) -> bool:
    """
    Создает широкоформатное изображение расписания, рендеря HTML в headless-браузере.

    Стратегия, решающая проблему "вытянутой картинки":
    1. Принудительно задается ШИРОКИЙ viewport (окно браузера).
    2. В этом широком окне рендерится HTML, и его двухколоночная сетка занимает правильное место.
    3. Измеряется РЕАЛЬНАЯ высота, которую занял контент.
    4. Высота viewport подгоняется под измеренную высоту.
    5. Делается скриншот, который получается широким и с правильной высотой.
    """
    try:
        # --- ШАГ 1: Рендеринг HTML по шаблону ---
        print_progress_bar(1, 5, f"Генерация {group}", "Подготовка шаблона")
        
        global _template_cache, _bg_images_cache, _template_mtime
        project_root = Path(__file__).resolve().parent.parent
        templates_dir = project_root / "templates"
        template_path = templates_dir / "schedule_template.html"
        try:
            current_mtime = template_path.stat().st_mtime
        except Exception:
            current_mtime = None

        # Перезагружаем шаблон, если он не загружен или изменился на диске
        if _template_cache is None or (_template_mtime is not None and current_mtime is not None and current_mtime != _template_mtime):
            env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=select_autoescape())
            _template_cache = env.get_template("schedule_template.html")
            _template_mtime = current_mtime
        week_slug = 'odd' if 'Неч' in week_type else 'even'
        if not _bg_images_cache:
            from base64 import b64encode
            try:
                orange_path = project_root / "assets" / "orange_background.png"
                _bg_images_cache['orange'] = f"data:image/png;base64,{b64encode(orange_path.read_bytes()).decode()}"
            except Exception: _bg_images_cache['orange'] = ""
            try:
                purple_path = project_root / "assets" / "purple_background.png"
                _bg_images_cache['purple'] = f"data:image/png;base64,{b64encode(purple_path.read_bytes()).decode()}"
            except Exception: _bg_images_cache['purple'] = ""
        html = _template_cache.render(
            week_type=week_type,
            week_slug=week_slug,
            group=group,
            schedule_days=_prepare_days(schedule_data),
            bg_image=(_bg_images_cache['orange'] if week_slug == 'odd' else _bg_images_cache['purple']),
            assets_base=(project_root / 'assets').as_uri(),
        )

        global async_playwright
        if async_playwright is None:
            logging.error("Playwright не инициализирован")
            return False

        # --- ШАГ 2: Запуск браузера и создание скриншота по новой логике ---
        print_progress_bar(2, 5, f"Генерация {group}", "Запуск браузера")
        
        # Создаем лок для каждого вызова generate_schedule_image
        import asyncio
        browser_lock = asyncio.Lock()
        
        async with browser_lock:
            global _browser_instance
            if _browser_instance is None or not _browser_instance.is_connected():
                pw = await async_playwright().__aenter__()
                _browser_instance = await pw.chromium.launch(
                    args=[
                        '--no-sandbox', 
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--disable-web-security',
                        '--disable-features=VizDisplayCompositor'
                    ]
                )
            
            page = await _browser_instance.new_page()
            
            # Устанавливаем увеличенные таймауты
            page.set_default_timeout(120000)  # 2 минуты вместо 30 секунд
            page.set_default_navigation_timeout(120000)
            
            try:
                # --- УСТАНАВЛИВАЕМ ОПТИМИЗИРОВАННЫЙ VIEWPORT ---
                # Широкий viewport с соотношением ~3:2 для красивой сетки
                initial_width = 3000
                initial_height = 2250
                
                await page.set_viewport_size({"width": initial_width, "height": initial_height})
                
                print_progress_bar(3, 5, f"Генерация {group}", "Загрузка контента")
                
                # Увеличиваем таймаут для set_content
                await page.set_content(html, wait_until="domcontentloaded", timeout=120000)
                
                # Ждём полной прогрузки шрифтов
                await page.evaluate("document.fonts.ready")
                await page.wait_for_timeout(500)  # Увеличиваем время ожидания

                # --- ИЗМЕРЯЕМ ВЫСОТУ КОНТЕНТА ---
                print_progress_bar(4, 5, f"Генерация {group}", "Измерение размеров")
                
                # Находим элемент, который мы хотим измерить
                content_element = await page.query_selector('.content-wrapper')
                if not content_element:
                    raise ValueError("Не удалось найти элемент .content-wrapper на странице.")

                # Получаем его реальные размеры в широком окне
                bounding_box = await content_element.bounding_box()
                if not bounding_box:
                    raise ValueError("Не удалось измерить размеры элемента .content-wrapper.")

                # Вычисляем размеры контента
                content_height = bounding_box['height']
                top_margin = bounding_box['y']  # Отступ сверху
                bottom_margin = min(top_margin, 100)  # Симметричный нижний отступ

                # Целевой размер кадра: фиксированный 3:2 (как в оптимизированной версии)
                target_width = initial_width
                target_height = initial_height

                # Текущая требуемая высота под контент
                required_height = int(content_height + top_margin + bottom_margin)

                # Если контент выше — не масштабирую, фиксированная область как было изначально

                # Используем фиксированную высоту 3:2
                final_height = target_height

                # --- УСТАНАВЛИВАЕМ ФИНАЛЬНЫЙ РАЗМЕР И ДЕЛАЕМ СКРИНШОТ ---
                print_progress_bar(5, 5, f"Генерация {group}", "Создание скриншота")
                
                # Устанавливаем финальный viewport фиксированного размера
                await page.set_viewport_size({"width": target_width, "height": final_height})
                # Форсируем полный рефлоу и два кадра, чтобы все стили применились перед скриншотом
                try:
                    await page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                except Exception:
                    pass
                
                # Делаем скриншот всей страницы, которая теперь имеет идеальный размер
                await page.screenshot(path=output_path, type="png")
                
                logging.info(f"✅ Изображение успешно сгенерировано: {output_path}")

            except Exception as e:
                logging.error(f"❌ Ошибка при генерации изображения для {group}: {e}")
                # Закрываем страницу в случае ошибки
                try:
                    await page.close()
                except:
                    pass
                return False
                
            finally:
                # Всегда закрываем страницу
                try:
                    await page.close()
                except:
                    pass
        
            
        return True
    except Exception as e:
        print(f"Ошибка при генерации изображения: {e}")
        return False
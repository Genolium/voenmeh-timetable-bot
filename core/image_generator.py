import os
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import weakref

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
# Удалены глобальные переменные браузера для предотвращения конфликтов между процессами
# Пер-луповые состояния для управления браузером и локами (не делиться между event loop)
_loop_state = weakref.WeakKeyDictionary()

def _get_loop_state():
    """Возвращает (и при необходимости создаёт) состояние для текущего event loop.

    Структура: {"lock": asyncio.Lock, "browser": Browser|None, "ctx": Playwright|None}
    """
    loop = asyncio.get_running_loop()
    state = _loop_state.get(loop)
    if state is None:
        state = {"lock": asyncio.Lock(), "browser": None, "ctx": None}
        _loop_state[loop] = state
    return state


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
        
        # Лок и браузер привязаны к текущему event loop
        state = _get_loop_state()
        async with state["lock"]:
            if state["browser"] is None or not state["browser"].is_connected():
                # Инициализируем и сохраняем контекст, чтобы корректно закрыть при завершении
                state["ctx"] = await async_playwright().__aenter__()
                state["browser"] = await state["ctx"].chromium.launch(
                    args=[
                        '--no-sandbox', 
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--disable-web-security',
                        '--disable-features=VizDisplayCompositor'
                    ]
                )

            page = await state["browser"].new_page()
            
            # Устанавливаем увеличенные таймауты
            page.set_default_timeout(120000)  # 2 минуты вместо 30 секунд
            page.set_default_navigation_timeout(120000)

            try:
                attempt = 0
                last_error = None
                while attempt < 5:  # Increased from 3 to 5
                    attempt += 1
                    try:
                        # --- УСТАНАВЛИВАЕМ ОПТИМИЗИРОВАННЫЙ VIEWPORT ---
                        # Всегда фиксированный широкий viewport 3000x2250
                        initial_width = 3000
                        initial_height = 2250
                        
                        await page.set_viewport_size({"width": initial_width, "height": initial_height})
                        
                        print_progress_bar(3, 5, f"Генерация {group}", "Загрузка контента")
                        
                        await page.set_content(html, timeout=120000)  # Increased timeout
                        
                        # Ждем полной загрузки (увеличенный таймаут)
                        await page.wait_for_load_state("networkidle", timeout=120000)
                        
                        # --- ИЗМЕРЯЕМ РЕАЛЬНУЮ ВЫСОТУ КОНТЕНТА ---
                        content_height = await page.evaluate("""
                            () => {
                                // Пробуем найти основной контейнер по разным селекторам
                                const selectors = ['.content-wrapper', '.days-grid', 'body'];
                                for (const selector of selectors) {
                                    const container = document.querySelector(selector);
                                    if (container) {
                                        const rect = container.getBoundingClientRect();
                                        console.log(`Found ${selector}: height=${rect.height}`);
                                        return rect.height;
                                    }
                                }
                                // Fallback: измеряем всю страницу
                                const bodyHeight = Math.max(
                                    document.body.scrollHeight,
                                    document.body.offsetHeight,
                                    document.documentElement.clientHeight,
                                    document.documentElement.scrollHeight,
                                    document.documentElement.offsetHeight
                                );
                                console.log(`Fallback body height: ${bodyHeight}`);
                                return bodyHeight;
                            }
                        """)
                        
                        if content_height == 0:
                            raise ValueError("Не удалось измерить высоту контента - все селекторы вернули 0")
                        
                        # Подгоняем высоту viewport под контент + отступы
                        final_height = int(content_height + 100)  # 100px запас
                        
                        await page.set_viewport_size({"width": initial_width, "height": final_height})
                        
                        # Дополнительная задержка для стабилизации после изменения viewport
                        await asyncio.sleep(1.0)
                        
                        # Проверяем, что элементы действительно загрузились
                        elements_loaded = await page.evaluate("""
                            () => {
                                const wrapper = document.querySelector('.content-wrapper');
                                const grid = document.querySelector('.days-grid');
                                const lessons = document.querySelectorAll('.lesson-row');
                                return {
                                    wrapper: wrapper ? wrapper.offsetHeight : 0,
                                    grid: grid ? grid.offsetHeight : 0,
                                    lessons: lessons.length
                                };
                            }
                        """)
                        
                        logging.info(f"Elements loaded: {elements_loaded}")
                        
                        print_progress_bar(4, 5, f"Генерация {group}", "Скриншот")
                        
                        await page.screenshot(
                            path=output_path,
                            full_page=False,
                            type="png",
                            timeout=120000
                        )
                        
                        print_progress_bar(5, 5, f"Генерация {group}", "Готово")
                        
                        return True
                        
                    except Exception as inner_e:
                        last_error = inner_e
                        logging.warning(f"Попытка {attempt}/5 провалилась: {inner_e}")
                        await asyncio.sleep(1)  # Small delay before retry
                
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


async def shutdown_image_generator():
    """Корректно закрывает ресурсы Playwright/Chromium для предотвращения утечек."""
    try:
        state = _get_loop_state()
        async with state["lock"]:
            browser = state.get("browser")
            ctx = state.get("ctx")
            if browser is not None:
                try:
                    if browser.is_connected():
                        await browser.close()
                finally:
                    state["browser"] = None
            if ctx is not None:
                try:
                    await ctx.__aexit__(None, None, None)
                finally:
                    state["ctx"] = None
    except Exception:
        # Не бросаем исключение при остановке
        pass

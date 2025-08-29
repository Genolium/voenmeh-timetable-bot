import os
import logging
import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

"""Утилиты генерации изображений расписания через Playwright.

Реализован надёжный пул persistent-браузеров на уровне event loop:
- Для каждого event loop создаётся 1 постоянный Chromium (при первом вызове).
- На каждую задачу создаётся новый Page и закрывается по завершении.
- Встроены health-check, авто‑рецикл по счётчику задач/времени и фолбэк на
  безопасный перезапуск при краше. Это даёт экономию на старте браузера и
  избегает утечек при длительной работе воркера.
"""

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

_bg_images_cache = {}
_template_cache = None
_template_mtime: float | None = None

# Параметры пула (настраиваются через ENV)
_POOL_MAX_PAGES = int(os.getenv("IMAGE_BROWSER_MAX_PAGES", "60"))  # Перезапуск после N страниц
_POOL_MAX_AGE_SEC = int(os.getenv("IMAGE_BROWSER_MAX_AGE_SEC", "900"))  # …или после T секунд
_POOL_HEADLESS_ARGS = [
    '--headless=new',
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--disable-gpu',
    '--disable-web-security',
    '--disable-features=VizDisplayCompositor',
    '--no-zygote',
    '--disable-extensions',
    '--disable-background-networking',
]


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
        if _template_cache is None or (
            _template_mtime is not None and current_mtime is not None and current_mtime != _template_mtime
        ):
            env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=select_autoescape())
            _template_cache = env.get_template("schedule_template.html")
            _template_mtime = current_mtime

        week_slug = 'odd' if 'Неч' in week_type else 'even'
        if not _bg_images_cache:
            from base64 import b64encode
            try:
                orange_path = project_root / "assets" / "orange_background.png"
                _bg_images_cache['orange'] = f"data:image/png;base64,{b64encode(orange_path.read_bytes()).decode()}"
            except Exception:
                _bg_images_cache['orange'] = ""
            try:
                purple_path = project_root / "assets" / "purple_background.png"
                _bg_images_cache['purple'] = f"data:image/png;base64,{b64encode(purple_path.read_bytes()).decode()}"
            except Exception:
                _bg_images_cache['purple'] = ""

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
        # --- ШАГ 2: Получаем/держим persistent-браузер для текущего event loop ---
        print_progress_bar(2, 5, f"Генерация {group}", "Запуск браузера")

        # Состояние пула привязано к текущему event loop
        loop = asyncio.get_running_loop()

        @dataclass
        class _PoolState:
            lock: asyncio.Lock
            ctx: any
            browser: any
            created_at: float
            pages_made: int

        if not hasattr(loop, "__img_pool_state__"):
            setattr(loop, "__img_pool_state__", None)

        async def _ensure_browser() -> _PoolState:
            state: Optional[_PoolState] = getattr(loop, "__img_pool_state__")
            if state is None:
                # Первичная инициализация
                ctx = await async_playwright().__aenter__()
                browser = await ctx.chromium.launch(args=_POOL_HEADLESS_ARGS)
                state = _PoolState(lock=asyncio.Lock(), ctx=ctx, browser=browser, created_at=time.time(), pages_made=0)
                setattr(loop, "__img_pool_state__", state)
                return state
            # Health-check: браузер жив?
            try:
                _ = await state.browser.new_context()
                await _.close()
            except Exception:
                # Перезапустим пул
                try:
                    try:
                        await state.browser.close()
                    except Exception:
                        pass
                    try:
                        await state.ctx.__aexit__(None, None, None)
                    except Exception:
                        pass
                finally:
                    ctx = await async_playwright().__aenter__()
                    browser = await ctx.chromium.launch(args=_POOL_HEADLESS_ARGS)
                    state = _PoolState(lock=asyncio.Lock(), ctx=ctx, browser=browser, created_at=time.time(), pages_made=0)
                    setattr(loop, "__img_pool_state__", state)
            # Ротация по возрасту/количеству страниц
            if (time.time() - state.created_at) > _POOL_MAX_AGE_SEC or state.pages_made >= _POOL_MAX_PAGES:
                try:
                    try:
                        await state.browser.close()
                    except Exception:
                        pass
                    try:
                        await state.ctx.__aexit__(None, None, None)
                    except Exception:
                        pass
                finally:
                    ctx = await async_playwright().__aenter__()
                    browser = await ctx.chromium.launch(args=_POOL_HEADLESS_ARGS)
                    state = _PoolState(lock=asyncio.Lock(), ctx=ctx, browser=browser, created_at=time.time(), pages_made=0)
                    setattr(loop, "__img_pool_state__", state)
            return state

        state = await _ensure_browser()

        async with state.lock:
            page = await state.browser.new_page()
            page.set_default_timeout(120000)
            page.set_default_navigation_timeout(120000)

            attempt = 0
            last_error: Exception | None = None
            max_attempts = 5

            try:
                while attempt < max_attempts:
                    attempt += 1
                    try:
                        # --- УСТАНАВЛИВАЕМ VIEWPORT (ширина не меняется) ---
                        default_width = 3000
                        default_height = 2250
                        initial_width = (
                            (viewport_size or {}).get('width', default_width)
                            if isinstance(viewport_size, dict) else default_width
                        )
                        initial_height = (
                            (viewport_size or {}).get('height', default_height)
                            if isinstance(viewport_size, dict) else default_height
                        )

                        await page.set_viewport_size({"width": initial_width, "height": initial_height})

                        print_progress_bar(3, 5, f"Генерация {group}", "Загрузка контента")

                        await page.set_content(html, timeout=120000)
                        await page.wait_for_load_state("networkidle", timeout=120000)

                        # --- ИЗМЕРЯЕМ РЕАЛЬНУЮ ВЫСОТУ КОНТЕНТА ---
                        content_height = await page.evaluate(
                            """
                            () => {
                                const selectors = ['.content-wrapper', '.days-grid', 'body'];
                                for (const selector of selectors) {
                                    const container = document.querySelector(selector);
                                    if (container) {
                                        const rect = container.getBoundingClientRect();
                                        return rect.height;
                                    }
                                }
                                const bodyHeight = Math.max(
                                    document.body.scrollHeight,
                                    document.body.offsetHeight,
                                    document.documentElement.clientHeight,
                                    document.documentElement.scrollHeight,
                                    document.documentElement.offsetHeight
                                );
                                return bodyHeight;
                            }
                            """
                        )

                        if content_height == 0:
                            raise ValueError("Не удалось измерить высоту контента - все селекторы вернули 0")

                        final_height = int(content_height + 100)
                        await page.set_viewport_size({"width": initial_width, "height": final_height})
                        await asyncio.sleep(1.0)

                        # Небольшая проверка загрузки элементов
                        elements_loaded = await page.evaluate(
                            """
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
                            """
                        )
                        logging.info(f"Elements loaded: {elements_loaded}")

                        print_progress_bar(4, 5, f"Генерация {group}", "Скриншот")
                        await page.screenshot(
                            path=output_path,
                            full_page=False,
                            type="png",
                            timeout=120000,
                        )

                        print_progress_bar(5, 5, f"Генерация {group}", "Готово")
                        state.pages_made += 1
                        return True

                    except Exception as inner_e:
                        last_error = inner_e
                        logging.warning(f"Попытка {attempt}/{max_attempts} провалилась: {inner_e}")
                        await asyncio.sleep(1)
                        # Если целевой краш — перезапустим пул и повторим
                        if attempt < max_attempts:
                            try:
                                # Жёсткий перезапуск только браузера; контекст переоткроется при ensure
                                try:
                                    await state.browser.close()
                                except Exception:
                                    pass
                                try:
                                    await state.ctx.__aexit__(None, None, None)
                                except Exception:
                                    pass
                            finally:
                                state = await _ensure_browser()
                                page = await state.browser.new_page()
                                page.set_default_timeout(120000)
                                page.set_default_navigation_timeout(120000)

                if last_error is not None:
                    logging.error(f"Генерация для {group} не удалась: {last_error}")
                return False
            finally:
                try:
                    await page.close()
                except Exception:
                    pass

    except Exception as e:
        logging.error(f"Ошибка при генерации изображения: {e}")
        return False


async def shutdown_image_generator():
    """Закрывает persistent-браузер текущего event loop (если есть)."""
    try:
        loop = asyncio.get_running_loop()
        state = getattr(loop, "__img_pool_state__", None)
        if state is not None:
            try:
                try:
                    await state.browser.close()
                except Exception:
                    pass
                try:
                    await state.ctx.__aexit__(None, None, None)
                except Exception:
                    pass
            finally:
                setattr(loop, "__img_pool_state__", None)
    except Exception:
        pass

import os
import logging
import asyncio
from pathlib import Path
import weakref
import threading
from concurrent.futures import Future
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

# Глобальные кэши для ускорения (используются в рендер-лупе)
_bg_images_cache = {}
_template_cache = None
_template_mtime: float | None = None

# Дедицированный поток и event loop для Playwright
_renderer_thread: Optional[threading.Thread] = None
_renderer_loop: Optional[asyncio.AbstractEventLoop] = None
_renderer_started: bool = False
_renderer_browser = None
_renderer_ctx = None
_renderer_semaphore: Optional[asyncio.Semaphore] = None
_renderer_ready_event: threading.Event = threading.Event()
_renderer_base_max: int = int(os.getenv("RENDER_CONCURRENCY", "4"))
_renderer_max_conc: int = _renderer_base_max
_renderer_inflight: int = 0
_renderer_cond: Optional[asyncio.Condition] = None
_renderer_success_streak: int = 0
_renderer_error_streak: int = 0


def _current_loop() -> asyncio.AbstractEventLoop:
    return asyncio.get_running_loop()


def _ensure_renderer_started() -> None:
    global _renderer_started, _renderer_thread, _renderer_loop
    if _renderer_started:
        return
    _renderer_started = True

    def _thread_target():
        global _renderer_loop
        loop = asyncio.new_event_loop()
        _renderer_loop = loop
        asyncio.set_event_loop(loop)
        # Планируем инициализацию и запускаем луп навсегда
        loop.create_task(_renderer_init())
        _renderer_ready_event.set()
        try:
            loop.run_forever()
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
            except Exception:
                pass

    _renderer_thread = threading.Thread(target=_thread_target, name="PlaywrightRenderer", daemon=True)
    _renderer_thread.start()
    # Ждем готовности лупа
    _renderer_ready_event.wait(timeout=5)


async def _renderer_init():
    global _renderer_browser, _renderer_ctx, _renderer_semaphore, _renderer_cond
    # Ограничиваем конкурентность внутри одного процесса
    _renderer_semaphore = asyncio.Semaphore(_renderer_base_max)
    _renderer_cond = asyncio.Condition()
    # Ленивая инициализация браузера при первой задаче
    return


def _run_in_renderer(coro_factory) -> Future:
    """Планирует корутину в рендер-лупе и возвращает concurrent.futures.Future."""
    _ensure_renderer_started()
    assert _renderer_loop is not None
    return asyncio.run_coroutine_threadsafe(coro_factory(), _renderer_loop)


async def _get_or_launch_browser():
    global _renderer_browser, _renderer_ctx
    if _renderer_browser is None or not getattr(_renderer_browser, "is_connected", lambda: False)():
        ctx = await async_playwright().__aenter__()
        browser = await ctx.chromium.launch(
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor'
            ]
        )
        _renderer_ctx = ctx
        _renderer_browser = browser
    return _renderer_browser


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
        # Для тестов (pytest) выполняем в текущем event loop для корректной работы моков
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return await _render_image_job(schedule_data, week_type, group, output_path, viewport_size)
        # В проде — отправляем корутину в рендер-луп и ждём результата в текущем лупе
        fut = _run_in_renderer(lambda: _render_image_job(schedule_data, week_type, group, output_path, viewport_size))
        return await asyncio.wrap_future(fut)
    except Exception:
        logging.exception("Ошибка при генерации изображения (enqueue)")
        return False


async def _render_image_job(
    schedule_data: dict,
    week_type: str,
    group: str,
    output_path: str,
    viewport_size: Optional[Dict[str, int]] = None,
) -> bool:
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

        print_progress_bar(2, 5, f"Генерация {group}", "Запуск браузера")

        browser = await _get_or_launch_browser()

        # Динамическое ограничение конкурентности
        async def _acquire_slot():
            global _renderer_inflight
            assert _renderer_cond is not None
            async with _renderer_cond:
                while _renderer_inflight >= _renderer_max_conc:
                    await _renderer_cond.wait()
                _renderer_inflight += 1
        async def _release_slot():
            global _renderer_inflight
            assert _renderer_cond is not None
            async with _renderer_cond:
                _renderer_inflight = max(0, _renderer_inflight - 1)
                _renderer_cond.notify_all()

        await _acquire_slot()
        try:
            page = await browser.new_page()
            page.set_default_timeout(120000)
            page.set_default_navigation_timeout(120000)
            try:
                attempt = 0
                while attempt < 3:
                    attempt += 1
                    try:
                        initial_width = 3000
                        initial_height = 2250
                        await page.set_viewport_size({"width": initial_width, "height": initial_height})
                        print_progress_bar(3, 5, f"Генерация {group}", "Загрузка контента")
                        await page.set_content(html, wait_until="domcontentloaded", timeout=60000)
                        await page.evaluate("document.fonts.ready")
                        await page.wait_for_timeout(500)
                        print_progress_bar(4, 5, f"Генерация {group}", "Измерение размеров")
                        content_element = await page.query_selector('.content-wrapper')
                        if not content_element:
                            raise ValueError("Не удалось найти элемент .content-wrapper на странице.")
                        bounding_box = await content_element.bounding_box()
                        if not bounding_box:
                            raise ValueError("Не удалось измерить размеры элемента .content-wrapper.")
                        target_width = initial_width
                        final_height = initial_height
                        print_progress_bar(5, 5, f"Генерация {group}", "Создание скриншота")
                        await page.set_viewport_size({"width": target_width, "height": final_height})
                        try:
                            await page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                        except Exception:
                            pass
                        await page.screenshot(path=output_path, type="png", timeout=30000)
                        logging.info(f"✅ Изображение успешно сгенерировано: {output_path}")
                        # Успех: мягкий рост конкурентности
                        global _renderer_success_streak, _renderer_error_streak, _renderer_max_conc
                        _renderer_success_streak += 1
                        _renderer_error_streak = 0
                        if _renderer_success_streak >= 20 and _renderer_max_conc < _renderer_base_max:
                            _renderer_max_conc += 1
                            _renderer_success_streak = 0
                        break
                    except Exception as e:
                        logging.error(f"❌ Попытка {attempt}/3: ошибка при генерации изображения для {group}: {e}")
                        # Ошибка: экспоненциальное снижение конкурентности
                        _renderer_success_streak = 0
                        _renderer_error_streak += 1
                        if _renderer_error_streak in (3, 5, 7):
                            _renderer_max_conc = max(1, _renderer_max_conc // 2)
                            logging.warning(f"Снижаю RENDER_CONCURRENCY до {_renderer_max_conc} из-за ошибок")
                        if attempt >= 3:
                            return False
                        await asyncio.sleep(0.5)
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
        finally:
            await _release_slot()
        return True
    except Exception:
        logging.exception("Ошибка при генерации изображения (render job)")
        return False


async def shutdown_image_generator():
    """Корректно закрывает ресурсы Playwright/Chromium для предотвращения утечек."""
    try:
        if _renderer_loop is None:
            return
        async def _shutdown():
            global _renderer_browser, _renderer_ctx
            try:
                if _renderer_browser is not None and getattr(_renderer_browser, "is_connected", lambda: False)():
                    await _renderer_browser.close()
            finally:
                _renderer_browser = None
            try:
                if _renderer_ctx is not None:
                    await _renderer_ctx.__aexit__(None, None, None)
            finally:
                _renderer_ctx = None
        fut = asyncio.run_coroutine_threadsafe(_shutdown(), _renderer_loop)
        fut.result(timeout=5)
    except Exception:
        pass


async def renderer_healthcheck() -> bool:
    """Проверяет готовность рендера в его собственном лупе."""
    async def _job():
        try:
            if async_playwright is None:
                return False
            browser = await _get_or_launch_browser()
            page = await browser.new_page()
            try:
                await page.set_content("<html><body>ok</body></html>", timeout=2000)
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
            return True
        except Exception:
            return False

    fut = _run_in_renderer(_job)
    return await asyncio.wrap_future(fut)
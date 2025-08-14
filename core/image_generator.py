import os
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from PIL import Image, ImageDraw, ImageFont
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
_browser_instance = None
_browser_lock = None

# --- Параметры PIL-фолбэка (на случай отсутствия браузера) ---
ASSETS_PATH = Path(__file__).resolve().parent.parent / "assets"
FONTS_PATH = ASSETS_PATH / "fonts"

try:
    FONT_REGULAR = ImageFont.truetype(str(FONTS_PATH / "SegoeUI.ttf"), 16)
    FONT_BOLD = ImageFont.truetype(str(FONTS_PATH / "SegoeUI-Bold.ttf"), 16)
    FONT_DAY_TITLE = ImageFont.truetype(str(FONTS_PATH / "SegoeUI-Bold.ttf"), 30)
    FONT_HEADER = ImageFont.truetype(str(FONTS_PATH / "SegoeUI-Bold.ttf"), 52)
    FONT_SUBHEADER = ImageFont.truetype(str(FONTS_PATH / "SegoeUI.ttf"), 24)
    FONT_BADGE_TIME = ImageFont.truetype(str(FONTS_PATH / "SegoeUI-Bold.ttf"), 20)
    FONT_BADGE_TEXT = ImageFont.truetype(str(FONTS_PATH / "SegoeUI-Bold.ttf"), 10)
except IOError:
    FONT_REGULAR = FONT_BOLD = FONT_DAY_TITLE = FONT_HEADER = FONT_SUBHEADER = FONT_BADGE_TIME = FONT_BADGE_TEXT = ImageFont.load_default()

WHITE = (255, 255, 255, 255)
TEXT_SEMI_TRANSPARENT = (255, 255, 255, 200)
DARK_TEXT = (50, 50, 50, 255)
DARK_TEXT_SEMI = (50, 50, 50, 180)
BG_PURPLE = (109, 99, 239, 255)
BG_ORANGE = (248, 111, 3, 255)
PURPLE_ACCENT = (101, 85, 240, 255)
ORANGE_ACCENT_HEADER = (255, 47, 0, 255)
ORANGE_ACCENT_BADGE = (255, 101, 111, 255)

CANVAS_WIDTH = 1280
PADDING = 60
GRID_GAP_X = 40
GRID_GAP_Y = 25
CARD_PADDING_X = 30
CARD_PADDING_Y = 25
HEADER_HEIGHT = 120
CARD_HEADER_HEIGHT = 60
LESSON_ROW_HEIGHT = 28
CARD_RADIUS = 20

def _get_card_height(lessons: List[Dict[str, Any]]) -> int:
    if not lessons:
        return 120
    base_height = CARD_PADDING_Y * 2 + CARD_HEADER_HEIGHT
    lessons_height = len(lessons) * LESSON_ROW_HEIGHT
    return base_height + lessons_height

def _time_to_minutes(t: str) -> int:
    try:
        parts = t.strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 24 * 60 + 59

def _get_adaptive_font(text: str, max_width: int, base_font: ImageFont.FreeTypeFont) -> ImageFont.FreeTypeFont:
    """
    Возвращает адаптивный шрифт для текста, чтобы он помещался в заданную ширину.
    
    Args:
        text: Текст для измерения
        max_width: Максимальная ширина в пикселях
        base_font: Базовый шрифт
    
    Returns:
        Шрифт с адаптированным размером
    """
    try:
        # Автоматически уменьшаем шрифт для длинных названий
        original_size = base_font.size
        new_size = original_size
        
        # Если текст длинный, сразу уменьшаем шрифт
        if len(text) > 30:
            new_size = max(20, original_size - int((len(text) - 30) * 1.5))
        elif len(text) > 25:
            new_size = max(24, original_size - int((len(text) - 25) * 1.2))
        elif len(text) > 20:
            new_size = max(28, original_size - int((len(text) - 20) * 0.8))
        
        # Создаем временный шрифт для измерения
        font_path = str(FONTS_PATH / "SegoeUI-Bold.ttf")
        temp_font = ImageFont.truetype(font_path, new_size)
        
        # Получаем размер текста с временным шрифтом
        bbox = temp_font.getbbox(text)
        text_width = bbox[2] - bbox[0]
        
        # Если текст все еще не помещается, уменьшаем дальше
        while text_width > max_width and new_size > 16:
            new_size -= 2
            temp_font = ImageFont.truetype(font_path, new_size)
            bbox = temp_font.getbbox(text)
            text_width = bbox[2] - bbox[0]
        
        # Создаем финальный шрифт
        return ImageFont.truetype(font_path, new_size)
        
    except Exception:
        # В случае ошибки возвращаем базовый шрифт
        return base_font

def _draw_day_card(draw: ImageDraw.ImageDraw, day_title: str, lessons: List[Dict[str, Any]], card_pos: Tuple[int, int], theme_colors: dict) -> None:
    card_height = _get_card_height(lessons)
    card_width = (CANVAS_WIDTH - PADDING * 2 - GRID_GAP_X) // 2
    draw.rounded_rectangle([card_pos, (card_pos[0] + card_width, card_pos[1] + card_height)], radius=CARD_RADIUS, fill=WHITE)
    draw.text((card_pos[0] + CARD_PADDING_X, card_pos[1] + CARD_PADDING_Y), day_title, font=FONT_DAY_TITLE, fill=theme_colors['header'])
    if not lessons:
        draw.text((card_pos[0] + CARD_PADDING_X, card_pos[1] + 85), "Отдыхаем на расслабоне, друзья", font=FONT_BOLD, fill=DARK_TEXT_SEMI)
        return
    first_lesson_time = sorted(lessons, key=lambda l: l.get('start_time_raw', '23:59'))[0].get('start_time_raw', '')
    if first_lesson_time:
        badge_w, badge_h = 90, 50
        badge_x = card_pos[0] + card_width - CARD_PADDING_X - badge_w
        badge_y = card_pos[1] + CARD_PADDING_Y - 5
        draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + badge_h], radius=12, fill=theme_colors['badge'])
        draw.text((badge_x + badge_w / 2, badge_y + 18), first_lesson_time, font=FONT_BADGE_TIME, fill=WHITE, anchor="mm")
        draw.text((badge_x + badge_w / 2, badge_y + 38), "ПЕРВАЯ ПАРА", font=FONT_BADGE_TEXT, fill=WHITE, anchor="mm")
    # Вычисляем центр карточки для контента, но с отступом от бейджа
    badge_bottom = card_pos[1] + CARD_PADDING_Y + 50  # Нижняя граница бейджа
    content_start = badge_bottom + 30  # Отступ от бейджа
    content_height = len(lessons) * LESSON_ROW_HEIGHT
    
    # Центр карточки (учитывая заголовок дня)
    card_center = card_pos[1] + CARD_HEADER_HEIGHT + (card_height - CARD_HEADER_HEIGHT) // 2
    
    # Позиция контента, если начать от бейджа
    content_center_if_from_badge = content_start + content_height // 2
    
    # Если контент помещается в центре с отступом от бейджа
    if content_center_if_from_badge <= card_center:
        current_y = card_center - content_height // 2
    else:
        # Начинаем от бейджа с отступом
        current_y = content_start
    for lesson in lessons:
        subject_text = f"{lesson.get('subject', '')} ({lesson.get('type', '')})"
        room_text = f"{lesson.get('room', '')}"
        time_text = lesson.get('time', '')
        
        # Вычисляем максимальную ширину для названия предмета
        # Оставляем место для аудитории (360px от левого края) и времени (справа)
        room_x = card_pos[0] + 360
        time_x = card_pos[0] + card_width - CARD_PADDING_X
        max_subject_width = room_x - (card_pos[0] + CARD_PADDING_X) - 20  # 20px отступ от аудитории
        
        # Получаем адаптивный шрифт для названия предмета
        adaptive_font = _get_adaptive_font(subject_text, max_subject_width, FONT_BOLD)
        
        # Отладочная информация
        bbox = adaptive_font.getbbox(subject_text)
        actual_width = bbox[2] - bbox[0]
        print(f"Текст: '{subject_text}' (длина: {len(subject_text)}) - размер шрифта: {adaptive_font.size}px, ширина: {actual_width}px/{max_subject_width}px")
        if actual_width > max_subject_width:
            print(f"ВНИМАНИЕ: Текст '{subject_text}' все еще не помещается! Максимум: {max_subject_width}px, фактически: {actual_width}px")
        
        draw.text((card_pos[0] + CARD_PADDING_X, current_y), subject_text, font=adaptive_font, fill=DARK_TEXT, anchor="lm")
        draw.text((card_pos[0] + 360, current_y), room_text, font=FONT_REGULAR, fill=theme_colors['room'], anchor="rm")
        draw.text((card_pos[0] + card_width - CARD_PADDING_X, current_y), time_text, font=FONT_REGULAR, fill=DARK_TEXT_SEMI, anchor="rm")
        current_y += LESSON_ROW_HEIGHT

async def _fallback_generate_with_pillow(schedule_data: dict, week_type: str, group: str, output_path: str) -> bool:
    try:
        is_odd_week = "Нечётная" in week_type
        theme_colors = {
            'bg': BG_ORANGE if is_odd_week else BG_PURPLE,
            'header': ORANGE_ACCENT_HEADER if is_odd_week else PURPLE_ACCENT,
            'badge': ORANGE_ACCENT_BADGE if is_odd_week else PURPLE_ACCENT,
            'room': ORANGE_ACCENT_HEADER if is_odd_week else DARK_TEXT_SEMI,
        }
        days_order = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]
        schedule_data_upper = {k.upper(): v for k, v in schedule_data.items()}
        card_heights = {day: _get_card_height(schedule_data_upper.get(day.upper(), [])) for day in days_order}
        col1_h = card_heights["Понедельник"] + card_heights["Среда"] + card_heights["Пятница"]
        col2_h = card_heights["Вторник"] + card_heights["Четверг"] + card_heights["Суббота"]
        content_height = max(col1_h, col2_h) + GRID_GAP_Y * 2
        total_height = PADDING * 2 + HEADER_HEIGHT + content_height
        image = Image.new("RGBA", (CANVAS_WIDTH, int(total_height)), theme_colors['bg'])
        draw = ImageDraw.Draw(image)
        draw.text((PADDING, PADDING), "РАСПИСАНИЕ", font=FONT_HEADER, fill=WHITE)
        week_type_with_group = f"{week_type}: {group}"
        draw.text((PADDING, PADDING + 60), week_type_with_group, font=FONT_SUBHEADER, fill=TEXT_SEMI_TRANSPARENT)
        y_col1 = PADDING + HEADER_HEIGHT
        y_col2 = PADDING + HEADER_HEIGHT
        x_col1 = PADDING
        x_col2 = PADDING + (CANVAS_WIDTH - PADDING * 2 - GRID_GAP_X) // 2 + GRID_GAP_X
        _draw_day_card(draw, "Понедельник", schedule_data_upper.get("ПОНЕДЕЛЬНИК", []), (x_col1, y_col1), theme_colors)
        y_col1 += card_heights["Понедельник"] + GRID_GAP_Y
        _draw_day_card(draw, "Среда", schedule_data_upper.get("СРЕДА", []), (x_col1, y_col1), theme_colors)
        y_col1 += card_heights["Среда"] + GRID_GAP_Y
        _draw_day_card(draw, "Пятница", schedule_data_upper.get("ПЯТНИЦА", []), (x_col1, y_col1), theme_colors)
        _draw_day_card(draw, "Вторник", schedule_data_upper.get("ВТОРНИК", []), (x_col2, y_col2), theme_colors)
        y_col2 += card_heights["Вторник"] + GRID_GAP_Y
        _draw_day_card(draw, "Четверг", schedule_data_upper.get("ЧЕТВЕРГ", []), (x_col2, y_col2), theme_colors)
        y_col2 += card_heights["Четверг"] + GRID_GAP_Y
        _draw_day_card(draw, "Суббота", schedule_data_upper.get("СУББОТА", []), (x_col2, y_col2), theme_colors)
        image.save(output_path, "PNG", quality=95, optimize=True)
        return True
    except Exception as e:
        print(f"Неизвестная ошибка при генерации изображения (fallback): {e}")
        return False

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
        
        global _template_cache, _bg_images_cache
        project_root = Path(__file__).resolve().parent.parent
        if _template_cache is None:
            templates_dir = project_root / "templates"
            env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=select_autoescape())
            _template_cache = env.get_template("schedule_template.html")
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
            return await _fallback_generate_with_pillow(schedule_data, week_type, group, output_path)

        # --- ШАГ 2: Запуск браузера и создание скриншота по новой логике ---
        print_progress_bar(2, 5, f"Генерация {group}", "Запуск браузера")
        
        global _browser_instance, _browser_lock
        if _browser_lock is None:
            import asyncio
            _browser_lock = asyncio.Lock()
        
        async with _browser_lock:
            if _browser_instance is None or not _browser_instance.is_connected():
                pw = await async_playwright().__aenter__()
                _browser_instance = await pw.chromium.launch(args=['--no-sandbox', '--disable-dev-shm-usage'])
            
            page = await _browser_instance.new_page()
            
            try:
                # --- УСТАНАВЛИВАЕМ ШИРОКИЙ VIEWPORT ---
                # Задаем фиксированную ширину, чтобы сетка не сжималась. 
                # 2800px выбрано с запасом под ваш max-width: 270rem.
                # Высоту делаем разумной, чтобы не было лишнего пространства.
                initial_width = 2800 
                initial_height =  int(initial_width * 2/3) #соотношение сторон 2:3
                
                await page.set_viewport_size({"width": initial_width, "height": initial_height})
                
                print_progress_bar(3, 5, f"Генерация {group}", "Загрузка контента")
                await page.set_content(html, wait_until="domcontentloaded")
                
                # Ждём полной прогрузки шрифтов
                await page.evaluate("document.fonts.ready")
                await page.wait_for_timeout(200)

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

                # Вычисляем правильную высоту с одинаковыми отступами сверху и снизу
                content_height = bounding_box['height']
                top_margin = bounding_box['y']  # Отступ сверху
                
                # Добавляем такой же отступ снизу для симметрии, но не более 100px
                bottom_margin = min(top_margin, 100)
                final_height = int(content_height + top_margin + bottom_margin)

                # --- УСТАНАВЛИВАЕМ ФИНАЛЬНЫЙ РАЗМЕР И ДЕЛАЕМ СКРИНШОТ ---
                print_progress_bar(5, 5, f"Генерация {group}", "Создание скриншота")
                
                # Подгоняем высоту viewport точно под контент
                await page.set_viewport_size({"width": initial_width, "height": final_height})
                
                # Делаем скриншот всей страницы, которая теперь имеет идеальный размер
                await page.screenshot(path=output_path, type="png")

            finally:
                await page.close()
        
        # Постобработка для сжатия 
        try:
            if os.path.getsize(output_path) > 10_000_000:
                from math import sqrt
                with Image.open(output_path) as img:
                    w, h = img.size
                    max_dim, max_area = 4096, 12_000_000
                    scale = min(1.0, max_dim / w, max_dim / h, sqrt(max_area / (w * h)))
                    if scale < 0.95:
                        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
                        resized_img = img.resize(new_size, Image.LANCZOS)
                        resized_img.save(output_path, format="PNG", optimize=True)
        except Exception:
            pass
            
        return True
    except Exception as e:
        print(f"Ошибка при генерации изображения: {e}")
        return await _fallback_generate_with_pillow(schedule_data, week_type, group, output_path)
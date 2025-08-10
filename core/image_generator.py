import os
from pathlib import Path
from typing import List, Dict, Any, Tuple

from PIL import Image, ImageDraw, ImageFont
from jinja2 import Environment, FileSystemLoader, select_autoescape

# Экспортируемая ссылка на async_playwright (для тестов она мокается)
try:
    from playwright.async_api import async_playwright as _async_playwright
except Exception:
    _async_playwright = None
async_playwright = _async_playwright

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
    current_y = card_pos[1] + CARD_HEADER_HEIGHT + 25
    for lesson in lessons:
        subject_text = f"{lesson.get('subject', '')} ({lesson.get('type', '')})"
        room_text = f"{lesson.get('room', '')}*"
        time_text = lesson.get('time', '')
        draw.text((card_pos[0] + CARD_PADDING_X, current_y), subject_text, font=FONT_BOLD, fill=DARK_TEXT, anchor="lm")
        draw.text((card_pos[0] + 360, current_y), room_text, font=FONT_REGULAR, fill=theme_colors['room'], anchor="lm")
        draw.text((card_pos[0] + card_width - CARD_PADDING_X, current_y), time_text, font=FONT_REGULAR, fill=DARK_TEXT_SEMI, anchor="rm")
        current_y += LESSON_ROW_HEIGHT

async def _fallback_generate_with_pillow(schedule_data: dict, week_type: str, output_path: str) -> bool:
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
        draw.text((PADDING, PADDING + 60), week_type, font=FONT_SUBHEADER, fill=TEXT_SEMI_TRANSPARENT)
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
            room_raw = (l.get('room', '') or '').replace('*', '').strip()
            room_lower = room_raw.lower()
            # Считаем такие значения отсутствием кабинета
            if not room_raw or ('кабинет' in room_lower and 'не указан' in room_lower):
                room_fmt = ''
            else:
                room_fmt = f"{room_raw}*"
            items.append({
                'title': title,
                'room': room_fmt,
                'time': l.get('time','')
            })
        prepared.append({'name': name, 'firstStart': first, 'lessons': items})
    return prepared

async def generate_schedule_image(schedule_data: dict, week_type: str, group: str, output_path: str) -> bool:
    """
    HTML→PNG через headless браузер. При сбое — PIL-фолбэк.

    Args:
        schedule_data: словарь с расписанием недели.
        week_type: строка "Нечётная"/"Чётная".
        group: группа (для будущих пометок, сейчас не используется в шаблоне).
        output_path: путь для сохранения PNG.

    Returns:
        bool: успех/неуспех.
    """
    try:
        # 1) Рендер HTML по шаблону
        project_root = Path(__file__).resolve().parent.parent
        templates_dir = project_root / "templates"
        env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=select_autoescape())
        template = env.get_template("schedule_template.html")

        week_slug = 'odd' if 'Неч' in week_type else 'even'
        # Инлайн-фоны в base64, чтобы не зависеть от file:// доступа в браузере
        from base64 import b64encode
        orange_path = project_root / "assets" / "orange_background.png"
        purple_path = project_root / "assets" / "purple_background.png"
        try:
            bg_orange_data = f"data:image/png;base64,{b64encode(orange_path.read_bytes()).decode()}"
        except Exception:
            bg_orange_data = ""
        try:
            bg_purple_data = f"data:image/png;base64,{b64encode(purple_path.read_bytes()).decode()}"
        except Exception:
            bg_purple_data = ""
        html = template.render(
            week_type=week_type,
            week_slug=week_slug,
            schedule_days=_prepare_days(schedule_data),
            bg_image=(bg_orange_data if week_slug == 'odd' else bg_purple_data),
            assets_base=(project_root / 'assets').as_uri(),
        )

        # 2) Ленивая загрузка playwright или использование замоканного объекта из тестов
        global async_playwright
        if async_playwright is None:
            try:
                from playwright.async_api import async_playwright as _imported
                async_playwright = _imported
            except Exception:
                # Нет браузера — уходим в фолбэк
                return await _fallback_generate_with_pillow(schedule_data, week_type, output_path)

        # 3) Скриншот страницы
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context(device_scale_factor=2)
            page = await context.new_page()
            # Отрисуем по реальной ширине канвы; высота с запасом
            await page.set_viewport_size({"width": 2969, "height": 2400})
            await page.set_content(html)
            await page.add_style_tag(content='''
              :root { --scale-multiplier: 1 !important; }
              #scale-canvas { transform: scale(var(--scale-multiplier)); transform-origin: top left; }
            ''')
            canvas = await page.query_selector('#scale-canvas')
            # Снимаем скрин именно элемента целиком (за пределами вьюпорта тоже)
            await canvas.screenshot(path=output_path, type="png")
            # Telegram ограничения: размеры и площадь изображения. При необходимости даунскейлим.
            try:
                from math import sqrt
                img = Image.open(output_path)
                w, h = img.size
                # Ограничения под Telegram. На dpr=2 почти всегда попадаем, но перестрахуемся.
                max_dim = 4096
                max_area = 12_000_000
                scale_w = max_dim / w
                scale_h = max_dim / h
                scale_area = sqrt(max_area / (w * h)) if (w * h) > 0 else 1.0
                scale = min(1.0, scale_w, scale_h, scale_area)
                if scale < 1.0:
                    new_w = max(1, int(w * scale))
                    new_h = max(1, int(h * scale))
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                # На всякий: убираем альфу для совместимости
                if img.mode in ("RGBA", "LA"):
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[-1])
                    img = bg
                else:
                    img = img.convert("RGB")
                img.save(output_path, format="PNG", optimize=True)
            except Exception:
                pass
            await browser.close()
        return True
    except Exception as e:
        print(f"Ошибка при генерации изображения: {e}")
        return False
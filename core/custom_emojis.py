"""Маппинг стандартных Unicode-эмодзи на кастомные Telegram-эмодзи (dashiniemoji, red_timerjeni, RestrictedEmoji)."""

from __future__ import annotations

import re
from typing import Final

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Основные паки:
# - dashiniemoji (https://t.me/addemoji/dashiniemoji) — бордово-золотая палитра
# - red_timerjeni (https://t.me/addemoji/red_timerjeni) — залитые ✅/❌, цифры 1️⃣–🔟, 🔥, 💬, ⬇️, 🙁, 🙏
# - RestrictedEmoji (https://t.me/addemoji/RestrictedEmoji) — анимированные недостающие иконки
EMOJI_PACK_NAME: Final[str] = "dashiniemoji"

EMOJI_TO_CUSTOM_ID: Final[dict[str, str]] = {
    # Статусы и действия
    "✅": "5438501135969170287",  # залитая красная галочка в кружке (red_timerjeni)
    "☑️": "5440450063279072133",  # чекбокс с галочкой (red_timerjeni)
    "☑": "5440450063279072133",
    "✔️": "5386845748983773391",  # галочка в кружке
    "✔": "5386845748983773391",
    "❌": "5438620364261305573",  # залитый красный крестик в кружке (red_timerjeni)
    "❎": "5386369209477382299",  # бордовый крестик
    "⚠️": "5384414277803195040",  # бордовый !
    "⚠": "5384414277803195040",
    "❗️": "5384414277803195040",
    "❗": "5384414277803195040",
    "‼️": "5386616535169121382",  # бордовый !!
    "❓": "5384480720947263584",  # бордовый ?
    "⁉️": "5386556882368345918",  # !?
    # Время суток, погода, приветствия, настроение
    "☀️": "5386683193061555535",  # золотое солнце
    "☀": "5386683193061555535",
    "🌤️": "5386438272551501815",  # бордовое солнце
    "🌤": "5386438272551501815",
    "🌙": "5386845392501488150",  # золотой полумесяц
    "☕️": "5386461147547318294",  # бордовая чашка кофе
    "☕": "5386461147547318294",
    "🎉": "5386667988877326667",  # бордовый салют-искра
    "✨": "5384257666115715029",  # золотая искра
    "🌟": "5386480526439758442",  # бордовая звёздочка
    "⭐️": "5386493411341649722",  # золотая звезда
    "⭐": "5386493411341649722",
    "🚀": "5386331473894719940",  # бордовая молния
    "⚡️": "5384247164920677398",  # золотая молния
    "⚡": "5384247164920677398",
    "🔥": "5442616732545983624",  # красный огонь (red_timerjeni)
    "💡": "5384471770235418433",  # золотая лампочка
    "👋": "5386356294510722927",  # улыбка
    "😉": "5384102368688226729",  # смайл
    "🙂": "5384102368688226729",
    "😎": "5384102368688226729",
    "🙁": "5438597145668104967",  # грустный смайл (red_timerjeni)
    "🙏": "5443119303849158787",  # благодарность (red_timerjeni)
    "🌸": "5386496447883524756",  # бордовый цветок
    "🌧️": "5386307112840217299",  # капля
    "🌧": "5386307112840217299",
    "💧": "5386307112840217299",
    # Расписание, учёба, навигация и меню
    "📍": "5386429751336383258",  # бордовый пин (аудитории)
    "📌": "5386429751336383258",
    "🗓️": "5386376601116098709",  # бордовая закладка-календарь
    "🗓": "5386376601116098709",
    "📅": "5386376601116098709",
    "📆": "5386376601116098709",
    "🎓": "5386786152017575672",  # бордовая звезда-значок студента
    "🧑‍🏫": "5386808752135484333",  # контурная закладка преподавателя
    "👨‍🏫": "5386808752135484333",
    "🔍": "5384138193010441280",  # бордовый глазик (поиск)
    "🔔": "5384494713950716186",  # бордовый динамик (уведомления)
    "📢": "5384345893333907609",  # динамик (новости)
    "📣": "5384345893333907609",
    "✉️": "5386622896015685646",  # бордовый конвертик
    "✉": "5386622896015685646",
    "📬": "5386622896015685646",
    "💌": "5386622896015685646",
    "💬": "5438158711111563496",  # красное облачко с точками (red_timerjeni)
    "📝": "5386585413836095148",  # бордовое облачко чата
    "✏️": "5386585413836095148",
    "✏": "5386585413836095148",
    "📎": "5386408817665785919",  # бордовая скрепка
    "🖼️": "5386523055205924882",  # жёлтая камера (изображения)
    "🖼": "5386523055205924882",
    "📷": "5386523055205924882",
    "📸": "5386540763356082820",
    "🏠": "5386612553734439298",  # бордовое сердечко с лучами («Моя»)
    "⚙️": "5386322660621827578",  # бордовая искра (настройки / ещё)
    "⚙": "5386322660621827578",
    "🔄": "5384615015984675600",  # стрелка-поворот (сменить)
    "➡️": "5386740255997050051",  # бордовая стрелка вправо
    "➡": "5386740255997050051",
    "⬇️": "5442625949545800135",  # красная стрелка вниз (red_timerjeni)
    "⬇": "5442625949545800135",
    "👇": "5442625949545800135",
    "🔴": "5386499158007888876",  # бордовая точка
    "🟡": "5386677381970802988",  # жёлтая точка
    "❤️": "5384267892432845490",  # бордовое сердце
    "❤": "5384267892432845490",
    "👑": "5386610006818832694",  # золотая звезда
    "🏆": "5386610006818832694",
    "🎯": "5386653656571461004",  # бордовый ромб
    "📖": "5386843073219147248",  # золотая закладка
    "📚": "5386843073219147248",
    "📋": "5386843073219147248",
    "📄": "5386843073219147248",
    "📜": "5386843073219147248",
    "🚪": "5384291849760422379",  # золотая контурная закладка (аудитория)
    "🎨": "5384209296194027301",  # жёлтый цветок (темы оформления)
    # Красные цифры 1-10 (red_timerjeni)
    "1️⃣": "5442663320056243760",
    "1⃣": "5442663320056243760",
    "2️⃣": "5440856856811546732",
    "2⃣": "5440856856811546732",
    "3️⃣": "5440768865816554344",
    "3⃣": "5440768865816554344",
    "4️⃣": "5443137574640035334",
    "4⃣": "5443137574640035334",
    "5️⃣": "5440369558412077267",
    "5⃣": "5440369558412077267",
    "6️⃣": "5442852929977459454",
    "6⃣": "5442852929977459454",
    "7️⃣": "5440704437012146106",
    "7⃣": "5440704437012146106",
    "8️⃣": "5443136780071084818",
    "8⃣": "5443136780071084818",
    "9️⃣": "5443054952354162103",
    "9⃣": "5443054952354162103",
    "🔟": "5442643245379100861",
    # Недостающие иконки из анимированного пака RestrictedEmoji
    "👤": "5373012449597335010",
    "👥": "5372926953978341366",
    "🌍": "5399898266265475100",
    "⏰": "5413704112220949842",
    "🕒": "5413704112220949842",
    "🕘": "5413704112220949842",
    "🔐": "5472308992514464048",
    "🔒": "5472308992514464048",
    "📊": "5431577498364158238",
    "📈": "5373001317042101552",
    "📤": "5433614747381538714",
    "📥": "5433811242135331842",
    "📂": "5431721976769027887",
    "📲": "5406809207947142040",
    "🧪": "5411512278740640309",
    "🤖": "5372981976804366741",
    "🤝": "5357080225463149588",
    "➕": "5226945370684140473",
    "👆": "5469718869536940860",
    "💪": "5471883477219549006",
    "❄️": "5431895003821513760",
    "❄": "5431895003821513760",
    "🌴": "5449372007432985754",
    "🪻": "5375282786489880721",
    "🍁": "5281026503658728615",
    "🍦": "5372799217355987430",
}

# Сортируем ключи по убыванию длины, чтобы длинные последовательности (с \uFE0F / ZWJ) матчились раньше.
_SORTED_EMOJIS: Final[list[str]] = sorted(EMOJI_TO_CUSTOM_ID.keys(), key=len, reverse=True)
_EMOJI_PATTERN: Final[re.Pattern[str]] = re.compile("|".join(re.escape(e) for e in _SORTED_EMOJIS))
_LEADING_EMOJI_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\s*(" + "|".join(re.escape(e) for e in _SORTED_EMOJIS) + r")")

# Пропускаем уже обёрнутые <tg-emoji>, блоки <code>/<pre> и любые HTML-теги целиком.
_HTML_PROTECTED_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(<tg-emoji\b[^>]*>.*?</tg-emoji>|<pre\b[^>]*>.*?</pre>|<code\b[^>]*>.*?</code>|<[^>]+>)",
    re.DOTALL | re.IGNORECASE,
)


def _wrap_emoji_match(match: re.Match[str]) -> str:
    emoji = match.group(0)
    custom_id = EMOJI_TO_CUSTOM_ID[emoji]
    return f'<tg-emoji emoji-id="{custom_id}">{emoji}</tg-emoji>'


def replace_emojis_html(text: str) -> str:
    """Заменяет известные Unicode-эмодзи в HTML-тексте на <tg-emoji>, не затрагивая теги и code/pre."""
    if not text:
        return text

    parts = _HTML_PROTECTED_PATTERN.split(text)
    changed = False
    for idx in range(0, len(parts), 2):
        segment = parts[idx]
        if not segment:
            continue
        replaced = _EMOJI_PATTERN.sub(_wrap_emoji_match, segment)
        if replaced != segment:
            parts[idx] = replaced
            changed = True

    return "".join(parts) if changed else text


_PRIMARY_WIDGET_IDS: Final[frozenset[str]] = frozenset(
    {
        "today",
        "week_as_image",
        "make_my_group",
        "export_ics",
        "settings",
        "theme_btn",
        "to_morning_time",
        "to_evening_time",
        "to_time_settings",
        "quality_gate_check",
        "check_subscription",
        "skip_tutorial",
        "send_original_file",
        "send_original_file_week",
    }
)

_SETTINGS_TOGGLE_WIDGET_IDS: Final[frozenset[str]] = frozenset(
    {
        "morning_summary",
        "evening_notify",
        "lesson_reminders",
        "schedule_diff_notify",
    }
)

_SUCCESS_WIDGET_IDS: Final[frozenset[str]] = frozenset(
    {
        "confirm_broadcast_send",
        "confirm_segment_send",
    }
)

_DANGER_WIDGET_IDS: Final[frozenset[str]] = frozenset(
    {
        "cancel_broadcast_send",
        "feedback_cancel",
        "back_from_backup_status",
    }
)


def resolve_button_style(button: InlineKeyboardButton) -> str | None:
    """Подбирает Bot API 9.4 цветной стиль (primary/success/danger) для ключевых кнопок."""
    if button.style:
        return None

    cb = button.callback_data or ""
    widget_id = cb.rsplit("\x1d", 1)[-1] if cb else ""
    text = button.text.lstrip()

    if widget_id in _SETTINGS_TOGGLE_WIDGET_IDS:
        if text.startswith(("Включить", "Enable", "开启")):
            return "success"
        if text.startswith(("Отключить", "Выключить", "Disable", "关闭")):
            return "danger"
        return None

    if widget_id in _PRIMARY_WIDGET_IDS:
        return "primary"
    if widget_id in _SUCCESS_WIDGET_IDS:
        return "success"
    if widget_id in _DANGER_WIDGET_IDS:
        return "danger"

    if button.url and text.startswith("🔔"):
        return "primary"
    if text.startswith("❌"):
        return "danger"
    return None


def transform_inline_button(button: InlineKeyboardButton) -> InlineKeyboardButton:
    """Переносит ведущий эмодзи из текста inline-кнопки в icon_custom_emoji_id и задаёт style."""
    updates: dict[str, str] = {}

    style = resolve_button_style(button)
    if style:
        updates["style"] = style

    if not button.icon_custom_emoji_id:
        match = _LEADING_EMOJI_PATTERN.match(button.text)
        if match:
            rest = button.text[match.end() :].lstrip()
            if rest:
                # Telegram не позволяет пустой text на кнопке; не дублируем чисто символьные кнопки.
                updates["text"] = rest
                updates["icon_custom_emoji_id"] = EMOJI_TO_CUSTOM_ID[match.group(1)]

    if not updates:
        return button
    return button.model_copy(update=updates)


def transform_inline_keyboard(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Применяет кастомные иконки ко всем подходящим кнопкам InlineKeyboardMarkup."""
    changed = False
    new_rows: list[list[InlineKeyboardButton]] = []
    for row in markup.inline_keyboard:
        new_row: list[InlineKeyboardButton] = []
        for btn in row:
            updated = transform_inline_button(btn)
            if updated is not btn:
                changed = True
            new_row.append(updated)
        new_rows.append(new_row)

    if not changed:
        return markup
    return markup.model_copy(update={"inline_keyboard": new_rows})

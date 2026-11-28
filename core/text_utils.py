"""
Утилиты для обработки текста, транслитерации и нормализации ввода.
"""

import re

# Таблица транслитерации для групп (латиница -> кириллица)
# Учитываем визуальное сходство или фонетическое для часто используемых букв
TRANSLIT_TABLE = {
    "A": "А",
    "B": "Б",
    "C": "Ц",
    "D": "Д",
    "E": "Е",
    "F": "Ф",
    "G": "Г",
    "H": "Х",
    "I": "И",
    "J": "Й",
    "K": "К",
    "L": "Л",
    "M": "М",
    "N": "Н",
    "O": "О",
    "P": "П",
    "Q": "К",
    "R": "Р",
    "S": "С",
    "T": "Т",
    "U": "У",
    "V": "В",
    "W": "В",
    "X": "Х",
    "Y": "У",
    "Z": "З",
}

# Более полная таблица для фамилий (ISO 9 + фонетика)
NAME_TRANSLIT_TABLE = {
    "shch": "щ",
    "sch": "щ",
    "yo": "ё",
    "zh": "ж",
    "kh": "х",
    "ts": "ц",
    "ch": "ч",
    "sh": "ш",
    "yu": "ю",
    "ya": "я",
    "a": "а",
    "b": "б",
    "v": "в",
    "g": "г",
    "d": "д",
    "e": "е",
    "z": "з",
    "i": "и",
    "j": "й",
    "k": "к",
    "l": "л",
    "m": "м",
    "n": "н",
    "o": "о",
    "p": "п",
    "r": "р",
    "s": "с",
    "t": "т",
    "u": "у",
    "f": "ф",
    "h": "х",
    "c": "ц",
    "q": "к",
    "x": "кс",
    "w": "в",
    "y": "ы",
    "'": "ь",
    '"': "ъ",
}


_GROUP_TRANSLIT_MAP = str.maketrans(TRANSLIT_TABLE)
_GROUP_ALLOWED_RE = re.compile(r"[^А-ЯЁ0-9\-\.]")
_NAME_MULTI_RE = re.compile(
    "|".join(re.escape(k) for k in sorted((k for k in NAME_TRANSLIT_TABLE if len(k) > 1), key=len, reverse=True))
)
_NAME_SINGLE_MAP = str.maketrans({k: v for k, v in NAME_TRANSLIT_TABLE.items() if len(k) == 1})


def normalize_group_name(group_name: str) -> str:
    """
    Нормализует название группы:
    - Приводит к верхнему регистру
    - Заменяет латинские буквы на кириллицу (если похожи)
    - Убирает лишние символы
    """
    if not group_name:
        return ""

    normalized = group_name.upper().strip().translate(_GROUP_TRANSLIT_MAP)
    return _GROUP_ALLOWED_RE.sub("", normalized)


def transliterate_failed_search(text: str) -> str:
    """
    Пытается транслитерировать поисковый запрос (например, фамилию) с латиницы на кириллицу.
    Ivanov -> Иванов
    """
    if not text:
        return ""
    lowered = _NAME_MULTI_RE.sub(lambda m: NAME_TRANSLIT_TABLE[m.group(0)], text.lower())
    return lowered.translate(_NAME_SINGLE_MAP).title()  # Возвращаем с заглавной буквы

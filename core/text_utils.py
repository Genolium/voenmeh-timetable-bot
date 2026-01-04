"""
Утилиты для обработки текста, транслитерации и нормализации ввода.
"""
import re

# Таблица транслитерации для групп (латиница -> кириллица)
# Учитываем визуальное сходство или фонетическое для часто используемых букв
TRANSLIT_TABLE = {
    'A': 'А', 'B': 'Б', 'C': 'Ц', 'D': 'Д', 'E': 'Е', 'F': 'Ф', 'G': 'Г',
    'H': 'Х', 'I': 'И', 'J': 'Й', 'K': 'К', 'L': 'Л', 'M': 'М', 'N': 'Н',
    'O': 'О', 'P': 'П', 'Q': 'К', 'R': 'Р', 'S': 'С', 'T': 'Т', 'U': 'У',
    'V': 'В', 'W': 'В', 'X': 'Х', 'Y': 'У', 'Z': 'З'
}

# Более полная таблица для фамилий (ISO 9 + фонетика)
NAME_TRANSLIT_TABLE = {
    'shch': 'щ', 'sch': 'щ', 'yo': 'ё', 'zh': 'ж', 'kh': 'х', 'ts': 'ц',
    'ch': 'ч', 'sh': 'ш', 'yu': 'ю', 'ya': 'я',
    'a': 'а', 'b': 'б', 'v': 'в', 'g': 'г', 'd': 'д', 'e': 'е', 'z': 'з',
    'i': 'и', 'j': 'й', 'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н', 'o': 'о',
    'p': 'п', 'r': 'р', 's': 'с', 't': 'т', 'u': 'у', 'f': 'ф',
    'w': 'в',
    'y': 'ы', "'": 'ь', '"': 'ъ'
}

def normalize_group_name(group_name: str) -> str:
    """
    Нормализует название группы:
    - Приводит к верхнему регистру
    - Заменяет латинские буквы на кириллицу (если похожи)
    - Убирает лишние символы
    """
    if not group_name:
        return ""
        
    s = group_name.upper().strip()
    
    # Замена латиницы на кириллицу
    chars = list(s)
    for i, char in enumerate(chars):
        if char in TRANSLIT_TABLE:
            chars[i] = TRANSLIT_TABLE[char]
            
    normalized = "".join(chars)
    
    # Оставляем только допустимые символы (буквы и цифры, дефис)
    normalized = re.sub(r"[^А-Я0-9\-\.]", "", normalized)
    
    return normalized

def transliterate_failed_search(text: str) -> str:
    """
    Пытается транслитерировать поисковый запрос (например, фамилию) с латиницы на кириллицу.
    Ivanov -> Иванов
    """
    text = text.lower()
    
    # Сначала заменяем многобуквенные сочетания
    for eng, rus in NAME_TRANSLIT_TABLE.items():
        if len(eng) > 1:
            text = text.replace(eng, rus)
            
    # Затем одиночные буквы
    chars = list(text)
    for i, char in enumerate(chars):
        if char in NAME_TRANSLIT_TABLE:
            chars[i] = NAME_TRANSLIT_TABLE[char]
            
    return "".join(chars).title()  # Возвращаем с заглавной буквы

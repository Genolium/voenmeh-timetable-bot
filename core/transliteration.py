# Transliteration utilities for Cyrillic to Latin conversion
# Uses cyrtranslit library for accurate transliteration

from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Try to import cyrtranslit, fallback to basic transliteration if not available
try:
    from cyrtranslit import to_latin
    CYRTRANSLIT_AVAILABLE = True
except ImportError:
    CYRTRANSLIT_AVAILABLE = False
    logger.warning("cyrtranslit not installed, using fallback transliteration")

# Fallback transliteration table (Russian to Latin)
TRANSLIT_TABLE = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'Yo',
    'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M',
    'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
    'Ф': 'F', 'Х': 'Kh', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Shch',
    'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'Yu', 'Я': 'Ya',
}


def _fallback_translit(text: str) -> str:
    """Fallback transliteration using translation table."""
    result = []
    for char in text:
        result.append(TRANSLIT_TABLE.get(char, char))
    return ''.join(result)


def transliterate(text: Optional[str], lang: str = "en") -> Optional[str]:
    """
    Transliterate Cyrillic text to Latin script for non-Russian languages.
    
    Args:
        text: Text to transliterate (can be None)
        lang: Target language code (if 'ru', returns original text)
        
    Returns:
        Transliterated text for non-Russian languages, original for Russian
    """
    if text is None:
        return None
        
    # Don't transliterate for Russian
    if lang == "ru":
        return text
    
    # Check if text contains any Cyrillic characters
    has_cyrillic = any('\u0400' <= char <= '\u04FF' for char in text)
    if not has_cyrillic:
        return text
    
    try:
        if CYRTRANSLIT_AVAILABLE:
            # cyrtranslit uses 'ru' as source language code
            return to_latin(text, 'ru')
        else:
            return _fallback_translit(text)
    except Exception as e:
        logger.warning(f"Transliteration failed: {e}")
        return _fallback_translit(text)


def transliterate_schedule_data(schedule_data: dict, lang: str = "en") -> dict:
    """
    Transliterate all text fields in schedule data for non-Russian languages.
    
    Args:
        schedule_data: Schedule data dictionary with lessons
        lang: Target language code
        
    Returns:
        Schedule data with transliterated text fields
    """
    if lang == "ru":
        return schedule_data
    
    result = {}
    for day_name, lessons in schedule_data.items():
        # Transliterate day name
        new_day_name = transliterate(day_name, lang)
        
        # Transliterate lessons
        new_lessons = []
        if isinstance(lessons, list):
            for lesson in lessons:
                if isinstance(lesson, dict):
                    new_lesson = lesson.copy()
                    # Transliterate common lesson fields
                    for field in ['subject', 'title', 'teacher', 'room', 'type', 'name']:
                        if field in new_lesson and isinstance(new_lesson[field], str):
                            new_lesson[field] = transliterate(new_lesson[field], lang)
                    new_lessons.append(new_lesson)
                else:
                    new_lessons.append(lesson)
        else:
            new_lessons = lessons
            
        result[new_day_name] = new_lessons
    
    return result


# Day names transliteration mapping for schedule images
DAY_NAMES_TRANSLIT = {
    'Понедельник': 'Monday',
    'Вторник': 'Tuesday',
    'Среда': 'Wednesday',
    'Четверг': 'Thursday',
    'Пятница': 'Friday',
    'Суббота': 'Saturday',
    'Воскресенье': 'Sunday',
}

# Chinese day names
DAY_NAMES_ZH = {
    'Понедельник': '星期一',
    'Вторник': '星期二',
    'Среда': '星期三',
    'Четверг': '星期四',
    'Пятница': '星期五',
    'Суббота': '星期六',
    'Воскресенье': '星期日',
}


def get_day_name(russian_day: str, lang: str = "ru") -> str:
    """
    Get day name in the target language.
    
    Args:
        russian_day: Day name in Russian
        lang: Target language code
        
    Returns:
        Day name in target language
    """
    if lang == "ru":
        return russian_day
    elif lang == "zh":
        return DAY_NAMES_ZH.get(russian_day, transliterate(russian_day, lang))
    else:
        return DAY_NAMES_TRANSLIT.get(russian_day, transliterate(russian_day, lang))

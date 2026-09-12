"""
Централизованный реестр тем расписания (Single Source of Truth).
Все доступные темы, их локализационные ключи и свойства определяются здесь.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True, slots=True)
class ThemeMeta:
    id: str
    name_key: str
    desc_key: str
    is_procedural: bool = False
    bg_image_key: Optional[str] = None  # Для растровых тем: white, dark, coffee, official


DEFAULT_THEME: str = "standard"

THEMES: tuple[ThemeMeta, ...] = (
    ThemeMeta(
        id="standard",
        name_key="theme_standard_name",
        desc_key="theme_standard_desc",
        is_procedural=False,
    ),
    ThemeMeta(
        id="light",
        name_key="theme_light_name",
        desc_key="theme_light_desc",
        is_procedural=False,
        bg_image_key="white",
    ),
    ThemeMeta(
        id="dark",
        name_key="theme_dark_name",
        desc_key="theme_dark_desc",
        is_procedural=False,
        bg_image_key="dark",
    ),
    ThemeMeta(
        id="classic",
        name_key="theme_classic_name",
        desc_key="theme_classic_desc",
        is_procedural=False,
        bg_image_key="official",
    ),
    ThemeMeta(
        id="coffee",
        name_key="theme_coffee_name",
        desc_key="theme_coffee_desc",
        is_procedural=False,
        bg_image_key="coffee",
    ),
    ThemeMeta(
        id="blueprint",
        name_key="theme_blueprint_name",
        desc_key="theme_blueprint_desc",
        is_procedural=True,
    ),
    ThemeMeta(
        id="space",
        name_key="theme_space_name",
        desc_key="theme_space_desc",
        is_procedural=True,
    ),
    ThemeMeta(
        id="nord",
        name_key="theme_nord_name",
        desc_key="theme_nord_desc",
        is_procedural=True,
    ),
    ThemeMeta(
        id="cyberpunk",
        name_key="theme_cyberpunk_name",
        desc_key="theme_cyberpunk_desc",
        is_procedural=True,
    ),
    ThemeMeta(
        id="matrix",
        name_key="theme_matrix_name",
        desc_key="theme_matrix_desc",
        is_procedural=True,
    ),
    ThemeMeta(
        id="matcha",
        name_key="theme_matcha_name",
        desc_key="theme_matcha_desc",
        is_procedural=True,
    ),
    ThemeMeta(
        id="sunset",
        name_key="theme_sunset_name",
        desc_key="theme_sunset_desc",
        is_procedural=True,
    ),
    ThemeMeta(
        id="lavender",
        name_key="theme_lavender_name",
        desc_key="theme_lavender_desc",
        is_procedural=True,
    ),
    ThemeMeta(
        id="oled",
        name_key="theme_oled_name",
        desc_key="theme_oled_desc",
        is_procedural=True,
    ),
    ThemeMeta(
        id="paper",
        name_key="theme_paper_name",
        desc_key="theme_paper_desc",
        is_procedural=True,
    ),
    ThemeMeta(
        id="rain",
        name_key="theme_rain_name",
        desc_key="theme_rain_desc",
        is_procedural=True,
    ),
)

THEME_BY_ID: Dict[str, ThemeMeta] = {t.id: t for t in THEMES}


def is_valid_theme(theme_id: Optional[str]) -> bool:
    """Проверяет, существует ли тема с таким идентификатором."""
    if not theme_id or not isinstance(theme_id, str):
        return False
    return theme_id.lower() in THEME_BY_ID


def get_theme_meta(theme_id: Optional[str]) -> ThemeMeta:
    """Возвращает метаданные темы по ID (с fallback на DEFAULT_THEME)."""
    if theme_id and isinstance(theme_id, str):
        normalized = theme_id.lower()
        if normalized in THEME_BY_ID:
            return THEME_BY_ID[normalized]
    return THEME_BY_ID[DEFAULT_THEME]


def get_all_theme_ids() -> List[str]:
    """Возвращает список всех доступных ID тем."""
    return [t.id for t in THEMES]

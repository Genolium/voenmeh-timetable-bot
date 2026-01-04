"""
Модуль интернационализации (i18n).

Отвечает за загрузку файлов переводов и предоставление локализованных текстов.
"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class I18n:
    def __init__(self, locales_dir: str = "locales", default_lang: str = "ru"):
        self.locales_dir = Path(locales_dir)
        self.default_lang = default_lang
        self.translations: Dict[str, Dict[str, Any]] = {}
        self.available_locales = ["ru", "en", "zh"]
        
        self.reload()

    def reload(self):
        """Загружает (или перезагружает) файлы переводов."""
        if not self.locales_dir.exists():
            logger.warning(f"Locales directory not found: {self.locales_dir}")
            # Создаем директорию, если её нет
            os.makedirs(self.locales_dir, exist_ok=True)
            return

        for lang in self.available_locales:
            file_path = self.locales_dir / f"{lang}.json"
            if file_path.exists():
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        self.translations[lang] = json.load(f)
                    logger.info(f"Loaded locale: {lang}")
                except Exception as e:
                    logger.error(f"Failed to load locale {lang}: {e}")
            else:
                logger.warning(f"Locale file missing: {file_path}")
                self.translations[lang] = {}

    def get(self, key: str, lang: str = "ru", **kwargs) -> str:
        """
        Возвращает перевод по ключу.
        Поддерживает форматирование строк через kwargs.
        Если ключ не найден, возвращает сам ключ (или дефолт).
        """
        # Если язык не поддерживается, используем дефолтный
        if lang not in self.translations:
            lang = self.default_lang

        # Получаем перевод
        text = self.translations.get(lang, {}).get(key)

        # Фолбэк на дефолтный язык
        if text is None and lang != self.default_lang:
            text = self.translations.get(self.default_lang, {}).get(key)
        
        # Если все еще None, возвращаем ключ
        if text is None:
            return key

        # Форматирование
        if kwargs and isinstance(text, str):
            try:
                return text.format(**kwargs)
            except Exception as e:
                logger.warning(f"Formatting error for key '{key}' in '{lang}': {e}")
                return text

        return text

# Глобальный экземпляр
i18n = I18n()

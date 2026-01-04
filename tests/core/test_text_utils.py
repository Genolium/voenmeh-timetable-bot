
import pytest
from core.text_utils import normalize_group_name, transliterate_failed_search

class TestTextUtils:
    @pytest.mark.parametrize(
        "input_str, expected",
        [
            ("A1", "А1"),
            ("b-2", "Б-2"),
            ("  c3  ", "Ц3"),
            ("Group 1", "ГРОУП1"), # Assuming simple translit table
            ("O735B", "О735Б"), # Common case
            ("P123", "П123"),
            ("H", "Х"),
            ("X", "Х"),
            ("", ""),
            (None, ""),
            ("!@#$", ""), # symbols stripped
            ("АБВ", "АБВ"), # Cyrillic untouched
        ]
    )
    def test_normalize_group_name(self, input_str, expected):
        assert normalize_group_name(input_str) == expected

    @pytest.mark.parametrize(
        "input_str, expected",
        [
            ("Ivanov", "Иванов"),
            ("schuka", "Щука"),
            ("shchuka", "Щука"),
            ("yozh", "Ёж"),
            ("tsaplya", "Цапля"),
            ("unknown", "Ункновн"),
            ("123", "123"), # digits kept? logic says only translit letters
        ]
    )
    def test_transliterate_failed_search(self, input_str, expected):
        # Note: transliterate_failed_search returns title-cased string
        assert transliterate_failed_search(input_str) == expected

from datetime import datetime

from bot import text_formatters


def test_evening_intro_handles_empty_day_context(monkeypatch):
    class FakeI18n:
        def get(self, key, lang="ru", **kwargs):
            if key == "day_context":
                return {"0": []}
            if key == "evening_greetings":
                return ["hi"]
            return ""

    monkeypatch.setattr(text_formatters, "i18n", FakeI18n())
    result = text_formatters.generate_evening_intro(None, datetime(2026, 10, 19))
    assert "hi" in result

from core.image_generator import resolve_week_slug


def test_resolve_week_slug_prefers_explicit_key():
    assert resolve_week_slug("Odd Week", "odd") == "odd"
    assert resolve_week_slug("单周", "odd") == "odd"
    assert resolve_week_slug("Чётная неделя", "even") == "even"


def test_resolve_week_slug_falls_back_to_russian_text():
    assert resolve_week_slug("Нечётная неделя", None) == "odd"
    assert resolve_week_slug("Чётная неделя", None) == "even"

from types import SimpleNamespace

from bot.middlewares.event_utils import (
    extract_callback_data,
    extract_from_user,
    extract_message,
    extract_text,
)


def _update(**kwargs):
    return SimpleNamespace(**kwargs)


def test_extract_from_user_message():
    user = SimpleNamespace(id=1, username="u")
    event = _update(message=SimpleNamespace(from_user=user, text="hi"))
    assert extract_from_user(event) is user
    assert extract_text(event) == "hi"
    assert extract_message(event) is event.message


def test_extract_from_user_callback():
    user = SimpleNamespace(id=2, username="c")
    message = SimpleNamespace(from_user=user, text=None)
    cb = SimpleNamespace(from_user=user, data="btn", message=message)
    event = _update(callback_query=cb)
    assert extract_from_user(event) is user
    assert extract_callback_data(event) == "btn"
    assert extract_message(event) is message


def test_extract_from_user_inline():
    user = SimpleNamespace(id=3, username="i")
    event = _update(inline_query=SimpleNamespace(from_user=user, query="q"))
    assert extract_from_user(event) is user
    assert extract_text(event) == "q"


def test_extract_from_user_plain_message_object():
    user = SimpleNamespace(id=4, username="p")
    event = SimpleNamespace(from_user=user, text="plain")
    assert extract_from_user(event) is user
    assert extract_text(event) == "plain"

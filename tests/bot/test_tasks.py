import importlib
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock


def import_tasks(monkeypatch):
    # Подготовим переменные окружения, чтобы импорт не упал
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("REDIS_PASSWORD", "x")
    monkeypatch.setenv("BOT_TOKEN", "123:ABC")

    if 'bot.tasks' in sys.modules:
        del sys.modules['bot.tasks']
    import bot.tasks as tasks
    return tasks


@pytest.mark.asyncio
async def test_send_message_helper_success(monkeypatch):
    tasks = import_tasks(monkeypatch)
    monkeypatch.setattr(tasks, 'BOT_INSTANCE', MagicMock())
    send_mock = AsyncMock(return_value=None)
    tasks.BOT_INSTANCE.send_message = send_mock

    await tasks._send_message(1, "hi")
    send_mock.assert_awaited_once_with(1, "hi", disable_web_page_preview=True)


@pytest.mark.asyncio
async def test_send_message_helper_exception(monkeypatch):
    tasks = import_tasks(monkeypatch)
    monkeypatch.setattr(tasks, 'BOT_INSTANCE', MagicMock())
    send_mock = AsyncMock(side_effect=Exception("boom"))
    tasks.BOT_INSTANCE.send_message = send_mock

    with pytest.raises(Exception):
        await tasks._send_message(1, "hi")


@pytest.mark.asyncio
async def test_copy_message_helper_success(monkeypatch):
    tasks = import_tasks(monkeypatch)
    monkeypatch.setattr(tasks, 'BOT_INSTANCE', MagicMock())
    copy_mock = AsyncMock(return_value=None)
    tasks.BOT_INSTANCE.copy_message = copy_mock

    await tasks._copy_message(1, 2, 3)
    copy_mock.assert_awaited_once_with(chat_id=1, from_chat_id=2, message_id=3)


@pytest.mark.asyncio
async def test_copy_message_helper_exception(monkeypatch):
    tasks = import_tasks(monkeypatch)
    monkeypatch.setattr(tasks, 'BOT_INSTANCE', MagicMock())
    copy_mock = AsyncMock(side_effect=Exception("boom"))
    tasks.BOT_INSTANCE.copy_message = copy_mock

    with pytest.raises(Exception):
        await tasks._copy_message(1, 2, 3)


def test_send_message_actor_calls_loop(monkeypatch):
    tasks = import_tasks(monkeypatch)
    # Перехватим вызов event loop
    loop_mock = MagicMock()
    monkeypatch.setattr(tasks, 'LOOP', loop_mock)
    # не запускаем реальный корутин — подменим на заглушку
    async def noop(*_args, **_kwargs):
        return None
    monkeypatch.setattr(tasks, '_send_message', noop)

    tasks.send_message_task.fn(42, "hello")
    assert loop_mock.run_until_complete.called


def test_copy_message_actor_calls_loop(monkeypatch):
    tasks = import_tasks(monkeypatch)
    loop_mock = MagicMock()
    monkeypatch.setattr(tasks, 'LOOP', loop_mock)

    async def noop(*_args, **_kwargs):
        return None
    monkeypatch.setattr(tasks, '_copy_message', noop)

    tasks.copy_message_task.fn(1, 2, 3)
    assert loop_mock.run_until_complete.called


def test_send_lesson_reminder_actor_sends(monkeypatch):
    tasks = import_tasks(monkeypatch)
    # Заглушим генератор текста и отправку
    monkeypatch.setattr(tasks, 'generate_reminder_text', lambda *a, **k: "text")
    sender = MagicMock()
    monkeypatch.setattr(tasks, 'send_message_task', sender)

    tasks.send_lesson_reminder_task.fn(7, {"subject": "X", "type": "L", "time": "10:00-11:30"}, "first", None, 15)
    sender.send.assert_called_once_with(7, "text")


def test_send_lesson_reminder_actor_skips_when_no_text(monkeypatch):
    tasks = import_tasks(monkeypatch)
    monkeypatch.setattr(tasks, 'generate_reminder_text', lambda *a, **k: None)
    sender = MagicMock()
    monkeypatch.setattr(tasks, 'send_message_task', sender)

    tasks.send_lesson_reminder_task.fn(7, {}, "first", None, 15)
    sender.send.assert_not_called()



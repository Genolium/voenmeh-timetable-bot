from unittest.mock import MagicMock

import pytest

import bot.scheduler as scheduler


def test_setup_scheduler_uses_manager_ref(monkeypatch):
    """Задачи планировщика должны получать ссылку на менеджер, а не сам объект."""
    mock_scheduler_cls = MagicMock()
    monkeypatch.setattr(scheduler, "AsyncIOScheduler", mock_scheduler_cls)

    manager = MagicMock(name="manager")
    user_data_manager = MagicMock(name="user_data_manager")
    redis_client = MagicMock(name="redis_client")
    bot = MagicMock(name="bot")

    scheduler.setup_scheduler(bot, manager, user_data_manager, redis_client)

    add_job = mock_scheduler_cls.return_value.add_job
    assert add_job.called
    refs = []
    for call in add_job.call_args_list:
        args = call.kwargs.get("args") or []
        refs.extend(a for a in args if isinstance(a, scheduler.TimetableManagerRef))
    assert len(refs) >= 4
    for ref in refs:
        assert ref.get() is manager


def test_manager_ref_can_be_updated():
    """Ref должен возвращать актуальный менеджер после обновления."""
    first = MagicMock(name="first")
    second = MagicMock(name="second")
    ref = scheduler.TimetableManagerRef(first)
    assert ref.get() is first
    ref.set(second)
    assert ref.get() is second

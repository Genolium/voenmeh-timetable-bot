"""Extra coverage for bot/scheduler.py.

All external I/O (Dramatiq, Redis, weather, DB, parser, alerts) is mocked here.
The tests target the remaining error/edge branches of the scheduler jobs.
"""

import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.scheduler as sched
from core.config import MOSCOW_TZ


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def make_redis():
    redis = AsyncMock()
    redis.get.return_value = b""
    redis.set.return_value = True
    redis.incr.return_value = 2
    lock = AsyncMock()
    lock.__aenter__ = AsyncMock(return_value=None)
    lock.__aexit__ = AsyncMock(return_value=None)
    redis.lock = MagicMock(return_value=lock)
    return redis


def make_udm():
    return AsyncMock()


def make_schedule(lessons, **extra):
    info = {
        "date": datetime.now(MOSCOW_TZ).date(),
        "day_name": "Понедельник",
        "lessons": lessons,
    }
    info.update(extra)
    return info


LESSONS = [
    {
        "time": "09:00-10:30",
        "subject": "Математика",
        "start_time_raw": "09:00",
        "end_time_raw": "10:30",
        "room": "101",
        "teachers": "Иванов И.И.",
    },
    {
        "time": "10:40-12:10",
        "subject": "Физика",
        "start_time_raw": "10:40",
        "end_time_raw": "12:10",
        "room": "102",
        "teachers": "Петров П.П.",
    },
]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Never let the scheduler touch real Dramatiq/Redis/weather."""
    monkeypatch.setattr(sched, "send_message_task", MagicMock())
    monkeypatch.setattr(sched, "send_lesson_reminder_task", MagicMock())
    monkeypatch.setattr(sched, "TASKS_SENT_TO_QUEUE", MagicMock())


@pytest.fixture
def weather(monkeypatch):
    api = AsyncMock()
    api.get_forecast_for_time.return_value = {
        "temperature": 5,
        "description": "ясно",
        "emoji": "☀️",
    }
    api.calls = []
    monkeypatch.setattr(sched, "WeatherAPI", lambda *a, **k: api)
    return api


# --------------------------------------------------------------------------- #
# Manager reference helpers (lines 67, 76)
# --------------------------------------------------------------------------- #
def test_manager_ref_get_set_and_resolve():
    manager = MagicMock()
    ref = sched.TimetableManagerRef(manager)
    assert ref.get() is manager
    assert sched._resolve_manager(ref) is manager
    other = MagicMock()
    ref.set(other)
    assert ref.get() is other
    assert sched._resolve_manager(other) is other


def test_print_progress_bar_finished_adds_newline(capsys):
    sched.print_progress_bar(3, 3, "Done", "!", 10)
    assert capsys.readouterr().out.endswith("\n")


# --------------------------------------------------------------------------- #
# Evening broadcast (133, 141, 150-169, 175, 190-191)
# --------------------------------------------------------------------------- #
async def test_evening_broadcast_teacher_and_falsy_user_type(weather, monkeypatch):
    udm = make_udm()
    udm.get_users_for_evening_notify.return_value = [
        (1, "T1", "ru", "teacher"),
        (2, "T2", "ru", "teacher"),
        (3, "S1", "en", None),
    ]
    tm = MagicMock()
    tm.get_teacher_schedule = AsyncMock(
        side_effect=lambda group, target_date=None: (
            make_schedule(LESSONS, teacher="Иванов") if group == "T1" else make_schedule([])
        )
    )
    tm.get_schedule_for_day = AsyncMock(return_value=make_schedule(LESSONS))

    send = MagicMock()
    monkeypatch.setattr(sched, "send_message_task", send)

    await sched.evening_broadcast(udm, tm)

    assert send.send.call_count == 3
    assert {call.args[0] for call in send.send.call_args_list} == {1, 2, 3}
    # Weather is requested per language of the recipients.
    requested_langs = {call.kwargs.get("lang") for call in weather.get_forecast_for_time.call_args_list}
    assert requested_langs == {"ru", "en"}
    # Teacher without lessons gets the generic "no lessons" text.
    teacher_no_lessons = send.send.call_args_list[1].args[1]
    assert "fmt_no_lessons" not in teacher_no_lessons


async def test_evening_broadcast_50_users_yields_event_loop(weather, monkeypatch):
    udm = make_udm()
    udm.get_users_for_evening_notify.return_value = [(i, "G", "ru", "student") for i in range(1, 51)]
    tm = MagicMock()
    tm.get_schedule_for_day = AsyncMock(return_value=make_schedule(LESSONS))
    tm.get_teacher_schedule = AsyncMock(return_value=make_schedule(LESSONS))

    send = MagicMock()
    monkeypatch.setattr(sched, "send_message_task", send)

    await sched.evening_broadcast(udm, tm)

    assert send.send.call_count == 50


# --------------------------------------------------------------------------- #
# Morning broadcast (220, 227, 234, 245-247)
# --------------------------------------------------------------------------- #
async def test_morning_broadcast_teacher_falsy_type_and_50_users(weather, monkeypatch):
    users = [(i, "G", "ru", "student") for i in range(1, 51)]
    users.append((51, "T", "ru", "teacher"))
    users.append((52, "N", "ru", None))  # falsy user_type -> student, no lessons

    udm = make_udm()
    udm.get_users_for_morning_summary.return_value = users

    tm = MagicMock()
    tm.get_schedule_for_day = AsyncMock(
        side_effect=lambda group, target_date=None: make_schedule([]) if group == "N" else make_schedule(LESSONS)
    )
    tm.get_teacher_schedule = AsyncMock(return_value=make_schedule(LESSONS, teacher="Петров"))

    send = MagicMock()
    monkeypatch.setattr(sched, "send_message_task", send)

    await sched.morning_summary_broadcast(udm, tm)

    # 50 students + teacher have lessons; the last student gets "no lessons".
    assert send.send.call_count == 52
    tm.get_teacher_schedule.assert_awaited_once()
    tm.get_schedule_for_day.assert_any_call("N", target_date=datetime.now(MOSCOW_TZ).date())


# --------------------------------------------------------------------------- #
# Reminders (279, 283, 292, 301, 317-318, 337-338)
# --------------------------------------------------------------------------- #
class _TruthyLessonsDict(dict):
    """Passes ``.get("lessons")`` truthiness but yields [] via ``__getitem__``.

    This exercises the defensive ``if not lessons`` guard that is otherwise
    unreachable with a plain dict.
    """

    def get(self, key, default=None):
        if key == "lessons":
            return ["placeholder"]
        return super().get(key, default)

    def __getitem__(self, key):
        if key == "lessons":
            return []
        return super().__getitem__(key)


async def test_minutely_reminders_edge_branches(monkeypatch):
    udm = make_udm()
    udm.get_users_for_lesson_reminders.return_value = [
        (1, "G_ERR", 20, "ru"),
        (2, "G_EMPTY", 20, "ru"),
        (3, "G_NA", 20, "ru"),
        (4, "G_ENDNA", 20, "ru"),
        (5, "G_BAD", 20, "ru"),
        (6, "", 20, "ru"),  # empty group name is ignored
        (7, "G_RAISE", 20, "ru"),
    ]

    def schedule_for(group, target_date=None):
        if group == "G_ERR":
            return {"error": "db down", "lessons": LESSONS}
        if group == "G_EMPTY":
            return _TruthyLessonsDict({"marker": True})
        if group == "G_NA":
            return {"lessons": [{"start_time_raw": "N/A", "end_time_raw": "10:30"}]}
        if group == "G_ENDNA":
            return {"lessons": [{"start_time_raw": "09:00", "end_time_raw": "N/A"}]}
        if group == "G_BAD":
            return {"lessons": [{"start_time_raw": "09:00", "end_time_raw": "not-a-time"}]}
        raise RuntimeError("group failure")

    tm = MagicMock()
    tm.get_schedule_for_day = AsyncMock(side_effect=schedule_for)
    task = MagicMock()
    monkeypatch.setattr(sched, "send_lesson_reminder_task", task)

    await sched.minutely_reminders_checker(udm, tm)

    task.send.assert_not_called()
    assert tm.get_schedule_for_day.await_count == 6  # empty group name skipped


async def test_minutely_reminders_no_users(monkeypatch):
    udm = make_udm()
    udm.get_users_for_lesson_reminders.return_value = []
    tm = MagicMock()
    tm.get_schedule_for_day = AsyncMock()
    await sched.minutely_reminders_checker(udm, tm)
    tm.get_schedule_for_day.assert_not_awaited()


def _patch_now(monkeypatch, hour, minute):
    class FakeDatetime:
        fixed = datetime.now(MOSCOW_TZ).replace(hour=hour, minute=minute, second=0, microsecond=0)

        @classmethod
        def now(cls, tz=None):
            return cls.fixed

        @classmethod
        def strptime(cls, value, fmt):
            return datetime.strptime(value, fmt)

        @classmethod
        def combine(cls, d, t):
            return datetime.combine(d, t)

    monkeypatch.setattr(sched, "datetime", FakeDatetime)


async def test_minutely_reminders_first_lesson(monkeypatch):
    udm = make_udm()
    udm.get_users_for_lesson_reminders.return_value = [(1, "G1", 20, "ru")]
    tm = MagicMock()
    tm.get_schedule_for_day = AsyncMock(
        return_value={"lessons": [{"time": "09:00-10:30", "start_time_raw": "09:00", "end_time_raw": "10:30"}]}
    )
    task = MagicMock()
    monkeypatch.setattr(sched, "send_lesson_reminder_task", task)
    _patch_now(monkeypatch, 8, 40)

    await sched.minutely_reminders_checker(udm, tm)

    types = [call.args[2] for call in task.send.call_args_list]
    assert "first" in types


async def test_minutely_reminders_break_and_final(monkeypatch):
    udm = make_udm()
    udm.get_users_for_lesson_reminders.return_value = [(1, "G1", 90, "ru")]
    # Both lessons end at the current minute: the first is a "break" event,
    # the last one is a "final" event.
    lessons = [
        {"time": "09:00-10:30", "start_time_raw": "09:00", "end_time_raw": "10:30", "subject": "A"},
        {"time": "10:40-10:30", "start_time_raw": "10:40", "end_time_raw": "10:30", "subject": "B"},
    ]
    tm = MagicMock()
    tm.get_schedule_for_day = AsyncMock(return_value={"lessons": lessons})
    task = MagicMock()
    monkeypatch.setattr(sched, "send_lesson_reminder_task", task)
    _patch_now(monkeypatch, 10, 30)

    await sched.minutely_reminders_checker(udm, tm)

    types = [call.args[2] for call in task.send.call_args_list]
    assert "break" in types
    assert "final" in types


# --------------------------------------------------------------------------- #
# Monitor schedule changes (355-365, 379, 402-403, 410-411, 422-427)
# --------------------------------------------------------------------------- #
async def test_monitor_parser_retries_then_alerts(monkeypatch):
    redis = make_redis()
    redis.get.return_value = b""
    monkeypatch.setattr(
        sched,
        "fetch_and_parse_all_schedules",
        AsyncMock(side_effect=RuntimeError("parser down")),
    )
    sent = {}

    class FakeAlertSender:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send(self, alert):
            sent.update(alert)
            return True

    monkeypatch.setattr("core.alert_sender.AlertSender", FakeAlertSender)

    await sched.monitor_schedule_changes(make_udm(), redis, MagicMock())

    assert sched.fetch_and_parse_all_schedules.await_count == 3
    assert sent["severity"] == "critical"
    redis.lock.assert_not_called()


async def test_monitor_empty_result_and_empty_cache(monkeypatch):
    redis = make_redis()
    redis.get.side_effect = [b"", None]  # hash, then cache
    monkeypatch.setattr(sched, "fetch_and_parse_all_schedules", AsyncMock(return_value={}))

    await sched.monitor_schedule_changes(make_udm(), redis, MagicMock())

    # Empty parse result returns early (system relies on fallback backups).
    redis.set.assert_not_called()


async def test_monitor_unchanged_hash(monkeypatch):
    redis = make_redis()
    redis.get.return_value = b"same-hash"
    monkeypatch.setattr(
        sched,
        "fetch_and_parse_all_schedules",
        AsyncMock(return_value={"__current_xml_hash__": "same-hash"}),
    )
    ts = MagicMock()
    monkeypatch.setattr(sched, "LAST_SCHEDULE_UPDATE_TS", ts)

    await sched.monitor_schedule_changes(make_udm(), redis, MagicMock())

    assert not any(call.args[0] == "timetable:schedule_hash" for call in redis.set.call_args_list)
    ts.set.assert_called()


async def test_monitor_change_path_handles_every_exception(monkeypatch):
    redis = make_redis()
    redis.get.return_value = b"old-hash"

    new_data = {"__current_xml_hash__": "new-hash", "groups": {}}
    monkeypatch.setattr(sched, "fetch_and_parse_all_schedules", AsyncMock(return_value=new_data))

    new_manager = MagicMock()
    new_manager.save_to_cache = AsyncMock()
    new_manager.set_semester_settings_manager = MagicMock()
    monkeypatch.setattr(sched, "TimetableManager", lambda *a, **k: new_manager)
    monkeypatch.setattr(sched, "SemesterSettingsManager", MagicMock())

    # metric update raises -> except pass (402-403)
    broken_metric = MagicMock()
    broken_metric.labels.return_value.set.side_effect = RuntimeError("metric")
    monkeypatch.setattr("core.metrics.LAST_DATA_UPDATE", broken_metric)

    # image cache invalidation raises (410-411)
    monkeypatch.setattr(
        "core.image_cache_manager.invalidate_all_generated_images",
        AsyncMock(side_effect=RuntimeError("cache")),
    )

    udm = make_udm()
    udm.get_admin_users.return_value = [101, 102]
    sent = []

    def send(uid, text):
        if uid == 102:
            raise RuntimeError("telegram down")
        sent.append((uid, text))

    monkeypatch.setattr(sched, "send_message_task", MagicMock(send=MagicMock(side_effect=send)))

    old_ref = sched.TimetableManagerRef(MagicMock())
    monkeypatch.setattr(sched, "manager_ref", old_ref)

    await sched.monitor_schedule_changes(udm, redis, MagicMock())

    assert redis.set.await_args_list[0].args == ("timetable:schedule_hash", "new-hash")
    assert old_ref.get() is new_manager
    new_manager.set_semester_settings_manager.assert_called_once()
    assert sent and sent[0][0] == 101


# --------------------------------------------------------------------------- #
# auto_backup (509-512, 521-528, 535-540, 544-548)
# --------------------------------------------------------------------------- #
@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("core.config.REDIS_SCHEDULE_CACHE_KEY", "schedule:cache")
    monkeypatch.setattr(sched, "create_db_backup", AsyncMock(return_value=tmp_path / "db.sql"))
    return tmp_path


async def test_auto_backup_unicode_replacement_json(backup_env):
    redis = make_redis()
    # Invalid UTF-8 inside a JSON string: strict decode fails, replace-decode works.
    redis.get.return_value = b'{"group": "\xff"}'

    await sched.auto_backup(redis)

    files = list((backup_env / "data" / "backups").glob("schedules_backup_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["group"] == "\ufffd"


async def test_auto_backup_raw_fallback(backup_env):
    redis = make_redis()
    redis.get.return_value = b"definitely not json"

    await sched.auto_backup(redis)

    raw = list((backup_env / "data" / "backups").glob("schedules_backup_raw_*.bin"))
    assert len(raw) == 1
    assert raw[0].read_bytes() == b"definitely not json"


async def test_auto_backup_removes_expired_files(backup_env):
    backup_dir = backup_env / "data" / "backups"
    backup_dir.mkdir(parents=True)
    old_file = backup_dir / "old_backup.bin"
    old_file.write_bytes(b"old")
    old_ts = (datetime.now(MOSCOW_TZ) - timedelta(days=40)).timestamp()
    os.utime(old_file, (old_ts, old_ts))

    redis = make_redis()
    redis.get.return_value = None

    await sched.auto_backup(redis)

    assert not old_file.exists()


async def test_auto_backup_cleanup_error_is_logged(backup_env, monkeypatch):
    backup_dir = backup_env / "data" / "backups"
    backup_dir.mkdir(parents=True)
    old_file = backup_dir / "old_backup.bin"
    old_file.write_bytes(b"old")
    old_ts = (datetime.now(MOSCOW_TZ) - timedelta(days=40)).timestamp()
    os.utime(old_file, (old_ts, old_ts))

    def broken_unlink(self):
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", broken_unlink)

    redis = make_redis()
    redis.get.return_value = None

    await sched.auto_backup(redis)

    assert old_file.exists()


async def test_auto_backup_outer_exception(backup_env):
    redis = make_redis()
    redis.get.side_effect = RuntimeError("redis down")

    await sched.auto_backup(redis)  # must not raise


# --------------------------------------------------------------------------- #
# send_schedule_diff_notifications (630-631, 639-640, 656-665)
# --------------------------------------------------------------------------- #
async def test_diff_notifications_changes_admin_report_and_errors(monkeypatch):
    udm = make_udm()
    users = [(i, f"G{i}") for i in range(1, 11)]
    udm.get_all_users_with_groups.return_value = users
    udm.get_admin_users.return_value = [777, 999]
    udm.async_session_maker = MagicMock()

    audit = MagicMock()
    audit.record_changes = AsyncMock()
    monkeypatch.setattr("core.audit_manager.AuditManager", MagicMock(return_value=audit))

    def compare(group, target_date, old_schedule_data, new_schedule_data):
        diff = MagicMock()
        diff.has_changes.return_value = group == "G1"
        diff.changes = [{"field": "room"}]
        return diff

    monkeypatch.setattr("bot.scheduler.ScheduleDiffDetector.compare_group_schedules", compare)
    monkeypatch.setattr(
        "bot.scheduler.ScheduleDiffFormatter.format_group_diff",
        MagicMock(return_value="changed!"),
    )

    sent = []

    def send(uid, text):
        if uid == 999:
            raise RuntimeError("admin send failed")
        sent.append(uid)

    monkeypatch.setattr(sched, "send_message_task", MagicMock(send=MagicMock(side_effect=send)))

    old_manager = MagicMock()
    new_manager = MagicMock()
    old_manager.get_schedule_for_day = AsyncMock(return_value={})
    new_manager.get_schedule_for_day = AsyncMock(return_value={})

    await sched.send_schedule_diff_notifications(udm, old_manager, new_manager)

    assert 1 in sent  # user of group G1
    assert 777 in sent  # first admin report delivered
    assert audit.record_changes.await_count == 7  # one call per checked date


async def test_diff_notifications_comparison_exception(monkeypatch):
    udm = make_udm()
    udm.get_all_users_with_groups.return_value = [(1, "G1")]
    udm.async_session_maker = MagicMock()
    monkeypatch.setattr("core.audit_manager.AuditManager", MagicMock())

    old_manager = MagicMock()
    new_manager = MagicMock()
    old_manager.get_schedule_for_day = AsyncMock(side_effect=RuntimeError("boom"))
    new_manager.get_schedule_for_day = AsyncMock(return_value={})

    await sched.send_schedule_diff_notifications(udm, old_manager, new_manager)


async def test_diff_notifications_outer_exception(monkeypatch):
    udm = make_udm()
    udm.get_all_users_with_groups.side_effect = RuntimeError("db down")

    await sched.send_schedule_diff_notifications(udm, MagicMock(), MagicMock())


# --------------------------------------------------------------------------- #
# handle_graduated_groups (684-685, 737-741, 755-756)
# --------------------------------------------------------------------------- #
async def test_graduated_groups_no_users():
    udm = make_udm()
    udm.get_students_with_groups.return_value = []
    await sched.handle_graduated_groups(udm, MagicMock(_schedules={}), make_redis())


async def test_graduated_groups_50_users_and_send_error(monkeypatch):
    udm = make_udm()
    udm.get_students_with_groups.return_value = [(i, "old_group") for i in range(1, 52)]
    udm.set_user_group = AsyncMock()
    redis = make_redis()

    def send(uid, text):
        if uid == 51:
            raise RuntimeError("blocked by user")

    monkeypatch.setattr(sched, "send_message_task", MagicMock(send=MagicMock(side_effect=send)))

    tm = MagicMock()
    tm._schedules = {"CURRENT": {}}

    await sched.handle_graduated_groups(udm, tm, redis)

    assert udm.set_user_group.await_count == 50
    redis.set.assert_awaited()


async def test_graduated_groups_stats_save_error(monkeypatch):
    udm = make_udm()
    udm.get_students_with_groups.return_value = [(1, "OLD")]
    udm.set_user_group = AsyncMock()
    redis = make_redis()
    redis.set.side_effect = RuntimeError("redis write failed")

    tm = MagicMock()
    tm._schedules = {"CURRENT": {}}

    await sched.handle_graduated_groups(udm, tm, redis)


# --------------------------------------------------------------------------- #
# setup_scheduler with image cache jobs (830)
# --------------------------------------------------------------------------- #
def test_setup_scheduler_enables_image_cache_jobs(monkeypatch):
    scheduler = MagicMock()
    monkeypatch.setattr(sched, "AsyncIOScheduler", MagicMock(return_value=scheduler))
    monkeypatch.setenv("ENABLE_IMAGE_CACHE_JOBS", "true")

    result = sched.setup_scheduler(MagicMock(), MagicMock(), make_udm(), make_redis())

    assert result is scheduler
    job_funcs = [call.args[0] for call in scheduler.add_job.call_args_list]
    assert sched.cleanup_image_cache in job_funcs


# ---------------------------------------------------------------------------
# Added branches for teacher/trust hardening
# ---------------------------------------------------------------------------
async def test_minutely_reminders_outer_exception(monkeypatch):
    udm = make_udm()
    udm.get_users_for_lesson_reminders_with_type.side_effect = RuntimeError("db down")
    await sched.minutely_reminders_checker(udm, MagicMock())


async def test_monitor_diff_exception_is_swallowed(monkeypatch):
    redis = make_redis()
    redis.get.return_value = b"old"
    monkeypatch.setattr(
        sched,
        "fetch_and_parse_all_schedules",
        AsyncMock(return_value={"__current_xml_hash__": "new"}),
    )
    monkeypatch.setattr(sched, "TimetableManager", lambda *a: MagicMock(save_to_cache=AsyncMock()))
    monkeypatch.setattr(sched, "send_schedule_diff_notifications", AsyncMock(side_effect=RuntimeError("diff down")))
    monkeypatch.setattr(sched, "manager_ref", None)
    await sched.monitor_schedule_changes(make_udm(), redis, MagicMock())


async def test_monitor_freshness_redis_error_is_swallowed(monkeypatch):
    redis = make_redis()
    redis.get.return_value = b"same"
    redis.set.side_effect = RuntimeError("redis write down")
    monkeypatch.setattr(
        sched,
        "fetch_and_parse_all_schedules",
        AsyncMock(return_value={"__current_xml_hash__": "same"}),
    )
    await sched.monitor_schedule_changes(make_udm(), redis, MagicMock())


async def test_auto_backup_reads_gzip_json(monkeypatch, tmp_path):
    import gzip
    import json

    payload = {"__metadata__": {}, "G1": {}}
    redis = make_redis()
    redis.get.return_value = gzip.compress(json.dumps(payload).encode())
    monkeypatch.setattr(sched.Path, "exists", lambda self: True)
    monkeypatch.setattr(sched, "Path", MagicMock(return_value=tmp_path))
    await sched.auto_backup(redis)


async def test_diff_getter_type_error_falls_back(monkeypatch):
    udm = make_udm()
    udm.get_students_with_groups_and_language = AsyncMock(return_value=object())
    udm.get_all_users_with_groups.return_value = []
    await sched.send_schedule_diff_notifications(udm, MagicMock(), MagicMock())


async def test_graduated_redis_incr_error_preserves_profile(monkeypatch):
    udm = make_udm()
    udm.get_students_with_groups.return_value = [(1, "OLD")]
    redis = make_redis()
    redis.incr.side_effect = RuntimeError("redis down")
    tm = MagicMock(_schedules={"CURRENT": {}})
    await sched.handle_graduated_groups(udm, tm, redis)
    udm.set_user_group.assert_not_awaited()


async def test_graduated_delete_errors_are_swallowed(monkeypatch):
    udm = make_udm()
    udm.get_students_with_groups.return_value = [(1, "CURRENT")]
    redis = make_redis()
    redis.delete.side_effect = RuntimeError("redis down")
    tm = MagicMock(_schedules={"CURRENT": {}})
    await sched.handle_graduated_groups(udm, tm, redis)

    udm.get_students_with_groups.return_value = [(2, "OLD")]
    redis.incr.return_value = 2
    await sched.handle_graduated_groups(udm, tm, redis)


async def test_monitor_image_invalidation_error_is_swallowed(monkeypatch):
    redis = make_redis()
    redis.get.return_value = b"old"
    monkeypatch.setattr(
        sched,
        "fetch_and_parse_all_schedules",
        AsyncMock(return_value={"__current_xml_hash__": "new"}),
    )
    monkeypatch.setattr(sched, "TimetableManager", lambda *a: MagicMock(save_to_cache=AsyncMock()))
    monkeypatch.setattr(sched, "send_schedule_diff_notifications", AsyncMock(return_value={"G1"}))
    monkeypatch.setattr(
        "core.image_cache_manager.invalidate_generated_images_for_groups",
        AsyncMock(side_effect=RuntimeError("cache down")),
    )
    monkeypatch.setattr(sched, "manager_ref", None)
    await sched.monitor_schedule_changes(make_udm(), redis, MagicMock())

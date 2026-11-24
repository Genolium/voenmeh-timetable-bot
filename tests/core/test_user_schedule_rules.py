import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.db.models import Base, UserLessonRule
from core.user_schedule_rules import (
    UserScheduleRulesManager,
    _serialize_rule,
    apply_rules_to_lessons,
    apply_rules_to_weeks,
    rules_fingerprint,
)


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def test_serialize_and_fingerprint_and_apply_helpers():
    # None, valid dict, bad JSON, and non-dict JSON in payload_json
    none_rule = UserLessonRule(id=0, user_id=1, rule_type="note", target_group="О735Б", subject="Физика", payload_json=None)
    assert _serialize_rule(none_rule)["payload"] == {}
    ok_rule = UserLessonRule(
        id=3, user_id=1, rule_type="note", target_group="О735Б", subject="Физика", payload_json='{"note": "hi"}'
    )
    assert _serialize_rule(ok_rule)["payload"] == {"note": "hi"}
    bad_rule = UserLessonRule(id=1, user_id=1, rule_type="note", target_group="О735Б", subject="Физика", payload_json="{bad")
    assert _serialize_rule(bad_rule)["payload"] == {}
    list_rule = UserLessonRule(id=2, user_id=1, rule_type="note", target_group="О735Б", subject="Физика", payload_json="[1]")
    assert _serialize_rule(list_rule)["payload"] == {}

    assert rules_fingerprint([]) == ""
    fp = rules_fingerprint([{"id": 1, "rule_type": "note"}])
    assert len(fp) == 10

    lessons = [
        {"subject": "Физика", "start_time_raw": "09:00"},
        {"subject": "Математика", "start_time_raw": "10:50"},
        {"subject": "История", "start_time_raw": "12:40"},
    ]
    assert apply_rules_to_lessons(lessons, [], day_name="Понедельник", week_key="odd") == lessons

    rules = [
        {"rule_type": "hide_subject", "subject": "Физика", "week_key": "even", "day_name": None},
        {"rule_type": "hide_subject", "subject": "Физика", "week_key": "odd", "day_name": "Вторник"},
        {"rule_type": "hide_subject", "subject": "Физика", "week_key": "odd", "day_name": "Понедельник"},
        {"rule_type": "note", "subject": "Математика", "start_time": "14:20", "payload": {"note": "skip"}},
        {"rule_type": "note", "subject": "История", "start_time": "12:40", "payload": {"note": ""}},
        {"rule_type": "note", "subject": "Математика", "start_time": "10:50", "payload": {"note": "КР"}},
        {
            "rule_type": "custom_lesson",
            "target_group": "О735Б",
            "subject": "Спорт",
            "day_name": "Понедельник",
            "start_time": "08:00",
            "payload": {"end_time_raw": "09:30", "room": "Зал"},
        },
        {"rule_type": "unknown", "subject": "X"},
    ]
    res = apply_rules_to_lessons(lessons, rules, day_name="Понедельник", week_key="odd")
    assert [x["subject"] for x in res] == ["Спорт", "Математика [КР]", "История"]
    assert res[1]["user_note"] == "КР"

    weeks = {"odd": {"Понедельник": lessons}, "even": {}}
    assert apply_rules_to_weeks(weeks, []) is weeks
    updated = apply_rules_to_weeks(weeks, rules)
    assert "Понедельник" in updated["odd"]
    assert updated["even"]["Понедельник"][0]["subject"] == "Спорт"


@pytest.mark.asyncio
async def test_user_schedule_rules_manager_crud_and_cache(session_factory):
    # Fake redis with get/set/delete/scan_iter
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=[Exception("boom"), b'[{"rule_type": "note"}]', json.dumps({"not": "list"}), None])
    redis.set = AsyncMock(side_effect=Exception("set err"))
    redis.delete = AsyncMock()

    async def _scan_iter(_pattern):
        yield b"user_rules:10:O735B"

    redis.scan_iter = _scan_iter

    mgr = UserScheduleRulesManager(session_factory, redis_client=redis)
    assert await mgr.get_rules(10, "") == []

    # Cache error -> DB -> set error
    assert await mgr.get_rules(10, "о735б") == []
    # Cache hit (bytes list)
    assert await mgr.get_rules(10, "о735б") == [{"rule_type": "note"}]
    # Cache hit (non-list) -> falls through to DB
    assert await mgr.get_rules(10, "о735б") == []
    # Cache miss (None) -> falls through to DB
    assert await mgr.get_rules(10, "о735б") == []

    # Toggle hide_subject on/off
    assert await mgr.toggle_hide_subject(10, "о735б", "Физика") is True
    assert await mgr.toggle_hide_subject(10, "о735б", "Физика") is False

    # Add note & custom lesson
    note_id = await mgr.add_note(10, "о735б", "Математика", "взять тетрадь", week_key="odd", day_name="ПОНЕДЕЛЬНИК")
    custom_id = await mgr.add_custom_lesson(
        10,
        "о735б",
        day_name="ПОНЕДЕЛЬНИК",
        start_time="08:00",
        end_time="09:30",
        subject="Доп. занятие",
        room="101",
    )
    assert note_id > 0 and custom_id > 0

    # Delete rule (missing vs existing)
    assert await mgr.delete_rule(10, 999999) is False
    assert await mgr.delete_rule(10, note_id) is True

    # Clear rules without target_group (triggers scan_iter invalidation) and with target_group
    cleared = await mgr.clear_rules(10)
    assert cleared == 1
    assert await mgr.clear_rules(10, target_group="о735б") == 0

    # Invalidate error branch
    redis.delete = AsyncMock(side_effect=Exception("del err"))
    await mgr._invalidate(10, "о735б")
    # No redis branch
    mgr_no_redis = UserScheduleRulesManager(session_factory, redis_client=None)
    await mgr_no_redis._invalidate(10)
    assert await mgr_no_redis.get_rules(10, "о735б") == []

"""Wave 1 contracts: parser 304 vs fail, keyboard, search, onboarding, times, pickle."""

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest

from core.bot_identity import bot_mention, get_bot_username, set_bot_username
from core.i18n import i18n
from core.manager import TimetableManager, refresh_schedule_cache_ttl
from core.parser import NOT_MODIFIED, resolve_lesson_end
from core.user_data import UserDataManager

NEW_I18N_KEYS = (
    "btn_more",
    "btn_my_group",
    "nav_today",
    "more_title",
    "btn_cancel",
    "onboarding_reminders_hint",
    "pick_suggestion",
    "settings_diff_label",
    "settings_btn_enable_diff",
    "settings_btn_disable_diff",
    "btn_change_role",
)


def test_i18n_keys_exist_in_all_locales():
    for lang in ("ru", "en", "zh"):
        for key in NEW_I18N_KEYS:
            value = i18n.get(key, lang=lang)
            assert value != key, f"missing {key} in {lang}"
            assert "{bot_username}" in i18n.get("about_page_5", lang=lang)
            assert "{bot_username}" in i18n.get("inline_help_message", lang=lang)


def test_day_keyboard_widget_ids():
    from bot.dialogs.states import Schedule

    source = Path("bot/dialogs/schedule_view.py").read_text(encoding="utf-8")
    assert "state=Schedule.view" in source
    assert "state=Schedule.more" in source
    assert Schedule.view.state is not None
    assert Schedule.more.state is not None
    view_part, _, rest = source.partition("state=Schedule.view")
    more_part, _, _ = rest.partition("state=Schedule.more")
    assert "id=WidgetIds.MORE" in view_part
    assert "id=WidgetIds.TODAY" in view_part
    assert "id=WidgetIds.FIND_BTN" in view_part
    assert "id=WidgetIds.MY_GROUP" in view_part
    assert "nav_date_label" in view_part
    assert "help_btn" not in view_part
    assert "feedback_btn" not in view_part
    assert "report_error_btn" not in view_part
    assert 'id="news_url"' in more_part
    assert "Url(" in more_part
    assert 'id="help_btn"' in more_part
    assert "id=WidgetIds.CHANGE_GROUP" in more_part
    assert "id=WidgetIds.SETTINGS" in more_part


def test_day_row_labels_fit_phone_keyboard():
    for lang in ("ru", "en", "zh"):
        for key in ("btn_week", "btn_search", "btn_more", "nav_today", "btn_my_group"):
            label = i18n.get(key, lang=lang)
            assert len(label) <= 14, f"{key}/{lang} too long for a 3-button row: {label!r}"


def test_resolve_lesson_end_prefers_api_and_falls_back():
    start = datetime.strptime("14:55", "%H:%M")
    assert resolve_lesson_end(start, {"end": "16:10"}) == "16:10"
    assert resolve_lesson_end(start, {"end_time": "16:20"}) == "16:20"
    assert resolve_lesson_end(start, {"end": "bad"}) == "16:25"
    assert resolve_lesson_end(start, {}) == "16:25"


def test_bot_username_cache():
    set_bot_username("bstu_timetable_bot")
    assert get_bot_username() == "bstu_timetable_bot"
    assert bot_mention() == "@bstu_timetable_bot"
    set_bot_username(None)
    assert get_bot_username() == "voenmeh_bot"


def test_group_name_is_url_quoted():
    assert quote("О735Б", safe="") != "О735Б"
    assert "%D0%" in quote("О735Б", safe="")


@pytest.mark.asyncio
async def test_fetch_all_returns_not_modified_not_none():
    import core.parser as parser

    with patch("core.parser.fetch_and_parse_from_voenmeh_su", AsyncMock(return_value=NOT_MODIFIED)):
        result = await parser.fetch_and_parse_all_schedules()
    assert result is NOT_MODIFIED
    assert result is not None


@pytest.mark.asyncio
async def test_refresh_schedule_cache_ttl_expires_existing_keys():
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    await refresh_schedule_cache_ttl(redis)
    assert redis.expire.await_count >= 1


@pytest.mark.asyncio
async def test_refresh_schedule_cache_ttl_swallows_redis_errors():
    redis = AsyncMock()
    redis.exists = AsyncMock(side_effect=RuntimeError("down"))
    await refresh_schedule_cache_ttl(redis)


@pytest.mark.asyncio
async def test_refresh_schedule_cache_ttl_skips_missing_keys():
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    redis.expire = AsyncMock()
    await refresh_schedule_cache_ttl(redis)
    redis.expire.assert_not_awaited()


def test_decompress_rejects_pickle():
    import gzip
    import pickle

    manager = TimetableManager({"__metadata__": {}, "G": {}}, AsyncMock())
    payload = gzip.compress(pickle.dumps({"evil": True}))
    with pytest.raises(ValueError, match="JSON only"):
        manager._decompress_data(payload)


@pytest.mark.asyncio
async def test_hash_written_after_save_to_cache(monkeypatch):
    from bot.scheduler import monitor_schedule_changes

    order = []

    async def save():
        order.append("save")

    class FakeManager:
        def __init__(self, *args, **kwargs):
            self.save_to_cache = save

        def set_semester_settings_manager(self, *_a, **_k):
            return None

    redis = AsyncMock()
    redis.get.return_value = b"old"
    mock_lock = AsyncMock()
    mock_lock.__aenter__ = AsyncMock()
    mock_lock.__aexit__ = AsyncMock()
    redis.lock = MagicMock(return_value=mock_lock)

    async def fake_set(key, value, *args, **kwargs):
        order.append(("set", key, value))

    redis.set = AsyncMock(side_effect=fake_set)

    monkeypatch.setattr(
        "bot.scheduler.fetch_and_parse_all_schedules",
        AsyncMock(return_value={"__current_xml_hash__": "new", "G": {"odd": {}}}),
    )
    monkeypatch.setattr("bot.scheduler.TimetableManager", FakeManager)
    monkeypatch.setattr("bot.scheduler.SemesterSettingsManager", MagicMock())
    monkeypatch.setattr("bot.scheduler.send_schedule_diff_notifications", AsyncMock(return_value=set()))
    monkeypatch.setattr("bot.scheduler.LAST_SCHEDULE_UPDATE_TS", MagicMock())

    await monitor_schedule_changes(AsyncMock(), redis, MagicMock())
    assert "save" in order
    hash_idx = next(i for i, item in enumerate(order) if item[0] == "set" and item[1] == "timetable:schedule_hash")
    assert order.index("save") < hash_idx


@pytest.mark.asyncio
async def test_unchanged_refresh_ttl_and_last_update(monkeypatch):
    from bot.scheduler import monitor_schedule_changes

    redis = AsyncMock()
    redis.get.return_value = b"hash"
    mock_lock = AsyncMock()
    mock_lock.__aenter__ = AsyncMock()
    mock_lock.__aexit__ = AsyncMock()
    redis.lock = MagicMock(return_value=mock_lock)
    redis.exists = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    redis.set = AsyncMock()

    monkeypatch.setattr("bot.scheduler.fetch_and_parse_all_schedules", AsyncMock(return_value=NOT_MODIFIED))
    monkeypatch.setattr("bot.scheduler.LAST_SCHEDULE_UPDATE_TS", MagicMock())
    monkeypatch.setattr("bot.scheduler.refresh_schedule_cache_ttl", AsyncMock())

    from bot import scheduler as sched

    await monitor_schedule_changes(AsyncMock(), redis, MagicMock())
    sched.refresh_schedule_cache_ttl.assert_awaited()
    assert redis.set.await_count >= 1


@pytest.mark.asyncio
async def test_unchanged_last_update_set_error_is_swallowed(monkeypatch):
    from bot.scheduler import monitor_schedule_changes

    redis = AsyncMock()
    redis.get.return_value = b"hash"
    mock_lock = AsyncMock()
    mock_lock.__aenter__ = AsyncMock()
    mock_lock.__aexit__ = AsyncMock()
    redis.lock = MagicMock(return_value=mock_lock)
    redis.set = AsyncMock(side_effect=RuntimeError("redis down"))

    monkeypatch.setattr("bot.scheduler.fetch_and_parse_all_schedules", AsyncMock(return_value=NOT_MODIFIED))
    monkeypatch.setattr("bot.scheduler.LAST_SCHEDULE_UPDATE_TS", MagicMock())
    monkeypatch.setattr("bot.scheduler.refresh_schedule_cache_ttl", AsyncMock())

    await monitor_schedule_changes(AsyncMock(), redis, MagicMock())


@pytest.mark.asyncio
async def test_failed_parse_does_not_refresh_ttl(monkeypatch):
    from bot.scheduler import monitor_schedule_changes

    redis = AsyncMock()
    redis.get.return_value = b"hash"
    mock_lock = AsyncMock()
    mock_lock.__aenter__ = AsyncMock()
    mock_lock.__aexit__ = AsyncMock()
    redis.lock = MagicMock(return_value=mock_lock)

    monkeypatch.setattr("bot.scheduler.fetch_and_parse_all_schedules", AsyncMock(return_value=None))
    monkeypatch.setattr("bot.scheduler.refresh_schedule_cache_ttl", AsyncMock())
    from bot import scheduler as sched

    await monitor_schedule_changes(AsyncMock(), redis, MagicMock())
    sched.refresh_schedule_cache_ttl.assert_not_awaited()


@pytest.mark.asyncio
async def test_teacher_searching_group_uses_group_lookup():
    from bot.dialogs.schedule_view import get_schedule_data

    dm = AsyncMock()
    ctx = MagicMock()
    ctx.dialog_data = {"group": "О735Б", "current_date_iso": "2026-10-24"}
    dm.current_context = MagicMock(return_value=ctx)
    dm.start_data = {}
    tm = AsyncMock()
    tm._schedules = {"О735Б": {}}
    tm.get_schedule_for_day.return_value = {
        "lessons": [{"subject": "A", "time": "09:00-10:30", "start_time_raw": "09:00", "end_time_raw": "10:30"}],
        "date": datetime(2026, 10, 24).date(),
        "week_name": "Четная",
        "week_number": 8,
    }
    udm = AsyncMock()
    udm.get_user_language.return_value = "ru"
    udm.get_user_type.return_value = "teacher"
    udm.get_user_group.return_value = "Иванов И.И."
    dm.middleware_data = {"manager": tm, "user_data_manager": udm}
    dm.event.from_user.id = 1

    data = await get_schedule_data(dm)
    tm.get_schedule_for_day.assert_awaited()
    tm.get_teacher_schedule.assert_not_awaited()
    assert data["show_my_group"] is True
    assert data["has_lessons"] is True
    assert data["nav_date_label"] == "Сегодня"


@pytest.mark.asyncio
async def test_change_group_goes_to_group_entry_not_role():
    from bot.dialogs.schedule_view import on_change_group_click
    from bot.dialogs.states import MainMenu

    dm = AsyncMock()
    ctx = MagicMock()
    ctx.dialog_data = {"user_type": "student"}
    dm.current_context = MagicMock(return_value=ctx)
    await on_change_group_click(MagicMock(), MagicMock(), dm)
    dm.start.assert_awaited_once()
    assert dm.start.await_args.args[0] == MainMenu.enter_group


@pytest.mark.asyncio
async def test_tappable_group_suggestion_switches_state():
    from bot.dialogs.main_menu import on_group_entered
    from bot.dialogs.states import MainMenu

    dm = AsyncMock()
    dm.middleware_data = {
        "manager": MagicMock(_schedules={"О735Б": {}, "О735А": {}}),
        "user_data_manager": AsyncMock(),
        "_": lambda k, **kw: k,
    }
    dm.dialog_data = {}
    message = AsyncMock(text="О735")
    message.from_user = MagicMock(id=1, username="u")
    await on_group_entered(message, None, dm)
    assert dm.dialog_data["group_suggestions"]
    dm.switch_to.assert_awaited_with(MainMenu.enter_group)


@pytest.mark.asyncio
async def test_group_suggestion_button_registers_user():
    from bot.dialogs.main_menu import on_group_suggestion_selected
    from bot.dialogs.states import MainMenu

    dm = AsyncMock()
    udm = AsyncMock()
    dm.middleware_data = {"user_data_manager": udm}
    dm.dialog_data = {"group_suggestions": ["О735Б"]}
    callback = MagicMock()
    callback.from_user = MagicMock(id=9, username="u")
    await on_group_suggestion_selected(callback, None, dm, "О735Б")
    udm.set_user_group.assert_awaited_with(user_id=9, group="О735Б")
    dm.switch_to.assert_awaited_with(MainMenu.offer_tutorial)


def test_skip_ru_language_onboarding():
    source = Path("main.py").read_text(encoding="utf-8")
    assert 'lang_code.startswith("ru")' in source
    assert "MainMenu.choose_user_type" in source
    assert "MainMenu.select_language" in source


@pytest.mark.asyncio
async def test_morning_evening_match_hhmm(user_data_manager_factory=None):
    from sqlalchemy.dialects import postgresql

    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    session.execute.return_value = result
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=None)
    mgr = UserDataManager("sqlite+aiosqlite:///:memory:")
    mgr.async_session_maker = MagicMock(return_value=context)

    await mgr.get_users_for_morning_summary(target_time="08:30")
    stmt = session.execute.call_args[0][0]
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    assert "users.morning_time = '08:30'" in str(compiled)

    await mgr.get_users_for_evening_notify(target_time="20:15")
    stmt = session.execute.call_args[0][0]
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    assert "users.evening_time = '20:15'" in str(compiled)


@pytest.mark.asyncio
async def test_empty_day_caption_is_not_triplicated():
    from bot.dialogs.schedule_view import _when_no_lessons, _when_show_schedule_text, get_schedule_data

    dm = AsyncMock()
    ctx = MagicMock()
    ctx.dialog_data = {"group": "О735Б", "current_date_iso": "2026-10-24"}
    dm.current_context = MagicMock(return_value=ctx)
    dm.start_data = {}
    tm = AsyncMock()
    tm._schedules = {"О735Б": {}}
    tm.get_schedule_for_day.return_value = {
        "lessons": [],
        "date": datetime(2026, 10, 24).date(),
        "week_name": "Четная",
        "week_number": 8,
    }
    udm = AsyncMock()
    udm.get_user_language.return_value = "ru"
    udm.get_user_type.return_value = "student"
    udm.get_user_group.return_value = "О735Б"
    dm.middleware_data = {"manager": tm, "user_data_manager": udm}
    dm.event.from_user.id = 1

    data = await get_schedule_data(dm)
    assert data["has_lessons"] is False
    assert data["schedule_text"] == ""
    assert "Занятий нет" not in data["empty_caption"]
    assert "Сегодня занятий нет" not in data["empty_caption"]
    assert "О735Б" in data["empty_caption"]
    assert not _when_show_schedule_text(data, None, None)
    assert _when_no_lessons(data, None, None)


@pytest.mark.asyncio
async def test_on_more_and_my_group_and_role_and_cancel():
    from bot.dialogs.feedback_menu import on_feedback_cancel
    from bot.dialogs.schedule_view import _when_has_lessons, _when_show_my_group, on_more_click, on_my_group_click
    from bot.dialogs.settings_menu import on_change_role_click
    from bot.dialogs.states import MainMenu, Schedule

    dm = AsyncMock()
    await on_more_click(MagicMock(), MagicMock(), dm)
    dm.switch_to.assert_awaited_with(Schedule.more)

    udm = AsyncMock()
    udm.get_user_group.return_value = "О735Б"
    dm.middleware_data = {"user_data_manager": udm}
    callback = MagicMock()
    callback.from_user.id = 7
    await on_my_group_click(callback, MagicMock(), dm)
    dm.start.assert_awaited()
    assert dm.start.await_args.args[0] == Schedule.view
    assert dm.start.await_args.kwargs["data"]["group"] == "О735Б"

    dm.start.reset_mock()
    await on_change_role_click(MagicMock(), MagicMock(), dm)
    assert dm.start.await_args.args[0] == MainMenu.choose_user_type

    await on_feedback_cancel(MagicMock(), MagicMock(), dm)
    dm.done.assert_awaited()
    assert _when_has_lessons({"has_lessons": True}, None, None)
    assert _when_show_my_group({"show_my_group": True}, None, None)


def test_is_unchanged_sentinel():
    from core.parser import is_unchanged_result

    assert is_unchanged_result(NOT_MODIFIED) is True
    assert is_unchanged_result(None) is False
    assert is_unchanged_result({}) is False


def test_news_channel_url_variants(monkeypatch):
    import core.config as config_module

    monkeypatch.setattr(config_module, "SUBSCRIPTION_CHANNEL", "https://t.me/voenmeh404")
    assert config_module.news_channel_url() == "https://t.me/voenmeh404"
    monkeypatch.setattr(config_module, "SUBSCRIPTION_CHANNEL", "@campus")
    assert config_module.news_channel_url() == "https://t.me/campus"
    monkeypatch.setattr(config_module, "SUBSCRIPTION_CHANNEL", "-100123")
    assert config_module.news_channel_url().startswith("https://t.me/")
    monkeypatch.setattr(config_module, "SUBSCRIPTION_CHANNEL", "voenmeh404")
    assert config_module.news_channel_url() == "https://t.me/voenmeh404"
    monkeypatch.setattr(config_module, "SUBSCRIPTION_CHANNEL", "")
    assert "t.me" in config_module.news_channel_url()


@pytest.mark.asyncio
async def test_teacher_suggestion_registers_teacher():
    from bot.dialogs.main_menu import on_teacher_suggestion_selected
    from bot.dialogs.states import MainMenu

    dm = AsyncMock()
    udm = AsyncMock()
    dm.middleware_data = {"user_data_manager": udm}
    dm.dialog_data = {"teacher_suggestions": ["Иванов И.И."]}
    callback = MagicMock()
    callback.from_user = MagicMock(id=3, username="t")
    await on_teacher_suggestion_selected(callback, None, dm, "Иванов И.И.")
    udm.set_user_type.assert_awaited_with(user_id=3, user_type="teacher")
    dm.switch_to.assert_awaited_with(MainMenu.offer_tutorial)

"""Branch-coverage tests for the first half of ``core/*``.

Covered modules:

* ``core.alert_sender``      -- ``__aexit__`` without an open session
* ``core.db.backups``        -- dump sanitiser that has nothing to strip
* ``core.image_cache_manager`` -- metadata/invalidation edge branches
* ``core.image_generator``   -- the double-checked pool initialisation
* ``core.manager``           -- classroom index lookup for a foreign day

No network, no Docker, no real Redis: every collaborator is faked.
"""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from core import image_cache_manager as icm_module
from core import image_generator as ig
from core.alert_sender import AlertSender
from core.db.backups import _sanitize_dump_file
from core.image_cache_manager import (
    ImageCacheManager,
    invalidate_all_generated_images,
    invalidate_generated_images_for_groups,
)
from core.manager import TimetableManager

# ---------------------------------------------------------------------------
# core.alert_sender
# ---------------------------------------------------------------------------


async def test_alert_sender_aexit_without_session_is_a_noop():
    """``__aexit__`` must tolerate a sender that never opened a session."""
    sender = AlertSender({})

    assert sender.session is None

    await sender.__aexit__(None, None, None)

    assert sender.session is None


async def test_alert_sender_aexit_closes_existing_session():
    """The complementary branch: an open session is closed."""
    sender = AlertSender({})
    session = MagicMock()
    session.close = AsyncMock()
    sender.session = session

    await sender.__aexit__(None, None, None)

    session.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# core.db.backups
# ---------------------------------------------------------------------------


def test_sanitize_dump_file_keeps_sql_without_unsupported_settings(tmp_path):
    """A dump without ``set transaction_timeout`` is left untouched."""
    dump = tmp_path / "dump.sql"
    original = "SELECT 1;\nSET statement_timeout = 0;\n"
    dump.write_text(original, encoding="utf-8")

    _sanitize_dump_file(dump)

    assert dump.read_text(encoding="utf-8") == original


def test_sanitize_dump_file_strips_unsupported_setting(tmp_path):
    """The complementary branch: the unsupported setting is removed."""
    dump = tmp_path / "dump.sql"
    dump.write_text("SELECT 1;\nSET transaction_timeout = 0;\n", encoding="utf-8")

    _sanitize_dump_file(dump)

    assert dump.read_text(encoding="utf-8") == "SELECT 1;\n"


def test_sanitize_dump_file_missing_file_is_ignored(tmp_path):
    """A missing dump file must not raise."""
    _sanitize_dump_file(tmp_path / "missing.sql")  # must not raise


# ---------------------------------------------------------------------------
# core.image_cache_manager
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal async Redis double used by the cache manager."""

    def __init__(self):
        self.store: dict = {}
        self.get = AsyncMock(side_effect=self._get)
        self.set = AsyncMock(side_effect=self._set)
        self.delete = AsyncMock(side_effect=self._delete)
        self.exists = AsyncMock(side_effect=self._exists)
        self.incr = AsyncMock(return_value=1)
        self.decr = AsyncMock(return_value=0)

    async def _get(self, key):
        return self.store.get(key)

    async def _set(self, key, value, ex=None):
        self.store[key] = value
        return True

    async def _delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
        return removed

    async def _exists(self, key):
        return 1 if key in self.store else 0

    async def scan_iter(self, pattern="*"):
        prefix = pattern.rstrip("*")
        for key in list(self.store):
            if key.startswith(prefix):
                yield key


async def test_diagnose_cache_with_empty_metadata_value(tmp_path):
    """A metadata key that exists but carries an empty payload stays ``None``."""
    manager = ImageCacheManager(_FakeRedis())
    manager.cache_dir = tmp_path
    manager.redis.store[f"{manager.cache_metadata_prefix}K"] = b""

    result = await manager.diagnose_cache("K")

    assert result["metadata"]["exists"]
    assert result["metadata"]["data"] is None
    assert result["overall_status"] == "missing"


async def test_invalidate_generated_images_for_groups_without_cache_dir(tmp_path, monkeypatch):
    """Nothing to delete and no Redis client -> zero removals, no errors."""
    monkeypatch.setattr(icm_module, "MEDIA_PATH", tmp_path)
    assert not (tmp_path / "generated").exists()

    assert await invalidate_generated_images_for_groups(None, ["G1", ""]) == 0


async def test_invalidate_all_generated_images_without_cache_dir(tmp_path, monkeypatch):
    """The same for the "invalidate everything" helper."""
    monkeypatch.setattr(icm_module, "MEDIA_PATH", tmp_path)
    assert not (tmp_path / "generated").exists()

    assert await invalidate_all_generated_images() == 0


async def test_invalidate_all_generated_images_without_redis_client(tmp_path, monkeypatch):
    """Files are removed even when Redis is not configured."""
    monkeypatch.setattr(icm_module, "MEDIA_PATH", tmp_path)
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "G1_odd_ru.png").write_bytes(b"x")

    assert await invalidate_all_generated_images(None) == 1
    assert not (generated / "G1_odd_ru.png").exists()


# ---------------------------------------------------------------------------
# core.image_generator
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self):
        self.closed = False
        self._evaluate_calls = 0

    def set_default_timeout(self, timeout):
        pass

    def set_default_navigation_timeout(self, timeout):
        pass

    async def set_viewport_size(self, size):
        pass

    async def set_content(self, html, timeout=None):
        pass

    async def wait_for_load_state(self, *args, **kwargs):
        pass

    async def evaluate(self, script):
        self._evaluate_calls += 1
        return 1000 if self._evaluate_calls == 1 else {"wrapper": 1, "grid": 1, "lessons": 1}

    async def wait_for_selector(self, *args, **kwargs):
        pass

    async def screenshot(self, **kwargs):
        pass

    async def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, pages):
        self._pages = list(pages)

    async def new_page(self):
        return self._pages.pop(0)

    async def close(self):
        pass


class _FakeContext:
    def __init__(self, browser):
        self.chromium = self
        self._browser = browser

    async def launch(self, args=None):
        return self._browser

    async def stop(self):
        pass


class _FakePlaywright:
    def __init__(self, context):
        self._context = context

    async def start(self):
        return self._context


class _FakePoolState:
    """Stand-in for the dataclass that lives inside ``generate_schedule_image``."""

    def __init__(self, browser):
        self.lock = asyncio.Lock()
        self.ctx = _FakeContext(browser)
        self.browser = browser
        self.created_at = 0.0
        self.pages_made = 0
        self.last_check_at = 0.0


class _RaceLoop:
    """Event-loop double that simulates a race between two ``getattr`` calls.

    ``core.image_generator`` guards the creation of the init lock and of the
    browser pool with a double check (``getattr`` -> create -> ``getattr``).
    The branches behind those double checks are only reachable when another
    thread wins the race, which this double reproduces deterministically.
    """

    def __init__(self, init_lock_appears=True, pool_state=None):
        object.__setattr__(self, "_attrs", {})
        object.__setattr__(self, "_init_lock_calls", 0)
        object.__setattr__(self, "_pool_state_calls", 0)
        object.__setattr__(self, "_init_lock_appears", init_lock_appears)
        object.__setattr__(self, "_pool_state", pool_state)

    def __getattr__(self, name):
        attrs = object.__getattribute__(self, "_attrs")
        if name in attrs:
            return attrs[name]
        if name == "__img_init_async_lock__":
            calls = object.__getattribute__(self, "_init_lock_calls") + 1
            object.__setattr__(self, "_init_lock_calls", calls)
            if calls == 1 or not object.__getattribute__(self, "_init_lock_appears"):
                return None
            # Another thread created the lock between the two checks.
            return asyncio.Lock()
        if name == "__img_pool_state__":
            calls = object.__getattribute__(self, "_pool_state_calls") + 1
            object.__setattr__(self, "_pool_state_calls", calls)
            state = object.__getattribute__(self, "_pool_state")
            # The pool state appears only after the first lookups.
            return state if calls > 2 else None
        return None

    def __setattr__(self, name, value):
        if name.startswith("__img_"):
            object.__getattribute__(self, "_attrs")[name] = value
        else:
            object.__setattr__(self, name, value)


def _schedule():
    return {
        "Понедельник": [
            {
                "subject": "Математика",
                "type": "лек",
                "room": "101",
                "time": "09:00-10:30",
                "start_time_raw": "09:00",
            }
        ]
    }


@pytest.fixture(autouse=True)
def _reset_generator_globals():
    ig._template_cache = None
    ig._template_mtime = None
    ig._bg_images_cache.clear()
    yield
    ig._template_cache = None
    ig._template_mtime = None
    ig._bg_images_cache.clear()


async def test_generate_schedule_image_when_init_lock_appears_midway(monkeypatch, tmp_path):
    """Another thread created the init lock while we waited for it."""
    browser = _FakeBrowser([_FakePage()])
    monkeypatch.setattr(ig, "async_playwright", lambda: _FakePlaywright(_FakeContext(browser)))
    monkeypatch.setattr(ig.asyncio, "get_running_loop", lambda: _RaceLoop(init_lock_appears=True))

    ok = await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "out.png"))

    assert ok is True


async def test_generate_schedule_image_when_pool_state_appears_midway(monkeypatch, tmp_path):
    """Another worker created the browser pool while we waited for the lock."""
    browser = _FakeBrowser([_FakePage()])
    state = _FakePoolState(browser)
    monkeypatch.setattr(ig, "async_playwright", lambda: _FakePlaywright(_FakeContext(browser)))
    monkeypatch.setattr(ig.asyncio, "get_running_loop", lambda: _RaceLoop(pool_state=state))

    ok = await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "out.png"))

    assert ok is True
    assert state.pages_made == 1


class _ManagerRedis:
    """Минимальный Redis-дубль для TimetableManager."""

    async def get(self, *args, **kwargs):
        return None

    async def set(self, *args, **kwargs):
        return True

    async def delete(self, *args, **kwargs):
        return 1

    async def aclose(self):
        return None


def _manager_data():
    return {
        "__metadata__": {"period": {"StartYear": "2026", "StartMonth": "9", "StartDay": "1"}},
        "__teachers_index__": {
            "Иванов И.И.": [
                {"day": "Понедельник", "week_code": "0", "start_time_raw": "09:00", "subject": "Математика"},
            ]
        },
        "__classrooms_index__": {
            "101": [
                {"day": "Понедельник", "week_code": "0", "start_time_raw": "09:00", "subject": "Математика"},
            ]
        },
        "__current_xml_hash__": "hash-1",
        "ГРУППА1": {"odd": {"Понедельник": [{"start_time_raw": "09:00", "subject": "Математика"}]}, "even": {}},
    }


async def test_get_classroom_schedule_skips_lessons_of_other_days():
    """Lessons stored for another weekday are skipped by the classroom lookup."""
    manager = TimetableManager(_manager_data(), _ManagerRedis())

    monday = await manager.get_classroom_schedule("101", date(2026, 9, 7))
    assert [lesson["subject"] for lesson in monday["lessons"]] == ["Математика"]

    tuesday = await manager.get_classroom_schedule("101", date(2026, 9, 8))
    assert tuesday["lessons"] == []
    assert tuesday["day_name"] == "Вторник"

"""Coverage tests for ``core.image_generator``.

Playwright is fully faked (no browser is ever launched), assets are real files
from the repository, and the Jinja2 template is the real one.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import core.image_generator as ig

# ---------------------------------------------------------------------------
# Playwright fakes
# ---------------------------------------------------------------------------


class FakePage:
    def __init__(self, height=1200, elements=None, close_exc=False, selector_exc=None, screenshot_exc=False):
        self.height = height
        self.elements = elements or {"wrapper": 1, "grid": 1, "lessons": 1}
        self.close_exc = close_exc
        self.selector_exc = selector_exc
        self.screenshot_exc = screenshot_exc
        self.closed = False
        self._evaluate_calls = 0
        self.viewports = []
        self.screenshot_paths = []

    def set_default_timeout(self, timeout):
        pass

    def set_default_navigation_timeout(self, timeout):
        pass

    async def set_viewport_size(self, size):
        self.viewports.append(size)

    async def set_content(self, html, timeout=None):
        pass

    async def wait_for_load_state(self, *args, **kwargs):
        pass

    async def evaluate(self, script):
        self._evaluate_calls += 1
        return self.height if self._evaluate_calls == 1 else self.elements

    async def wait_for_selector(self, *args, **kwargs):
        if self.selector_exc:
            raise self.selector_exc

    async def screenshot(self, **kwargs):
        if self.screenshot_exc:
            raise self.screenshot_exc
        self.screenshot_paths.append(kwargs.get("path"))

    async def close(self):
        self.closed = True
        if self.close_exc:
            raise RuntimeError("page close failed")


class _FakeTestContext:
    async def close(self):
        pass


class FakeBrowser:
    def __init__(self, pages, new_page_exc=None, close_exc=False):
        self._pages = list(pages)
        self._new_page_exc = new_page_exc
        self.close_exc = close_exc
        self.closed = False
        self.new_context_exc = None

    async def new_page(self):
        if self._new_page_exc:
            raise self._new_page_exc
        item = self._pages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def new_context(self):
        if self.new_context_exc:
            raise self.new_context_exc
        return _FakeTestContext()

    async def close(self):
        self.closed = True
        if self.close_exc:
            raise RuntimeError("browser close failed")


class FakeContext:
    def __init__(self, browser, launch_exc=None, stop_exc=False):
        self._browser = browser
        self._launch_exc = launch_exc
        self.stop_exc = stop_exc
        self.stopped = False
        self.chromium = self

    async def launch(self, args=None):
        if self._launch_exc:
            raise self._launch_exc
        return self._browser

    async def stop(self):
        self.stopped = True
        if self.stop_exc:
            raise RuntimeError("ctx stop failed")


class _FakePlaywright:
    def __init__(self, context):
        self._context = context

    async def start(self):
        return self._context


class _PWFactory:
    def __init__(self, contexts):
        self._contexts = list(contexts)

    def __call__(self):
        return _FakePlaywright(self._contexts.pop(0))


class _LoopProxy:
    """A fake event loop that never stores attributes (forces re-init paths)."""

    def __setattr__(self, name, value):
        pass

    def __getattr__(self, name):
        return None


class _MutatingLock:
    """A lock whose ``__aenter__`` refreshes ``state.last_check_at``."""

    def __init__(self, holder):
        self._holder = holder

    async def __aenter__(self):
        self._holder[0].last_check_at = ig.time.time()
        return self

    async def __aexit__(self, *args):
        return False


_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_GENERATOR_FILE = str(_REPO_ROOT / "core" / "image_generator.py")


@pytest.fixture(autouse=True)
def _reset_globals():
    # Some legacy tests mutate ``image_generator.__file__`` / ``async_playwright``
    # globally without restoring them. Reset the module state so these tests are
    # order-independent.
    ig.__file__ = _REAL_GENERATOR_FILE
    ig._template_cache = None
    ig._template_mtime = None
    ig._bg_images_cache.clear()
    yield
    ig.__file__ = _REAL_GENERATOR_FILE
    ig._template_cache = None
    ig._template_mtime = None
    ig._bg_images_cache.clear()


def _schedule():
    return {
        "Понедельник": [
            {"subject": "Математика", "type": "лек", "room": "101", "time": "09:00-10:30", "start_time_raw": "09:00"},
            {"subject": "Физика", "type": "", "room": "кабинет не указан", "time": "10:40-12:10", "start_time_raw": "10:40"},
            {"subject": "Химия", "room": "", "time": "12:20-13:50", "start_time_raw": "12:20"},
        ],
        "Среда": [
            {"subject": "Информатика", "type": "пр", "room": "202", "time": "09:00-10:30", "start_time_raw": "09:00"},
        ],
    }


def _install(monkeypatch, *contexts):
    monkeypatch.setattr(ig, "async_playwright", _PWFactory(list(contexts)))


# ---------------------------------------------------------------------------
# simple helpers
# ---------------------------------------------------------------------------


def test_print_progress_bar(capsys):
    ig.print_progress_bar(1, 5, "prefix", "suffix")
    ig.print_progress_bar(5, 5, "prefix", "done")
    out = capsys.readouterr().out
    assert "done" in out


def test_resolve_week_slug():
    assert ig.resolve_week_slug("Нечетная") == "odd"
    assert ig.resolve_week_slug("Четная") == "even"
    assert ig.resolve_week_slug(None) == "even"
    assert ig.resolve_week_slug("Нечетная", "even") == "even"
    assert ig.resolve_week_slug("Четная", "odd") == "odd"


def test_time_to_minutes():
    assert ig._time_to_minutes("09:30") == 570
    assert ig._time_to_minutes("bad") == 24 * 60 + 59


def test_prepare_days_basic():
    days = ig._prepare_days(_schedule(), lang="ru")
    names = [d["name"] for d in days]
    assert "Понедельник" in names
    monday = next(d for d in days if d["name"] == "Понедельник")
    assert len(monday["lessons"]) == 3
    assert monday["lessons"][0]["room"] == "101"
    # Room "кабинет не указан" and empty room become "".
    assert monday["lessons"][1]["room"] == ""
    assert monday["lessons"][2]["room"] == ""
    assert monday["firstStart"] == "09:00"
    # Sunday is skipped entirely.
    assert "Воскресенье" not in names


def test_prepare_days_transliterated():
    days = ig._prepare_days(_schedule(), lang="en")
    monday = next(d for d in days if d["name"] not in ("Понедельник",))
    assert monday["lessons"][0]["room"].isascii()


def test_prepare_days_sort_exception():
    class FlakyDict(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.calls = 0

        def get(self, key, default=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return super().get(key, default)

    lesson = FlakyDict({"subject": "S", "type": "t", "room": "1", "time": "09:00-10:30", "start_time_raw": "09:00"})
    days = ig._prepare_days({"Понедельник": [lesson]}, lang="ru")
    monday = next(d for d in days if d["name"] == "Понедельник")
    assert monday["firstStart"] == ""


# ---------------------------------------------------------------------------
# generate_schedule_image - success paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_success_standard_theme(monkeypatch, tmp_path):
    page = FakePage(height=1200)
    browser = FakeBrowser([page])
    _install(monkeypatch, FakeContext(browser))
    out = str(tmp_path / "out.png")

    ok = await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out, user_theme="standard", lang="ru")
    assert ok is True
    assert page.screenshot_paths == [out]
    # Height gets adjusted to content + 100.
    assert page.viewports[-1]["height"] == 1300


@pytest.mark.asyncio
async def test_generate_success_even_purple_and_zh(monkeypatch, tmp_path):
    page = FakePage(height=800)
    browser = FakeBrowser([page])
    _install(monkeypatch, FakeContext(browser))
    out = str(tmp_path / "out.png")

    ok = await ig.generate_schedule_image(_schedule(), "Четная", "G1", out, user_theme=None, lang="zh", week_key="even")
    assert ok is True


@pytest.mark.asyncio
async def test_generate_selector_exception_is_ignored(monkeypatch, tmp_path):
    page = FakePage(height=900, selector_exc=RuntimeError("selector missing"))
    browser = FakeBrowser([page])
    _install(monkeypatch, FakeContext(browser))
    ok = await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "o.png"))
    assert ok is True


@pytest.mark.asyncio
async def test_generate_template_reload_on_mtime_change(monkeypatch, tmp_path):
    browser = FakeBrowser([FakePage(height=900), FakePage(height=900)])
    _install(monkeypatch, FakeContext(browser))
    out = str(tmp_path / "o.png")

    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out) is True
    # Force a different mtime so the template is reloaded.
    ig._template_mtime = -12345.0
    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out) is True


@pytest.mark.asyncio
async def test_generate_template_stat_failure(monkeypatch, tmp_path):
    page = FakePage(height=900)
    browser = FakeBrowser([page])
    _install(monkeypatch, FakeContext(browser))

    real_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        if self.name == "schedule_template.html":
            raise OSError("no stat")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "o.png")) is True


@pytest.mark.asyncio
async def test_generate_playwright_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ig, "async_playwright", None)
    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "o.png")) is False


# ---------------------------------------------------------------------------
# background image resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_bg_space_odd_week_specific(monkeypatch, tmp_path):
    page = FakePage(height=900)
    _install(monkeypatch, FakeContext(FakeBrowser([page])))
    ok = await ig.generate_schedule_image(
        _schedule(), "Нечетная", "G1", str(tmp_path / "o.png"), user_theme="space", week_key="odd"
    )
    assert ok is True
    assert "space_odd" in ig._bg_images_cache


@pytest.mark.asyncio
async def test_generate_bg_dark_no_week_variant_and_cache_hit(monkeypatch, tmp_path):
    _install(monkeypatch, FakeContext(FakeBrowser([FakePage(height=900), FakePage(height=900)])))
    out = str(tmp_path / "o.png")
    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out, user_theme="dark") is True
    assert "dark" in ig._bg_images_cache
    # Second call hits the cache.
    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out, user_theme="dark") is True


@pytest.mark.asyncio
async def test_generate_bg_read_failure(monkeypatch, tmp_path):
    page = FakePage(height=900)
    _install(monkeypatch, FakeContext(FakeBrowser([page])))

    real_read = Path.read_bytes

    def fake_read(self):
        if self.name == "light_background.png":
            raise RuntimeError("cannot read")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", fake_read)
    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "o.png"), user_theme="light") is True
    assert ig._bg_images_cache["light"] == ""


@pytest.mark.asyncio
async def test_generate_bg_missing_asset(monkeypatch, tmp_path):
    page = FakePage(height=900)
    _install(monkeypatch, FakeContext(FakeBrowser([page])))

    from core.themes import ThemeMeta

    monkeypatch.setattr(
        "core.themes.get_theme_meta",
        lambda theme_id: ThemeMeta(id="ghost", name_key="n", desc_key="d", bg_image_key="ghost"),
    )
    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "o.png"), user_theme="ghost") is True
    assert ig._bg_images_cache["ghost"] == ""


# ---------------------------------------------------------------------------
# generate_schedule_image - error / retry paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_retry_after_zero_height(monkeypatch, tmp_path):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(ig.asyncio, "sleep", _no_sleep)

    page1 = FakePage(height=0, close_exc=True)
    page2 = FakePage(height=1000)
    browser = FakeBrowser([page1, page2], close_exc=True)
    ctx = FakeContext(browser, stop_exc=True)
    _install(monkeypatch, ctx)

    ok = await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "o.png"))
    assert ok is True
    assert page1.closed is True
    assert page2.screenshot_paths


@pytest.mark.asyncio
async def test_generate_all_attempts_fail(monkeypatch, tmp_path, capsys):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(ig.asyncio, "sleep", _no_sleep)

    pages = [FakePage(height=0, close_exc=True) for _ in range(5)]
    browser = FakeBrowser(pages)
    _install(monkeypatch, FakeContext(browser))

    ok = await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "o.png"))
    assert ok is False
    assert all(p.closed for p in pages)


@pytest.mark.asyncio
async def test_generate_new_page_failure(monkeypatch, tmp_path):
    browser = FakeBrowser([], new_page_exc=RuntimeError("cannot create page"))
    _install(monkeypatch, FakeContext(browser))
    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "o.png")) is False


@pytest.mark.asyncio
async def test_generate_launch_failure(monkeypatch, tmp_path):
    ctx = FakeContext(FakeBrowser([]), launch_exc=RuntimeError("launch failed"))
    _install(monkeypatch, ctx)
    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "o.png")) is False
    assert ctx.stopped is True


@pytest.mark.asyncio
async def test_generate_browser_rotation(monkeypatch, tmp_path):
    page_pre = FakePage(height=900)
    browser_a = FakeBrowser([page_pre])
    ctx_a = FakeContext(browser_a)

    page_main = FakePage(height=900)
    browser_new = FakeBrowser([page_main])
    ctx_new = FakeContext(browser_new)

    _install(monkeypatch, ctx_a, ctx_new)
    out = str(tmp_path / "o.png")

    # First run creates the pool state.
    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out) is True

    loop = asyncio.get_running_loop()
    state = getattr(loop, "__img_pool_state__")
    state.last_check_at = 0
    state.created_at = 0
    browser_a.new_context_exc = RuntimeError("health check failed")
    browser_a.close_exc = True
    ctx_a.stop_exc = True

    # Second run triggers health-check failure + rotation.
    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out) is True
    assert state.browser is browser_new
    assert state.pages_made == 1


@pytest.mark.asyncio
async def test_generate_rotation_recreate_failure(monkeypatch, tmp_path):
    page_pre = FakePage(height=900)
    browser_a = FakeBrowser([page_pre])
    ctx_a = FakeContext(browser_a)

    ctx_new = FakeContext(FakeBrowser([]), launch_exc=RuntimeError("cannot recreate"))
    _install(monkeypatch, ctx_a, ctx_new)
    out = str(tmp_path / "o.png")

    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out) is True

    loop = asyncio.get_running_loop()
    state = getattr(loop, "__img_pool_state__")
    state.last_check_at = 0
    state.created_at = 0
    browser_a.new_context_exc = RuntimeError("health check failed")

    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out) is False
    assert ctx_new.stopped is True


@pytest.mark.asyncio
async def test_generate_recent_health_check_skip(monkeypatch, tmp_path):
    browser = FakeBrowser([FakePage(height=900), FakePage(height=900)])
    _install(monkeypatch, FakeContext(browser))
    out = str(tmp_path / "o.png")

    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out) is True
    loop = asyncio.get_running_loop()
    state = getattr(loop, "__img_pool_state__")
    state.last_check_at = ig.time.time()  # recent -> skip health check
    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out) is True


@pytest.mark.asyncio
async def test_generate_double_check_inside_lock(monkeypatch, tmp_path):
    browser = FakeBrowser([FakePage(height=900), FakePage(height=900)])
    _install(monkeypatch, FakeContext(browser))
    out = str(tmp_path / "o.png")

    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out) is True

    loop = asyncio.get_running_loop()
    state = getattr(loop, "__img_pool_state__")
    state.last_check_at = 0
    holder = [state]
    state.lock = _MutatingLock(holder)

    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", out) is True


@pytest.mark.asyncio
async def test_generate_loop_proxy_forces_page_semaphore_reinit(monkeypatch, tmp_path):
    page = FakePage(height=900)
    browser = FakeBrowser([page])
    _install(monkeypatch, FakeContext(browser))
    monkeypatch.setattr(ig.asyncio, "get_running_loop", lambda: _LoopProxy())

    assert await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "o.png")) is True


# ---------------------------------------------------------------------------
# shutdown_image_generator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_no_state():
    await ig.shutdown_image_generator()  # nothing to do


@pytest.mark.asyncio
async def test_shutdown_closes_state(monkeypatch, tmp_path):
    page = FakePage(height=900)
    browser = FakeBrowser([page])
    ctx = FakeContext(browser)
    _install(monkeypatch, ctx)
    await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "o.png"))

    await ig.shutdown_image_generator()
    loop = asyncio.get_running_loop()
    assert getattr(loop, "__img_pool_state__") is None
    assert browser.closed is True
    assert ctx.stopped is True


@pytest.mark.asyncio
async def test_shutdown_ignores_close_errors(monkeypatch, tmp_path):
    page = FakePage(height=900)
    browser = FakeBrowser([page], close_exc=True)
    ctx = FakeContext(browser, stop_exc=True)
    _install(monkeypatch, ctx)
    await ig.generate_schedule_image(_schedule(), "Нечетная", "G1", str(tmp_path / "o.png"))

    await ig.shutdown_image_generator()
    assert getattr(asyncio.get_running_loop(), "__img_pool_state__") is None


@pytest.mark.asyncio
async def test_shutdown_outer_exception(monkeypatch):
    def _boom():
        raise RuntimeError("no loop")

    monkeypatch.setattr(ig.asyncio, "get_running_loop", _boom)
    await ig.shutdown_image_generator()  # swallowed


# ---------------------------------------------------------------------------
# import fallback
# ---------------------------------------------------------------------------


def test_image_generator_import_error_branch(monkeypatch):
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    try:
        importlib.reload(ig)
        assert ig.async_playwright is None
    finally:
        monkeypatch.undo()
        importlib.reload(ig)
    assert ig.async_playwright is not None

"""Full statement coverage tests for bot/utils/worker_loop.py."""

import asyncio
import concurrent.futures
import threading
from unittest.mock import MagicMock

import pytest

import bot.utils.worker_loop as wl


class _FakeThread:
    """Thread double that never actually runs the target."""

    instances = []

    def __init__(self, target, args, daemon, name):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.name = name
        self.started = False
        _FakeThread.instances.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return True

    def join(self, timeout=None):
        return None


class _DeadThread:
    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


def _fake_loop(closed=False):
    loop = MagicMock()
    loop.is_closed.return_value = closed
    return loop


class TestGetWorkerLoop:
    def test_creates_loop_and_thread(self, monkeypatch):
        _FakeThread.instances.clear()
        monkeypatch.setattr(wl, "_worker_loop", None)
        monkeypatch.setattr(wl, "_worker_loop_thread", None)
        loop = _fake_loop()
        monkeypatch.setattr(wl.asyncio, "new_event_loop", lambda: loop)
        monkeypatch.setattr(wl.threading, "Thread", _FakeThread)

        result = wl.get_worker_loop()
        assert result is loop
        assert _FakeThread.instances[-1].started is True

    def test_reuses_existing_live_loop(self, monkeypatch):
        monkeypatch.setattr(wl, "_worker_loop", _fake_loop())
        monkeypatch.setattr(wl, "_worker_loop_thread", _FakeThread(lambda: None, (), True, "t"))
        called = {"n": 0}

        def _new_loop():
            called["n"] += 1
            return _fake_loop()

        monkeypatch.setattr(wl.asyncio, "new_event_loop", _new_loop)
        loop = wl.get_worker_loop()
        assert called["n"] == 0
        assert loop is wl._worker_loop

    def test_recreates_closed_loop(self, monkeypatch):
        _FakeThread.instances.clear()
        monkeypatch.setattr(wl, "_worker_loop", _fake_loop(closed=True))
        monkeypatch.setattr(wl, "_worker_loop_thread", _FakeThread(lambda: None, (), True, "t"))
        new_loop = _fake_loop()
        monkeypatch.setattr(wl.asyncio, "new_event_loop", lambda: new_loop)
        monkeypatch.setattr(wl.threading, "Thread", _FakeThread)

        result = wl.get_worker_loop()
        assert result is new_loop

    def test_dead_thread_restarts_loop(self, monkeypatch):
        # The production code re-enters get_worker_loop while holding a
        # non-reentrant threading.Lock; use an RLock so the restart can proceed.
        monkeypatch.setattr(wl, "_lock", threading.RLock())
        _FakeThread.instances.clear()
        monkeypatch.setattr(wl, "_worker_loop", _fake_loop())
        monkeypatch.setattr(wl, "_worker_loop_thread", _DeadThread())
        new_loop = _fake_loop()
        monkeypatch.setattr(wl.asyncio, "new_event_loop", lambda: new_loop)
        monkeypatch.setattr(wl.threading, "Thread", _FakeThread)

        result = wl.get_worker_loop()
        assert result is new_loop


class TestRunLoopBody:
    def test_run_loop_exception_is_logged_and_loop_closed(self, monkeypatch):
        captured = {}

        class _CapturingThread(_FakeThread):
            def __init__(self, target, args, daemon, name):
                super().__init__(target, args, daemon, name)
                captured["target"] = target

        monkeypatch.setattr(wl, "_worker_loop", None)
        monkeypatch.setattr(wl, "_worker_loop_thread", None)
        loop = _fake_loop()
        loop.run_forever.side_effect = RuntimeError("boom")
        monkeypatch.setattr(wl.asyncio, "new_event_loop", lambda: loop)
        monkeypatch.setattr(wl.asyncio, "set_event_loop", lambda l: None)
        monkeypatch.setattr(wl.threading, "Thread", _CapturingThread)

        wl.get_worker_loop()
        captured["target"](loop)  # execute the nested run_loop synchronously
        loop.close.assert_called_once()


class TestStopWorkerLoop:
    def test_stop_running_loop(self, monkeypatch):
        loop = MagicMock()
        loop.is_running.return_value = True
        monkeypatch.setattr(wl, "_worker_loop", loop)
        wl.stop_worker_loop()
        loop.call_soon_threadsafe.assert_called_once_with(loop.stop)

    def test_stop_when_not_running(self, monkeypatch):
        loop = MagicMock()
        loop.is_running.return_value = False
        monkeypatch.setattr(wl, "_worker_loop", loop)
        wl.stop_worker_loop()
        loop.call_soon_threadsafe.assert_not_called()

    def test_stop_when_none(self, monkeypatch):
        monkeypatch.setattr(wl, "_worker_loop", None)
        wl.stop_worker_loop()  # no exception


class TestRunAsync:
    def test_success(self, monkeypatch):
        future = concurrent.futures.Future()
        future.set_result("done")
        monkeypatch.setattr(wl.asyncio, "run_coroutine_threadsafe", lambda coro, loop: future)
        monkeypatch.setattr(wl, "get_worker_loop", lambda: MagicMock())
        assert wl.run_async(MagicMock()) == "done"

    def test_timeout(self, monkeypatch):
        future = concurrent.futures.Future()
        future.set_exception(asyncio.TimeoutError())
        monkeypatch.setattr(wl.asyncio, "run_coroutine_threadsafe", lambda coro, loop: future)
        monkeypatch.setattr(wl, "get_worker_loop", lambda: MagicMock())
        with pytest.raises(asyncio.TimeoutError):
            wl.run_async(MagicMock())

    def test_generic_exception(self, monkeypatch):
        future = concurrent.futures.Future()
        future.set_exception(ValueError("nope"))
        monkeypatch.setattr(wl.asyncio, "run_coroutine_threadsafe", lambda coro, loop: future)
        monkeypatch.setattr(wl, "get_worker_loop", lambda: MagicMock())
        with pytest.raises(ValueError):
            wl.run_async(MagicMock())

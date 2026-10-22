"""Тесты конфигурации брокера Dramatiq (не зависят от локального .env)."""

from unittest.mock import MagicMock, patch

import pytest
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.brokers.stub import StubBroker
from dramatiq.encoder import JSONEncoder

from bot.tasks import (
    RobustRetries,
    _configure_broker_middleware,
    _create_broker,
    _create_noop_broker,
    _harden_broker_url,
    _setup_broker,
)


def test_create_broker_without_url_uses_stub():
    broker = _create_broker(None)
    assert isinstance(broker, StubBroker)
    assert isinstance(broker.encoder, JSONEncoder)
    assert any(isinstance(m, RobustRetries) for m in broker.middleware)


def test_create_broker_with_url_uses_rabbitmq():
    broker = _create_broker("amqp://guest:guest@localhost:5672/")
    assert isinstance(broker, RabbitmqBroker)
    assert isinstance(broker.encoder, JSONEncoder)
    assert any(isinstance(m, RobustRetries) for m in broker.middleware)


def test_create_broker_falls_back_to_noop(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "dramatiq.brokers.stub":
            raise ImportError("no stub")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    broker = _create_broker(None)

    assert isinstance(broker.encoder, JSONEncoder)
    assert broker.declare_actor(MagicMock()) is None
    assert broker.enqueue(MagicMock()) is None


def test_create_noop_broker_directly():
    broker = _create_noop_broker()
    assert isinstance(broker.encoder, JSONEncoder)


def test_configure_middleware_is_idempotent():
    broker = StubBroker()
    _configure_broker_middleware(broker)
    _configure_broker_middleware(broker)
    limits = [m for m in broker.middleware if type(m).__name__ == "TimeLimit"]
    assert len(limits) == 1


def test_configure_middleware_survives_broken_add():
    broker = MagicMock()
    broker.middleware = []
    broker.add_middleware.side_effect = [None, RuntimeError("boom"), None]
    _configure_broker_middleware(broker)


def test_harden_broker_url_adds_defaults():
    url, changed = _harden_broker_url("amqp://guest:guest@localhost:5672/")
    assert changed is True
    assert "heartbeat=600" in url
    assert "socket_timeout=20" in url
    assert "connection_attempts=10" in url


def test_harden_broker_url_keeps_explicit_values():
    original = "amqp://guest:guest@localhost:5672/?heartbeat=30"
    url, changed = _harden_broker_url(original)
    assert changed is True
    assert "heartbeat=30" in url


def test_harden_broker_url_noop_when_complete():
    original = (
        "amqp://guest:guest@localhost:5672/"
        "?heartbeat=600&blocked_connection_timeout=600&connection_attempts=10&retry_delay=5&socket_timeout=20"
    )
    url, changed = _harden_broker_url(original)
    assert changed is False
    assert url == original


@pytest.mark.asyncio
async def test_robust_retries_backoff_on_connection_error():
    broker = MagicMock()
    message = MagicMock()
    message.options = {"retries": 2}
    exception = RuntimeError("Connection lost by server")

    RobustRetries(max_retries=5, min_backoff=2000, max_backoff=30000).after_process_message(
        broker, message, exception=exception
    )

    broker.enqueue.assert_called_once()
    assert message.options["retries"] == 1


def test_setup_broker_without_url():
    broker, url = _setup_broker(None)
    assert url is None
    assert broker is not None


def test_setup_broker_hardens_url():
    broker, url = _setup_broker("amqp://guest:guest@localhost:5672/")
    assert url is not None
    assert "heartbeat=600" in url
    assert broker is not None


def test_setup_broker_survives_hardening_error():
    with patch("bot.tasks._harden_broker_url", side_effect=RuntimeError("boom")):
        broker, url = _setup_broker("amqp://guest:guest@localhost:5672/")
    assert url == "amqp://guest:guest@localhost:5672/"
    assert broker is not None


def test_setup_broker_keeps_already_hardened_url():
    original = (
        "amqp://guest:guest@localhost:5672/"
        "?heartbeat=600&blocked_connection_timeout=600&connection_attempts=10&retry_delay=5&socket_timeout=20"
    )
    broker, url = _setup_broker(original)
    assert url == original
    assert broker is not None


def test_setup_broker_survives_close_error():
    first = MagicMock()
    first.url = "amqp://old"
    first.close.side_effect = RuntimeError("already closed")
    second = MagicMock()
    second.url = "amqp://new"

    with patch("bot.tasks._create_broker", side_effect=[first, second]):
        broker, url = _setup_broker("amqp://guest:guest@localhost:5672/")

    assert url is not None
    assert broker is second

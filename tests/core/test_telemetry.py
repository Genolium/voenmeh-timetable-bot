import logging

import pytest

from core import telemetry as telemetry_module
from core.telemetry import setup_telemetry, shutdown_telemetry


def test_setup_telemetry_builds_providers(monkeypatch):
    monkeypatch.setattr(telemetry_module, "_is_endpoint_reachable", lambda *args, **kwargs: True)

    handles = setup_telemetry(
        service_name="test-service",
        environment="test",
        endpoint="http://localhost:4318",
        headers="x-test-header=value",
    )

    assert handles is not None
    assert handles.tracer_provider is not None
    assert handles.meter_provider is not None
    assert handles.logger_provider is not None
    assert handles.log_handler is not None

    shutdown_telemetry(handles)


def test_setup_telemetry_returns_none_when_disabled():
    handles = setup_telemetry(
        service_name="test-service",
        enable_traces=False,
        enable_metrics=False,
        enable_logs=False,
    )

    assert handles is None


def test_setup_telemetry_returns_none_when_endpoint_unreachable(monkeypatch):
    monkeypatch.setattr(telemetry_module, "_is_endpoint_reachable", lambda *args, **kwargs: False)

    handles = setup_telemetry(
        service_name="test-service",
        endpoint="http://unreachable-endpoint:4318",
    )

    assert handles is None


def test_shutdown_telemetry_accepts_none():
    # Should not raise even if handles is None
    shutdown_telemetry(None)


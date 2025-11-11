from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlparse

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

try:
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
except ImportError:  # pragma: no cover
    LoggingInstrumentor = None  # type: ignore[assignment]

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class TelemetryHandles:
    tracer_provider: Optional[TracerProvider]
    meter_provider: Optional[MeterProvider]
    logger_provider: Optional[LoggerProvider]
    log_handler: Optional[LoggingHandler]


def _parse_headers(raw_headers: Optional[str]) -> Dict[str, str]:
    if not raw_headers:
        return {}
    headers: Dict[str, str] = {}
    for item in raw_headers.split(","):
        if not item.strip() or "=" not in item:
            continue
        key, value = item.split("=", 1)
        headers[key.strip()] = value.strip()
    return headers


def _is_endpoint_reachable(endpoint: str, timeout: float = 2.0) -> bool:
    try:
        parsed = urlparse(endpoint)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def setup_telemetry(
    *,
    service_name: str,
    environment: Optional[str] = None,
    endpoint: Optional[str] = None,
    headers: Optional[str] = None,
    enable_traces: bool = True,
    enable_metrics: bool = True,
    enable_logs: bool = True,
) -> Optional[TelemetryHandles]:
    base_endpoint = endpoint or "http://otel-collector:4318"

    if endpoint is not None and not _is_endpoint_reachable(base_endpoint):
        LOG.warning("OTLP endpoint %s is unreachable; telemetry disabled.", base_endpoint)
        return None

    parsed_headers = _parse_headers(headers)
    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": environment or "production",
        }
    )

    tracer_provider: Optional[TracerProvider] = None
    meter_provider: Optional[MeterProvider] = None
    logger_provider: Optional[LoggerProvider] = None
    log_handler: Optional[LoggingHandler] = None

    try:
        if enable_traces:
            tracer_provider = TracerProvider(resource=resource)
            span_exporter = OTLPSpanExporter(
                endpoint=f"{base_endpoint}/v1/traces",
                headers=parsed_headers,
            )
            tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            trace.set_tracer_provider(tracer_provider)

        if enable_metrics:
            metric_exporter = OTLPMetricExporter(
                endpoint=f"{base_endpoint}/v1/metrics",
                headers=parsed_headers,
            )
            reader = PeriodicExportingMetricReader(metric_exporter)
            meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
            metrics.set_meter_provider(meter_provider)

        if enable_logs:
            logger_provider = LoggerProvider(resource=resource)
            log_exporter = OTLPLogExporter(
                endpoint=f"{base_endpoint}/v1/logs",
                headers=parsed_headers,
            )
            logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
            set_logger_provider(logger_provider)
            log_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)

        if any(component is not None for component in (tracer_provider, meter_provider, logger_provider)):
            if LoggingInstrumentor is not None:
                try:
                    LoggingInstrumentor().instrument(set_logging_format=False)
                except Exception:  # pragma: no cover
                    LOG.exception("Failed to instrument logging with OpenTelemetry")
            return TelemetryHandles(
                tracer_provider=tracer_provider,
                meter_provider=meter_provider,
                logger_provider=logger_provider,
                log_handler=log_handler,
            )
        return None

    except Exception as exc:  # pragma: no cover
        LOG.error("Failed to initialize OpenTelemetry: %s", exc, exc_info=True)
        return None


def shutdown_telemetry(handles: Optional[TelemetryHandles]) -> None:
    if handles is None:
        return

    if handles.tracer_provider:
        try:
            handles.tracer_provider.shutdown()
        except Exception:  # pragma: no cover
            LOG.exception("Error shutting down tracer provider")

    if handles.meter_provider:
        try:
            handles.meter_provider.shutdown()
        except Exception:  # pragma: no cover
            LOG.exception("Error shutting down meter provider")

    if handles.logger_provider:
        try:
            handles.logger_provider.shutdown()
        except Exception:  # pragma: no cover
            LOG.exception("Error shutting down logger provider")

    root_logger = logging.getLogger()
    if handles.log_handler and handles.log_handler in root_logger.handlers:
        root_logger.removeHandler(handles.log_handler)


import asyncio
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional

import aiohttp

from core.alert_sender import AlertSender
from core.config import MOSCOW_TZ


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class BusinessAlert:
    title: str
    message: str
    severity: AlertSeverity
    metric_name: str
    current_value: Any
    threshold: Any
    timestamp: datetime
    tags: Dict[str, Any]


class BusinessMetricsMonitor:
    def __init__(self):
        self.alert_cooldown: Dict[str, datetime] = {}
        self.cooldown_minutes = 30
        self.thresholds = {
            "user_activity_drop": 0.5,
            "cache_hit_rate": 0.8,
            "schedule_generation_time": 10.0,
            "notification_failure_rate": 0.1,
            "user_conversion_drop": 0.3,
        }
        self.settings = {
            "SLACK_WEBHOOK_URL": os.getenv("SLACK_WEBHOOK_URL"),
            "DISCORD_WEBHOOK_URL": os.getenv("DISCORD_WEBHOOK_URL"),
            "TELEGRAM_ALERT_BOT_TOKEN": os.getenv("TELEGRAM_ALERT_BOT_TOKEN"),
            "TELEGRAM_ALERT_CHAT_ID": os.getenv("TELEGRAM_ALERT_CHAT_ID"),
            "ALERT_WEBHOOK_URL": os.getenv("ALERT_WEBHOOK_URL"),
            "ALERT_WEBHOOK_API_KEY": os.getenv("ALERT_WEBHOOK_API_KEY"),
        }
        # Optional Prometheus HTTP API endpoint; if not set, checks are skipped
        self.prometheus_url = os.getenv("PROMETHEUS_URL")

    async def start_monitoring(self):
        try:
            while True:
                await self._check_business_metrics()
                await asyncio.sleep(300)
        except Exception as e:
            logging.error(f"Business monitoring crashed: {e}")

    _METRIC_RULES: tuple[tuple[str, str, str, AlertSeverity, str], ...] = (
        (
            "user_activity_drop",
            "sum(increase(bot_user_activity_daily_total[24h])) / (sum(increase(bot_user_activity_daily_total[7d])) / 7)",
            "<",
            AlertSeverity.WARNING,
            "User Activity Drop",
        ),
        (
            "cache_hit_rate",
            "sum(increase(bot_image_cache_hits_total[1h])) / "
            "(sum(increase(bot_image_cache_hits_total[1h])) + sum(increase(bot_image_cache_misses_total[1h])))",
            "<",
            AlertSeverity.WARNING,
            "Low Image Cache Hit Rate",
        ),
        (
            "schedule_generation_time",
            "histogram_quantile(0.95, sum(rate(bot_schedule_generation_duration_seconds_bucket[15m])) by (le))",
            ">",
            AlertSeverity.WARNING,
            "Slow Schedule Image Generation",
        ),
        (
            "notification_failure_rate",
            'sum(increase(bot_notification_delivery_total{delivery_status="failed"}[15m])) / '
            "sum(increase(bot_notification_delivery_total[15m]))",
            ">",
            AlertSeverity.CRITICAL,
            "High Notification Failure Rate",
        ),
        (
            "user_conversion_drop",
            'sum(increase(bot_user_conversion_total{conversion_stage="daily_use"}[24h])) / '
            'sum(increase(bot_user_conversion_total{conversion_stage="first_use"}[24h]))',
            "<",
            AlertSeverity.WARNING,
            "User Conversion Drop",
        ),
    )

    async def _query_prometheus_scalar(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        promql: str,
    ) -> Optional[float]:
        async with session.get(
            f"{base_url}/api/v1/query",
            params={"query": promql},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            payload = await resp.json()
        if not isinstance(payload, dict) or payload.get("status") != "success":
            return None
        result = payload.get("data", {}).get("result")
        if not isinstance(result, list) or not result or not isinstance(result[0], dict):
            return None
        value_pair = result[0].get("value")
        if not isinstance(value_pair, (list, tuple)) or len(value_pair) < 2:
            return None
        try:
            value = float(value_pair[1])
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    async def _check_business_metrics(self) -> None:
        if not self.prometheus_url:
            return
        base_url = self.prometheus_url.strip().rstrip("/")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{base_url}/api/v1/query",
                    params={"query": "up"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    if not isinstance(data, dict) or data.get("status") != "success":
                        await self._send_alert(
                            "Prometheus Query Failed",
                            "Error querying metrics",
                            AlertSeverity.CRITICAL,
                            "prometheus",
                            {},
                        )
                        return

                for metric_name, promql, comparator, severity, title in self._METRIC_RULES:
                    threshold = self.thresholds.get(metric_name)
                    if threshold is None:
                        continue
                    current_value = await self._query_prometheus_scalar(session, base_url, promql)
                    if current_value is None:
                        continue
                    breached = current_value < threshold if comparator == "<" else current_value > threshold
                    if breached:
                        await self._send_alert(
                            title,
                            f"{metric_name}={current_value:.3f} {comparator} threshold {threshold}",
                            severity,
                            metric_name,
                            {"current_value": current_value, "threshold": threshold},
                        )
        except Exception as e:
            logging.error(f"Metrics check failed: {e}")

    async def _send_alert(
        self,
        title: str,
        message: str,
        severity: AlertSeverity,
        metric_name: str,
        additional_data: Dict[str, Any],
    ) -> None:
        alert_key = f"{metric_name}_{severity.value}"
        now = datetime.now(MOSCOW_TZ)
        last = self.alert_cooldown.get(alert_key)
        if last and (now - last) < timedelta(minutes=self.cooldown_minutes):
            return
        self.alert_cooldown[alert_key] = now
        payload = {
            "title": title,
            "message": message,
            "severity": severity.value,
            "metric_name": metric_name,
            "timestamp": now.isoformat(),
            "tags": additional_data,
        }
        try:
            async with AlertSender(self.settings) as sender:
                await sender.send(payload)
        except Exception as e:
            logging.error(f"Alert send failed: {e}")


business_monitor = BusinessMetricsMonitor()


async def start_business_monitoring():
    await business_monitor.start_monitoring()

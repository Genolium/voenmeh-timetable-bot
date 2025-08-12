import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any
from dataclasses import dataclass
from enum import Enum
import os

# Keep imports for type hints; do not introspect metric internals
from core.metrics import (
    USER_ACTIVITY_DAILY, GROUP_POPULARITY, USER_CONVERSION,
    IMAGE_CACHE_HITS, IMAGE_CACHE_MISSES, IMAGE_CACHE_SIZE,
    SCHEDULE_GENERATION_TIME, NOTIFICATION_DELIVERY
)
from core.alert_sender import AlertSender

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

    async def _check_business_metrics(self):
        # Without Prometheus API, skip heavy checks to avoid errors
        if not self.prometheus_url:
            return
        # Here one could implement Prometheus API queries; skipped for now
        return

    async def _send_alert(self, title: str, message: str, severity: AlertSeverity, metric_name: str, additional_data: Dict[str, Any]):
        alert_key = f"{metric_name}_{severity.value}"
        now = datetime.now()
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

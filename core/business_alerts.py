import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any
from dataclasses import dataclass
from enum import Enum
import os

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
        # Настройки берём из окружения (если есть)
        self.settings = {
            "SLACK_WEBHOOK_URL": os.getenv("SLACK_WEBHOOK_URL"),
            "DISCORD_WEBHOOK_URL": os.getenv("DISCORD_WEBHOOK_URL"),
            "TELEGRAM_ALERT_BOT_TOKEN": os.getenv("TELEGRAM_ALERT_BOT_TOKEN"),
            "TELEGRAM_ALERT_CHAT_ID": os.getenv("TELEGRAM_ALERT_CHAT_ID"),
            "ALERT_WEBHOOK_URL": os.getenv("ALERT_WEBHOOK_URL"),
            "ALERT_WEBHOOK_API_KEY": os.getenv("ALERT_WEBHOOK_API_KEY"),
        }

    async def start_monitoring(self):
        try:
            while True:
                await self._check_business_metrics()
                await asyncio.sleep(300)
        except Exception as e:
            logging.error(f"Business monitoring crashed: {e}")

    async def _check_business_metrics(self):
        await self._check_cache_performance()
        await self._check_schedule_generation()
        await self._check_notification_delivery()

    async def _check_cache_performance(self):
        try:
            hits = IMAGE_CACHE_HITS._value.sum()
            misses = IMAGE_CACHE_MISSES._value.sum()
            if hits + misses > 0:
                hit_rate = hits / (hits + misses)
                if hit_rate < self.thresholds["cache_hit_rate"]:
                    await self._send_alert(
                        title="Низкая эффективность кэша",
                        message=f"Кэш-хит: {hit_rate:.1%} (порог {self.thresholds['cache_hit_rate']:.0%})",
                        severity=AlertSeverity.WARNING,
                        metric_name="cache_performance",
                        additional_data={"hit_rate": hit_rate, "hits": hits, "misses": misses, "threshold": self.thresholds["cache_hit_rate"]},
                    )
        except Exception as e:
            logging.error(f"Cache performance check error: {e}")

    async def _check_schedule_generation(self):
        try:
            week_sum = SCHEDULE_GENERATION_TIME._sum.get("week", 0)
            week_cnt = SCHEDULE_GENERATION_TIME._count.get("week", 0)
            if week_cnt:
                avg = week_sum / week_cnt
                if avg > self.thresholds["schedule_generation_time"]:
                    await self._send_alert(
                        title="Медленная генерация расписания",
                        message=f"Среднее время: {avg:.1f}с (порог {self.thresholds['schedule_generation_time']}с)",
                        severity=AlertSeverity.WARNING,
                        metric_name="schedule_generation",
                        additional_data={"avg_time": avg, "count": week_cnt, "threshold": self.thresholds["schedule_generation_time"]},
                    )
        except Exception as e:
            logging.error(f"Schedule generation check error: {e}")

    async def _check_notification_delivery(self):
        try:
            succ = NOTIFICATION_DELIVERY._value.get(("success", "schedule"), 0)
            fail = NOTIFICATION_DELIVERY._value.get(("failed", "schedule"), 0)
            total = succ + fail
            if total:
                rate = fail / total
                if rate > self.thresholds["notification_failure_rate"]:
                    await self._send_alert(
                        title="Высокая доля неудачных уведомлений",
                        message=f"Неудачных: {rate:.1%} (порог {self.thresholds['notification_failure_rate']:.0%})",
                        severity=AlertSeverity.WARNING,
                        metric_name="notification_delivery",
                        additional_data={"failure_rate": rate, "success": succ, "failed": fail, "total": total, "threshold": self.thresholds["notification_failure_rate"]},
                    )
        except Exception as e:
            logging.error(f"Notification delivery check error: {e}")

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

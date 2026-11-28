import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional, cast

import aiohttp

from core.config import MOSCOW_TZ


class AlertSender:
    """Мульти-канальный отправитель алертов (Slack/Discord/Telegram/HTTP)."""

    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings
        self.session: Optional[aiohttp.ClientSession] = None
        self.webhooks = {
            "slack": settings.get("SLACK_WEBHOOK_URL"),
            "discord": settings.get("DISCORD_WEBHOOK_URL"),
            "telegram": settings.get("TELEGRAM_ALERT_BOT_TOKEN"),
            "http": settings.get("ALERT_WEBHOOK_URL"),
        }
        self.severity_filters = {
            "slack": ["warning", "error", "critical"],
            "discord": ["warning", "error", "critical"],
            "telegram": ["error", "critical"],
            "http": ["info", "warning", "error", "critical"],
        }

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self) -> None:
        if self.session:
            await self.session.close()
            self.session = None

    async def send(self, alert_data: Dict[str, Any]) -> bool:
        if not self.session or getattr(self.session, "closed", False):
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        success = False
        severity = alert_data.get("severity", "info")
        try:
            if self.webhooks["slack"] and severity in self.severity_filters["slack"]:
                success |= await self._send_slack(alert_data)
            if self.webhooks["discord"] and severity in self.severity_filters["discord"]:
                success |= await self._send_discord(alert_data)
            if self.webhooks["telegram"] and severity in self.severity_filters["telegram"]:
                success |= await self._send_telegram(alert_data)
            if self.webhooks["http"] and severity in self.severity_filters["http"]:
                success |= await self._send_http(alert_data)
            return bool(success)
        except Exception as e:
            logging.error(f"Alert send error: {e}")
            return False

    async def _normalize_ctx(self, cm_or_coro):
        """Возвращает асинхронный контекстный менеджер независимо от того,
        вернул ли вызов post уже контекст или корутину (как в моках тестов)."""
        try:
            if asyncio.iscoroutine(cm_or_coro):
                return await cm_or_coro
            return cm_or_coro
        except Exception:
            return cm_or_coro

    async def _send_slack(self, alert_data: Dict[str, Any]) -> bool:
        try:
            emoji = {"info": "ℹ️", "warning": "⚠️", "error": "❗", "critical": "🚨"}.get(alert_data.get("severity", "info"), "ℹ️")
            blocks: list[Dict[str, Any]] = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{emoji} {alert_data.get('title', 'Alert')}"[:150],
                    },
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": str(alert_data.get("message", ""))[:3000]},
                },
            ]
            if alert_data.get("tags"):
                tags = "\n".join([f"• {k}: {v}" for k, v in alert_data["tags"].items()])
                blocks.append(
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*Details:*\n{tags}"[:3000]},
                    }
                )
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"{datetime.now(MOSCOW_TZ).isoformat()}",
                        }
                    ],
                }
            )
            payload = {"text": alert_data.get("title", "Alert"), "blocks": blocks}
            session = cast(aiohttp.ClientSession, self.session)
            cm = await self._normalize_ctx(session.post(str(self.webhooks["slack"]), json=payload))
            async with cm as r:
                return cast(bool, r.status == 200)
        except Exception as e:
            logging.warning("Slack alert send failed: %s", type(e).__name__)
            return False

    async def _send_discord(self, alert_data: Dict[str, Any]) -> bool:
        try:
            color = {"info": 0x3498DB, "warning": 0xF39C12, "error": 0xE67E22, "critical": 0xE74C3C}.get(
                alert_data.get("severity", "info"), 0x95A5A6
            )
            embed = {
                "title": str(alert_data.get("title", "Alert"))[:256],
                "description": str(alert_data.get("message", ""))[:4096],
                "color": color,
                "timestamp": datetime.now(MOSCOW_TZ).isoformat(),
            }
            if alert_data.get("tags"):
                embed["fields"] = [
                    {"name": str(k)[:256], "value": str(v)[:1024], "inline": True}
                    for k, v in list(alert_data["tags"].items())[:25]
                ]
            session = cast(aiohttp.ClientSession, self.session)
            cm = await self._normalize_ctx(session.post(str(self.webhooks["discord"]), json={"embeds": [embed]}))
            async with cm as r:
                return r.status in (200, 204)
        except Exception as e:
            logging.warning("Discord alert send failed: %s", type(e).__name__)
            return False

    async def _send_telegram(self, alert_data: Dict[str, Any]) -> bool:
        try:
            import html as html_lib

            chat_id = self.settings.get("TELEGRAM_ALERT_CHAT_ID")
            if not chat_id:
                return False
            emoji = {"info": "ℹ️", "warning": "⚠️", "error": "❗", "critical": "🚨"}.get(alert_data.get("severity", "info"), "ℹ️")
            title = html_lib.escape(str(alert_data.get("title", "Alert")))
            message = html_lib.escape(str(alert_data.get("message", "")))
            text = f"{emoji} <b>{title}</b>\n\n{message}"
            if alert_data.get("tags"):
                text += "\n\n" + "\n".join(
                    [f"• {html_lib.escape(str(k))}: {html_lib.escape(str(v))}" for k, v in alert_data["tags"].items()]
                )
            if len(text) > 4096:
                text = text[:4093] + "..."
            url = f"https://api.telegram.org/bot{self.webhooks['telegram']}/sendMessage"
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            session = cast(aiohttp.ClientSession, self.session)
            cm = await self._normalize_ctx(session.post(url, json=payload))
            async with cm as r:
                if r.status == 200:
                    data = await r.json()
                    return bool(data.get("ok"))
                return False
        except Exception as e:
            logging.warning("Telegram alert send failed: %s", type(e).__name__)
            return False

    async def _send_http(self, alert_data: Dict[str, Any]) -> bool:
        try:
            headers = {"Content-Type": "application/json"}
            api_key = self.settings.get("ALERT_WEBHOOK_API_KEY")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            session = cast(aiohttp.ClientSession, self.session)
            cm = await self._normalize_ctx(
                session.post(
                    str(self.settings.get("ALERT_WEBHOOK_URL")),
                    json=alert_data,
                    headers=headers,
                )
            )
            async with cm as r:
                return r.status in (200, 201, 202)
        except Exception as e:
            logging.warning("HTTP alert send failed: %s", type(e).__name__)
            return False

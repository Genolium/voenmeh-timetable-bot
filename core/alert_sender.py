import aiohttp
import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime

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
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def send(self, alert_data: Dict[str, Any]) -> bool:
        if not self.session:
            self.session = aiohttp.ClientSession()
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
            emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(alert_data.get("severity", "info"), "ℹ️")
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} {alert_data.get('title', 'Alert')}"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": alert_data.get("message", "")}},
            ]
            if alert_data.get("tags"):
                tags = "\n".join([f"• {k}: {v}" for k, v in alert_data["tags"].items()])
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Details:*\n{tags}"}})
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"{datetime.now().isoformat()}"}]})
            payload = {"text": alert_data.get("title", "Alert"), "blocks": blocks}
            cm = await self._normalize_ctx(self.session.post(self.webhooks["slack"], json=payload))
            async with cm as r:
                return r.status == 200
        except Exception:
            return False

    async def _send_discord(self, alert_data: Dict[str, Any]) -> bool:
        try:
            color = {"info": 0x3498db, "warning": 0xf39c12, "critical": 0xe74c3c}.get(alert_data.get("severity", "info"), 0x95a5a6)
            embed = {
                "title": alert_data.get("title", "Alert"),
                "description": alert_data.get("message", ""),
                "color": color,
                "timestamp": datetime.now().isoformat(),
            }
            if alert_data.get("tags"):
                embed["fields"] = [{"name": k, "value": str(v), "inline": True} for k, v in alert_data["tags"].items()]
            cm = await self._normalize_ctx(self.session.post(self.webhooks["discord"], json={"embeds": [embed]}))
            async with cm as r:
                return r.status in (200, 204)
        except Exception:
            return False

    async def _send_telegram(self, alert_data: Dict[str, Any]) -> bool:
        try:
            chat_id = self.settings.get("TELEGRAM_ALERT_CHAT_ID")
            if not chat_id:
                return False
            emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(alert_data.get("severity", "info"), "ℹ️")
            text = f"{emoji} <b>{alert_data.get('title', 'Alert')}</b>\n\n{alert_data.get('message', '')}"
            if alert_data.get("tags"):
                text += "\n\n" + "\n".join([f"• {k}: {v}" for k, v in alert_data["tags"].items()])
            url = f"https://api.telegram.org/bot{self.webhooks['telegram']}/sendMessage"
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            cm = await self._normalize_ctx(self.session.post(url, json=payload))
            async with cm as r:
                if r.status == 200:
                    data = await r.json()
                    return bool(data.get("ok"))
                return False
        except Exception:
            return False

    async def _send_http(self, alert_data: Dict[str, Any]) -> bool:
        try:
            headers = {"Content-Type": "application/json"}
            api_key = self.settings.get("ALERT_WEBHOOK_API_KEY")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            cm = await self._normalize_ctx(self.session.post(self.settings.get("ALERT_WEBHOOK_URL"), json=alert_data, headers=headers))
            async with cm as r:
                return r.status in (200, 201, 202)
        except Exception:
            return False

import asyncio
import hmac
import html
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from aiohttp import web
from sqlalchemy import text

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
MAX_ALERTS_PER_PAYLOAD = 50
READINESS_PROBE_TIMEOUT_SEC = 3.0


def format_alertmanager_message(payload: Dict[str, Any]) -> str:
    """Formats Alertmanager webhook payload into a readable message for admins."""
    raw_status = payload.get("status")
    status = str(raw_status if raw_status is not None else "unknown").upper()
    raw_alerts = payload.get("alerts")
    alerts = raw_alerts[:MAX_ALERTS_PER_PAYLOAD] if isinstance(raw_alerts, list) else []
    lines: List[str] = [f"ALERTMANAGER: {status} ({len(alerts)} alert(s))"]

    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        raw_labels = alert.get("labels")
        raw_annotations = alert.get("annotations")
        labels: Dict[str, Any] = raw_labels if isinstance(raw_labels, dict) else {}
        annotations: Dict[str, Any] = raw_annotations if isinstance(raw_annotations, dict) else {}
        name = labels.get("alertname", "unknown")
        severity = labels.get("severity", "unknown")
        startsAt = alert.get("startsAt", "")
        endsAt = alert.get("endsAt", "")
        desc = annotations.get("description") or annotations.get("summary") or ""
        src = labels.get("source") or labels.get("component") or ""

        lines.append(
            "\n".join(
                [
                    f"⚠️ {name} [{severity}]",
                    f"{desc}",
                    f"source={src}",
                    f"startsAt={startsAt}",
                    f"endsAt={endsAt}",
                ]
            )
        )
    return "\n\n".join(lines)


def create_alert_app(
    bot: Any,
    admin_ids: List[int],
    redis_client: Any = None,
    db_session_maker: Any = None,
) -> web.Application:
    """Создаёт aiohttp приложение с health checks и webhook для алертов."""
    app = web.Application(client_max_size=64 * 1024)
    logger = logging.getLogger(__name__)

    # --- Health Check Endpoints ---

    async def health_check(request: web.Request) -> web.Response:
        """Liveness probe - бот жив и отвечает."""
        return web.json_response(
            {
                "status": "ok",
                "service": "voenmeh-timetable-bot",
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        )

    async def readiness_check(request: web.Request) -> web.Response:
        """Readiness probe - все зависимости готовы к обработке запросов."""
        checks = {"bot": True, "redis": False, "database": False}
        details = {}

        # Проверка Redis
        if redis_client:
            try:
                await asyncio.wait_for(redis_client.ping(), timeout=READINESS_PROBE_TIMEOUT_SEC)
                checks["redis"] = True
            except Exception as e:
                logger.warning("Readiness Redis check failed: %s", e)
                details["redis_error"] = "unavailable"
        else:
            details["redis_error"] = "Redis client not provided"

        # Проверка Database
        if db_session_maker:
            try:

                async def _probe_db() -> None:
                    async with db_session_maker() as session:
                        await session.execute(text("SELECT 1"))

                await asyncio.wait_for(_probe_db(), timeout=READINESS_PROBE_TIMEOUT_SEC)
                checks["database"] = True
            except Exception as e:
                logger.warning("Readiness DB check failed: %s", e)
                details["database_error"] = "unavailable"
        else:
            details["database_error"] = "Database session maker not provided"

        all_ok = all(checks.values())
        status_code = 200 if all_ok else 503

        response_data: Dict[str, Any] = {
            "status": "ready" if all_ok else "not_ready",
            "checks": checks,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if details:
            response_data["details"] = details

        return web.json_response(response_data, status=status_code)

    # --- Alertmanager Webhook ---

    async def handle_alert(request: web.Request) -> web.Response:
        # Авторизация по ключу: Header "Authorization: Bearer <ALERT_WEBHOOK_API_KEY>"
        expected_key = os.getenv("ALERT_WEBHOOK_API_KEY")
        if expected_key:
            auth_header = request.headers.get("Authorization", "")
            token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
            if not token or not hmac.compare_digest(token.encode("utf-8"), expected_key.encode("utf-8")):
                return web.Response(status=401, text="unauthorized")

        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="invalid json")

        if not isinstance(payload, dict):
            return web.Response(status=400, text="invalid json")

        text_msg = html.escape(format_alertmanager_message(payload), quote=False)
        if len(text_msg) > TELEGRAM_MAX_MESSAGE_LENGTH:
            text_msg = text_msg[: TELEGRAM_MAX_MESSAGE_LENGTH - 3] + "..."

        # fan-out to admins sequentially (простая и надежная доставка)
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text_msg)
            except Exception as e:
                logger.warning("Failed to deliver alert to admin %s: %s", admin_id, e)

        return web.Response(status=200, text="ok")

    # Регистрация маршрутов
    app.router.add_get("/health", health_check)
    app.router.add_get("/readiness", readiness_check)
    app.router.add_post("/alerts", handle_alert)

    logger.info("Alert webhook app created with /health and /readiness endpoints")
    return app


async def run_alert_webhook_server(
    bot: Any,
    admin_ids: List[int],
    port: int = 8010,
    redis_client: Any = None,
    db_session_maker: Any = None,
) -> web.AppRunner:
    """Запускает HTTP-сервер для health checks и alertmanager webhook."""
    app = create_alert_app(
        bot,
        admin_ids,
        redis_client=redis_client,
        db_session_maker=db_session_maker,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    host = os.getenv("ALERT_WEBHOOK_HOST", "127.0.0.1")
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    logging.info(f"Health check server started on http://{host}:{port}/health")
    return runner

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from aiohttp import web
from sqlalchemy import text


def format_alertmanager_message(payload: Dict[str, Any]) -> str:
    """Formats Alertmanager webhook payload into a readable message for admins."""
    status = payload.get("status", "unknown").upper()
    alerts = payload.get("alerts", [])
    lines: List[str] = [f"ALERTMANAGER: {status} ({len(alerts)} alert(s))"]

    for alert in alerts:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
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
    bot,
    admin_ids: List[int],
    redis_client=None,
    db_session_maker=None,
) -> web.Application:
    """Создаёт aiohttp приложение с health checks и webhook для алертов."""
    app = web.Application()
    logger = logging.getLogger(__name__)

    # --- Health Check Endpoints ---

    async def health_check(request: web.Request) -> web.Response:
        """Liveness probe - бот жив и отвечает."""
        return web.json_response({
            "status": "ok",
            "service": "voenmeh-timetable-bot",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })

    async def readiness_check(request: web.Request) -> web.Response:
        """Readiness probe - все зависимости готовы к обработке запросов."""
        checks = {"bot": True, "redis": False, "database": False}
        details = {}

        # Проверка Redis
        if redis_client:
            try:
                await redis_client.ping()
                checks["redis"] = True
            except Exception as e:
                details["redis_error"] = str(e)
        else:
            details["redis_error"] = "Redis client not provided"

        # Проверка Database
        if db_session_maker:
            try:
                async with db_session_maker() as session:
                    await session.execute(text("SELECT 1"))
                checks["database"] = True
            except Exception as e:
                details["database_error"] = str(e)
        else:
            details["database_error"] = "Database session maker not provided"

        all_ok = all(checks.values())
        status_code = 200 if all_ok else 503

        response_data = {
            "status": "ready" if all_ok else "not_ready",
            "checks": checks,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        if details:
            response_data["details"] = details

        return web.json_response(response_data, status=status_code)

    # --- Alertmanager Webhook ---

    async def handle_alert(request: web.Request) -> web.Response:
        # Простая авторизация по ключу: Header "Authorization: Bearer <ALERT_WEBHOOK_API_KEY>"
        expected_key = os.getenv("ALERT_WEBHOOK_API_KEY")
        if expected_key:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer ") or auth_header.split(" ", 1)[1] != expected_key:
                return web.Response(status=401, text="unauthorized")

        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="invalid json")

        text_msg = format_alertmanager_message(payload)

        # fan-out to admins sequentially (простая и надежная доставка)
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text_msg)
            except Exception:
                # best-effort; ignore failures
                pass

        return web.Response(status=200, text="ok")

    # Регистрация маршрутов
    app.router.add_get("/health", health_check)
    app.router.add_get("/readiness", readiness_check)
    app.router.add_post("/alerts", handle_alert)

    logger.info("Alert webhook app created with /health and /readiness endpoints")
    return app


async def run_alert_webhook_server(
    bot,
    admin_ids: List[int],
    port: int = 8010,
    redis_client=None,
    db_session_maker=None,
):
    """Запускает HTTP-сервер для health checks и alertmanager webhook."""
    app = create_alert_app(
        bot,
        admin_ids,
        redis_client=redis_client,
        db_session_maker=db_session_maker,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Health check server started on http://0.0.0.0:{port}/health")

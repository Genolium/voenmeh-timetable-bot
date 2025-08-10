from aiohttp import web
from typing import List, Dict, Any


def format_alertmanager_message(payload: Dict[str, Any]) -> str:
    """Formats Alertmanager webhook payload into a readable message for admins."""
    status = payload.get('status', 'unknown').upper()
    alerts = payload.get('alerts', [])
    lines: List[str] = [f"ALERTMANAGER: {status} ({len(alerts)} alert(s))"]

    for alert in alerts:
        labels = alert.get('labels', {})
        annotations = alert.get('annotations', {})
        name = labels.get('alertname', 'unknown')
        severity = labels.get('severity', 'unknown')
        startsAt = alert.get('startsAt', '')
        endsAt = alert.get('endsAt', '')
        desc = annotations.get('description') or annotations.get('summary') or ''
        src = labels.get('source') or labels.get('component') or ''

        lines.append(
            "\n".join([
                f"⚠️ {name} [{severity}]",
                f"{desc}",
                f"source={src}",
                f"startsAt={startsAt}",
                f"endsAt={endsAt}",
            ])
        )
    return "\n\n".join(lines)


def create_alert_app(bot, admin_ids: List[int]) -> web.Application:
    app = web.Application()

    async def handle_alert(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="invalid json")

        text = format_alertmanager_message(payload)

        # fan-out to admins sequentially (простая и надежная доставка)
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except Exception:
                # best-effort; ignore failures
                pass

        return web.Response(status=200, text="ok")

    app.router.add_post('/alerts', handle_alert)
    return app


async def run_alert_webhook_server(bot, admin_ids: List[int], port: int = 8010):
    app = create_alert_app(bot, admin_ids)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

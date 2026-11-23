"""
Модуль для генерации автоматических отчётов по статистике для администраторов.
"""

import logging
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from aiogram import Bot
from aiogram.types import FSInputFile

from core.analytics import get_top_features
from core.config import MOSCOW_TZ
from core.db.backups import create_db_backup
from core.metrics import (
    ERRORS_TOTAL,
    LAST_SCHEDULE_UPDATE_TS,
    PARSER_STATS,
    RETRIES_TOTAL,
    TASKS_SENT_TO_QUEUE,
)
from core.user_data import UserDataManager

logger = logging.getLogger(__name__)


def _local_counter_sum(metric: Any, label_filter: Optional[tuple[str, str]] = None) -> int:
    """Суммирует текущие значения Prometheus-счётчика в текущем процессе."""
    total = 0.0
    for family in metric.collect():
        for sample in family.samples:
            if not sample.name.endswith("_total"):
                continue
            if label_filter and sample.labels.get(label_filter[0]) != label_filter[1]:
                continue
            total += float(sample.value)
    return int(round(total))


def _local_last_update_str() -> str:
    """Возвращает время последнего обновления расписания из локальной метрики."""
    ts = 0.0
    for family in LAST_SCHEDULE_UPDATE_TS.collect():
        for sample in family.samples:
            ts = max(ts, float(sample.value))
    if ts > 0:
        return datetime.fromtimestamp(ts, tz=MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")
    return "Нет данных"


class AdminReportsGenerator:
    """Генератор отчётов для администраторов."""

    def __init__(self, bot: Bot, user_data_manager: UserDataManager):
        self.bot = bot
        self.user_data_manager = user_data_manager

    async def generate_daily_report(self) -> str:
        """
        Генерирует ежедневный отчёт по статистике бота.

        Returns:
            Текстовый отчёт для отправки администраторам
        """
        try:
            # Получаем статистику из базы данных
            (
                total_users,
                dau,
                wau,
                mau,
                subscribed_total,
                unsubscribed_total,
                subs_breakdown,
                top_groups,
                group_dist,
            ) = await self.user_data_manager.gather_stats()

            # Получаем количество новых пользователей за день
            raw_new = await self.user_data_manager.get_new_users_count(1)
            new_users_day = int(raw_new) if isinstance(raw_new, (int, float)) else 0

            # Получаем отток (заблокировавшие бота) и воронку (без группы)
            blocked_today = 0
            if hasattr(self.user_data_manager, "get_blocked_users_count"):
                raw_blocked = await self.user_data_manager.get_blocked_users_count(days=1)
                blocked_today = int(raw_blocked) if isinstance(raw_blocked, (int, float)) else 0

            users_without_group = 0
            if hasattr(self.user_data_manager, "get_users_without_group_count"):
                raw_without = await self.user_data_manager.get_users_without_group_count()
                users_without_group = int(raw_without) if isinstance(raw_without, (int, float)) else 0

            # Получаем дельты динамики к вчерашнему дню
            dynamics = None
            if hasattr(self.user_data_manager, "get_daily_dynamics"):
                dyn_res = await self.user_data_manager.get_daily_dynamics(
                    total_users=total_users,
                    dau=dau,
                    subscribed_total=subscribed_total,
                    new_users_day=new_users_day,
                )
                if isinstance(dyn_res, dict):
                    dynamics = dyn_res

            # Получаем популярные функции
            top_features = []
            try:
                redis_client = await self.user_data_manager._get_redis_client()
                top_features = await get_top_features(redis_client, days=1, limit=4)
            except Exception:
                pass

            # Получаем метрики из Prometheus (или локального реестра при отсутствии PROMETHEUS_URL)
            prometheus_metrics = await self._get_prometheus_metrics()

            # Текущая дата
            today = datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y")

            # Формируем отчёт
            report = self._format_daily_report(
                today,
                total_users,
                dau,
                wau,
                mau,
                subscribed_total,
                unsubscribed_total,
                subs_breakdown,
                top_groups,
                group_dist,
                prometheus_metrics,
                new_users_day=new_users_day,
                dynamics=dynamics,
                blocked_today=blocked_today,
                users_without_group=users_without_group,
                top_features=top_features,
            )

            return report

        except Exception as e:
            logger.error(f"Error generating daily report: {e}")
            return "❌ Ошибка при генерации отчёта. Проверьте логи."

    async def generate_weekly_report(self) -> str:
        """
        Генерирует еженедельный отчёт по статистике бота.

        Returns:
            Текстовый отчёт для отправки администраторам
        """
        try:
            # Получаем статистику за неделю
            new_users_week = await self.user_data_manager.get_new_users_count(7)
            active_users_week = await self.user_data_manager.get_active_users_by_period(7)

            # Получаем общую статистику
            (
                total_users,
                dau,
                wau,
                mau,
                subscribed_total,
                unsubscribed_total,
                subs_breakdown,
                top_groups,
                group_dist,
            ) = await self.user_data_manager.gather_stats()

            # Получаем метрики из Prometheus за неделю
            prometheus_metrics = await self._get_prometheus_metrics_weekly()

            # Формируем отчёт
            report = self._format_weekly_report(
                new_users_week,
                active_users_week,
                total_users,
                subscribed_total,
                top_groups,
                prometheus_metrics,
            )

            return report

        except Exception as e:
            logger.error(f"Error generating weekly report: {e}")
            return "❌ Ошибка при генерации недельного отчёта. Проверьте логи."

    async def generate_monthly_report(self) -> str:
        """
        Генерирует ежемесячный отчёт по статистике бота.

        Returns:
            Текстовый отчёт для отправки администраторам
        """
        try:
            # Получаем статистику за месяц
            new_users_month = await self.user_data_manager.get_new_users_count(30)
            active_users_month = await self.user_data_manager.get_active_users_by_period(30)

            # Получаем общую статистику
            (
                total_users,
                dau,
                wau,
                mau,
                subscribed_total,
                unsubscribed_total,
                subs_breakdown,
                top_groups,
                group_dist,
            ) = await self.user_data_manager.gather_stats()

            # Получаем метрики из Prometheus за месяц
            prometheus_metrics = await self._get_prometheus_metrics_monthly()
            if (
                isinstance(prometheus_metrics, dict)
                and not prometheus_metrics.get("avg_daily_users")
                and isinstance(active_users_month, (int, float))
                and active_users_month > 0
            ):
                prometheus_metrics["avg_daily_users"] = max(1, round(active_users_month / 30))

            # Формируем отчёт
            report = self._format_monthly_report(
                new_users_month,
                active_users_month,
                total_users,
                subscribed_total,
                top_groups,
                prometheus_metrics,
            )

            return report

        except Exception as e:
            logger.error(f"Error generating monthly report: {e}")
            return "❌ Ошибка при генерации месячного отчёта. Проверьте логи."

    async def _query_prometheus_vector(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        query: str,
    ) -> List[Dict[str, Any]]:
        """Выполняет instant query к Prometheus HTTP API."""
        async with session.get(
            f"{base_url}/api/v1/query",
            params={"query": query},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return []
            payload = await resp.json()
        if not isinstance(payload, dict) or payload.get("status") != "success":
            return []
        result = payload.get("data", {}).get("result")
        return result if isinstance(result, list) else []

    @staticmethod
    def _extract_scalar(rows: List[Dict[str, Any]]) -> Optional[float]:
        """Извлекает конечное скалярное значение из результата Prometheus vector."""
        if not rows or not isinstance(rows[0], dict):
            return None
        value_pair = rows[0].get("value")
        if not isinstance(value_pair, (list, tuple)) or len(value_pair) < 2:
            return None
        try:
            value = float(value_pair[1])
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    async def _fetch_prometheus_window(
        self,
        window: str,
        extra_queries: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Запрашивает счётчики за окно времени и дополнительные PromQL-выражения."""
        base_url = (os.getenv("PROMETHEUS_URL") or "").strip().rstrip("/")
        if not base_url:
            return None

        queries = {
            "tasks": f"sum(increase(bot_tasks_sent_total[{window}]))",
            "errors": f"sum(increase(bot_errors_total[{window}]))",
            "retries": f"sum(increase(bot_retries_total[{window}]))",
            "updates": f'sum(increase(bot_parser_operations_total{{status="success"}}[{window}]))',
        }
        try:
            async with aiohttp.ClientSession() as session:
                counts: Dict[str, int] = {}
                for key, promql in queries.items():
                    rows = await self._query_prometheus_vector(session, base_url, promql)
                    val = self._extract_scalar(rows)
                    counts[key] = max(0, int(round(val))) if val is not None else 0

                extra: Dict[str, List[Dict[str, Any]]] = {}
                for key, promql in (extra_queries or {}).items():
                    extra[key] = await self._query_prometheus_vector(session, base_url, promql)

                return {"counts": counts, "extra": extra}
        except Exception as exc:
            logger.warning(f"Failed to query Prometheus metrics ({window}): {exc}")
            return None

    async def _get_prometheus_metrics(self) -> Dict[str, Any]:
        """Получает метрики из Prometheus за последние 24 часа (с fallback на локальные метрики)."""
        prom = await self._fetch_prometheus_window(
            "24h",
            extra_queries={"last_update_ts": "max(bot_last_schedule_update_timestamp)"},
        )
        if prom is not None:
            counts = prom["counts"]
            ts = self._extract_scalar(prom["extra"].get("last_update_ts", []))
            last_update = (
                datetime.fromtimestamp(ts, tz=MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")
                if ts and ts > 0
                else _local_last_update_str()
            )
            return {
                "tasks_sent": counts["tasks"],
                "errors_count": counts["errors"],
                "retries_count": counts["retries"],
                "schedule_updates": counts["updates"],
                "last_update": last_update,
            }

        return {
            "tasks_sent": _local_counter_sum(TASKS_SENT_TO_QUEUE),
            "errors_count": _local_counter_sum(ERRORS_TOTAL),
            "retries_count": _local_counter_sum(RETRIES_TOTAL),
            "schedule_updates": _local_counter_sum(PARSER_STATS, ("status", "success")),
            "last_update": _local_last_update_str(),
        }

    async def _get_prometheus_metrics_weekly(self) -> Dict[str, Any]:
        """Получает метрики из Prometheus за последнюю неделю (с fallback на локальные метрики)."""
        prom = await self._fetch_prometheus_window(
            "7d",
            extra_queries={
                "peak_hour": "topk(1, sum by (hour_of_day) (increase(bot_user_activity_hourly_total[7d])))",
            },
        )
        if prom is not None:
            counts = prom["counts"]
            peak_rows = prom["extra"].get("peak_hour", [])
            peak_activity = "Нет данных"
            if peak_rows and isinstance(peak_rows[0], dict):
                hour_raw = peak_rows[0].get("metric", {}).get("hour_of_day")
                if hour_raw is not None:
                    try:
                        hour_int = int(str(hour_raw)) % 24
                        peak_activity = f"{hour_int:02d}:00-{(hour_int + 1) % 24:02d}:00"
                    except ValueError:
                        pass
            return {
                "tasks_sent_total": counts["tasks"],
                "errors_total": counts["errors"],
                "retries_total": counts["retries"],
                "schedule_updates": counts["updates"],
                "peak_activity": peak_activity,
            }

        return {
            "tasks_sent_total": _local_counter_sum(TASKS_SENT_TO_QUEUE),
            "errors_total": _local_counter_sum(ERRORS_TOTAL),
            "retries_total": _local_counter_sum(RETRIES_TOTAL),
            "schedule_updates": _local_counter_sum(PARSER_STATS, ("status", "success")),
            "peak_activity": "Нет данных",
        }

    async def _get_prometheus_metrics_monthly(self) -> Dict[str, Any]:
        """Получает метрики из Prometheus за последний месяц (с fallback на локальные метрики)."""
        top_features_names: List[str] = []
        try:
            redis_client = await self.user_data_manager._get_redis_client()
            top = await get_top_features(redis_client, days=30, limit=3)
            top_features_names = [name for _, name, _ in top]
        except Exception:
            top_features_names = []

        prom = await self._fetch_prometheus_window(
            "30d",
            extra_queries={"avg_dau": "sum(increase(bot_user_activity_daily_total[30d])) / 30"},
        )
        if prom is not None:
            counts = prom["counts"]
            avg_dau_val = self._extract_scalar(prom["extra"].get("avg_dau", []))
            return {
                "tasks_sent_total": counts["tasks"],
                "errors_total": counts["errors"],
                "retries_total": counts["retries"],
                "schedule_updates": counts["updates"],
                "avg_daily_users": max(0, int(round(avg_dau_val))) if avg_dau_val is not None else 0,
                "top_features": top_features_names or ["Нет данных"],
            }

        return {
            "tasks_sent_total": _local_counter_sum(TASKS_SENT_TO_QUEUE),
            "errors_total": _local_counter_sum(ERRORS_TOTAL),
            "retries_total": _local_counter_sum(RETRIES_TOTAL),
            "schedule_updates": _local_counter_sum(PARSER_STATS, ("status", "success")),
            "avg_daily_users": 0,
            "top_features": top_features_names or ["Нет данных"],
        }

    def _format_daily_report(
        self,
        today: str,
        total_users: int,
        dau: int,
        wau: int,
        mau: int,
        subscribed_total: int,
        unsubscribed_total: int,
        subs_breakdown: Dict[str, int],
        top_groups: List[tuple],
        group_dist: Dict[str, int],
        prometheus_metrics: Dict[str, Any],
        new_users_day: int = 0,
        dynamics: Optional[Dict[str, Any]] = None,
        blocked_today: int = 0,
        users_without_group: int = 0,
        top_features: Optional[List[tuple]] = None,
    ) -> str:
        """Форматирует ежедневный отчёт."""

        import html as html_lib

        top_groups_text = "\n".join(
            [f"  • {html_lib.escape(str(g or 'Не указана'))}: {c} пользователей" for g, c in top_groups[:5]]
        )
        if not top_groups_text:
            top_groups_text = "  • Нет данных"

        subs_breakdown_text = (
            f"  • Вечерние уведомления: {subs_breakdown.get('evening', 0)}\n"
            f"  • Утренние уведомления: {subs_breakdown.get('morning', 0)}\n"
            f"  • Напоминания о парах: {subs_breakdown.get('reminders', 0)}"
        )

        group_dist_text = "\n".join([f"  • {category}: {count} групп" for category, count in group_dist.items()])
        if not group_dist_text:
            group_dist_text = "  • Нет данных"

        # Формирование блока динамики
        dyn = (
            dynamics
            if isinstance(dynamics, dict)
            else {
                "delta_users_str": f"+{new_users_day}",
                "delta_dau_pct_str": "+0%",
                "delta_subs_str": "+0",
            }
        )

        # Расчет чистого прироста (Net Growth)
        safe_new = int(new_users_day) if isinstance(new_users_day, (int, float)) else 0
        safe_blocked = int(blocked_today) if isinstance(blocked_today, (int, float)) else 0
        net_growth = safe_new - safe_blocked
        net_growth_str = f"+{net_growth}" if net_growth >= 0 else str(net_growth)

        # Расчет воронки онбординга
        safe_total = int(total_users) if isinstance(total_users, (int, float)) else 0
        safe_without = int(users_without_group) if isinstance(users_without_group, (int, float)) else 0
        users_with_group = max(0, safe_total - safe_without)
        conv_pct = round((users_with_group / safe_total * 100), 1) if safe_total > 0 else 0.0

        # Формирование блока популярных функций
        top_features_list = top_features or []
        if top_features_list:
            top_features_text = "\n".join([f"  • {name}: <b>{count}</b>" for _, name, count in top_features_list])
        else:
            top_features_text = "  • Нет данных за сутки"

        report = f"""📊 <b>Ежедневный отчёт по боту</b>
📅 {today}

👥 <b>Пользователи</b>
  • Всего пользователей: <b>{total_users}</b> ({dyn.get('delta_users_str', f'+{new_users_day}')})
  • Новых за день: <b>{new_users_day}</b>
  • Активных за день (DAU): <b>{dau}</b> ({dyn.get('delta_dau_pct_str', '+0%')} к вчера)
  • Активных за неделю: <b>{wau}</b>
  • Активных за месяц: <b>{mau}</b>

🚪 <b>Отток и чистый прирост</b>
  • Заблокировали бота: <b>{blocked_today}</b>
  • Чистый прирост (Net Growth): <b>{net_growth_str}</b>

🎯 <b>Воронка онбординга</b>
  • Без группы (застряли): <b>{users_without_group}</b>
  • Выбрали группу: <b>{users_with_group}</b> ({conv_pct}%)

🔔 <b>Подписки</b>
  • С активными подписками: <b>{subscribed_total}</b> ({dyn.get('delta_subs_str', '+0')})
  • Полностью отписались: <b>{unsubscribed_total}</b>
  <b>Разбивка по типам:</b>
{subs_breakdown_text}

🏆 <b>Топ функций (24ч)</b>
{top_features_text}

🎓 <b>Топ-5 групп</b>
{top_groups_text}

📈 <b>Распределение по размеру групп</b>
{group_dist_text}

⚡ <b>Метрики системы (24ч)</b>
  • Отправлено задач: <b>{prometheus_metrics.get('tasks_sent', 0)}</b>
  • Ошибок: <b>{prometheus_metrics.get('errors_count', 0)}</b>
  • Повторных попыток: <b>{prometheus_metrics.get('retries_count', 0)}</b>
  • Обновлений расписания: <b>{prometheus_metrics.get('schedule_updates', 0)}</b>
  • Последнее обновление: <b>{prometheus_metrics.get('last_update', 'N/A')}</b>

<i>Отчёт сгенерирован автоматически</i>
"""
        return report

    def _format_weekly_report(
        self,
        new_users_week: int,
        active_users_week: int,
        total_users: int,
        subscribed_total: int,
        top_groups: List[tuple],
        prometheus_metrics: Dict[str, Any],
    ) -> str:
        """Форматирует еженедельный отчёт."""

        import html as html_lib

        top_groups_text = "\n".join(
            [f"  • {html_lib.escape(str(g or 'Не указана'))}: {c} пользователей" for g, c in top_groups[:5]]
        )
        if not top_groups_text:
            top_groups_text = "  • Нет данных"

        report = f"""📊 <b>Еженедельный отчёт по боту</b>
📅 {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y')}

📈 <b>Прирост за неделю</b>
  • Новых пользователей: <b>{new_users_week}</b>
  • Активных пользователей: <b>{active_users_week}</b>
  • Всего пользователей: <b>{total_users}</b>

🔔 <b>Подписки</b>
  • С активными подписками: <b>{subscribed_total}</b>

🎓 <b>Топ-5 групп</b>
{top_groups_text}

⚡ <b>Метрики системы (7 дней)</b>
  • Отправлено задач: <b>{prometheus_metrics.get('tasks_sent_total', 0)}</b>
  • Ошибок: <b>{prometheus_metrics.get('errors_total', 0)}</b>
  • Повторных попыток: <b>{prometheus_metrics.get('retries_total', 0)}</b>
  • Обновлений расписания: <b>{prometheus_metrics.get('schedule_updates', 0)}</b>
  • Пик активности: <b>{prometheus_metrics.get('peak_activity', 'N/A')}</b>

<i>Отчёт сгенерирован автоматически</i>
"""
        return report

    def _format_monthly_report(
        self,
        new_users_month: int,
        active_users_month: int,
        total_users: int,
        subscribed_total: int,
        top_groups: List[tuple],
        prometheus_metrics: Dict[str, Any],
    ) -> str:
        """Форматирует ежемесячный отчёт."""

        import html as html_lib

        top_groups_text = "\n".join(
            [f"  • {html_lib.escape(str(g or 'Не указана'))}: {c} пользователей" for g, c in top_groups[:5]]
        )
        if not top_groups_text:
            top_groups_text = "  • Нет данных"

        report = f"""📊 <b>Ежемесячный отчёт по боту</b>
📅 {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y')}

📈 <b>Прирост за месяц</b>
  • Новых пользователей: <b>{new_users_month}</b>
  • Активных пользователей: <b>{active_users_month}</b>
  • Всего пользователей: <b>{total_users}</b>

🔔 <b>Подписки</b>
  • С активными подписками: <b>{subscribed_total}</b>

🎓 <b>Топ-5 групп</b>
{top_groups_text}

⚡ <b>Метрики системы (30 дней)</b>
  • Отправлено задач: <b>{prometheus_metrics.get('tasks_sent_total', 0)}</b>
  • Ошибок: <b>{prometheus_metrics.get('errors_total', 0)}</b>
  • Повторных попыток: <b>{prometheus_metrics.get('retries_total', 0)}</b>
  • Обновлений расписания: <b>{prometheus_metrics.get('schedule_updates', 0)}</b>
  • Среднесуточная активность: <b>{prometheus_metrics.get('avg_daily_users', 0)}</b>

🏆 <b>Популярные функции</b>
  • {', '.join(prometheus_metrics.get('top_features', ['Нет данных']))}

<i>Отчёт сгенерирован автоматически</i>
"""
        return report


async def send_daily_reports(bot: Bot, user_data_manager: UserDataManager) -> None:
    """Отправляет ежедневные отчёты администраторам вместе с бэкапом базы данных."""
    backup_path: Optional[Path] = None
    try:
        # Получаем список администраторов
        admin_ids = await user_data_manager.get_admin_users()
        if not admin_ids:
            logger.info("No admin users found to send daily report")
            return

        generator = AdminReportsGenerator(bot, user_data_manager)
        report = await generator.generate_daily_report()

        # Создаем бэкап базы данных
        try:
            output_dir = Path("/app/data") if Path("/app/data").exists() else None
            backup_path = await create_db_backup(output_dir=output_dir)
            logger.info(f"Database backup created for daily report: {backup_path}")
        except Exception as exc:
            logger.error(f"Failed to create database backup for daily report: {exc}")
            ERRORS_TOTAL.labels(source="db_backup").inc()

        today = datetime.now(MOSCOW_TZ).strftime("%d.%m.%Y")

        # Отправляем отчёт каждому администратору
        for admin_id in admin_ids:
            try:
                await bot.send_message(chat_id=admin_id, text=report, parse_mode="HTML")
                logger.info(f"Daily report sent to admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to send daily report to admin {admin_id}: {e}")

            if backup_path and backup_path.exists():
                try:
                    document = FSInputFile(backup_path)
                    await bot.send_document(
                        chat_id=admin_id,
                        document=document,
                        caption=f"📦 <b>Резервная копия БД</b> ({today})\n<code>{backup_path.name}</code>",
                        parse_mode="HTML",
                    )
                    logger.info(f"Database backup sent to admin {admin_id}")
                except Exception as e:
                    logger.error(f"Failed to send database backup to admin {admin_id}: {e}")

    except Exception as e:
        logger.error(f"Error in send_daily_reports: {e}")
    finally:
        if backup_path and backup_path.exists():
            try:
                backup_path.unlink()
                logger.info(f"Temporary database backup file deleted: {backup_path}")
            except OSError as e:
                logger.warning(f"Failed to delete temporary backup file {backup_path}: {e}")


async def send_weekly_reports(bot: Bot, user_data_manager: UserDataManager) -> None:
    """Отправляет еженедельные отчёты администраторам."""
    try:
        generator = AdminReportsGenerator(bot, user_data_manager)
        report = await generator.generate_weekly_report()

        # Получаем список администраторов
        admin_ids = await user_data_manager.get_admin_users()

        # Отправляем отчёт каждому администратору
        for admin_id in admin_ids:
            try:
                await bot.send_message(chat_id=admin_id, text=report, parse_mode="HTML")
                logger.info(f"Weekly report sent to admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to send weekly report to admin {admin_id}: {e}")

    except Exception as e:
        logger.error(f"Error in send_weekly_reports: {e}")


async def send_monthly_reports(bot: Bot, user_data_manager: UserDataManager) -> None:
    """Отправляет ежемесячные отчёты администраторам."""
    try:
        generator = AdminReportsGenerator(bot, user_data_manager)
        report = await generator.generate_monthly_report()

        # Получаем список администраторов
        admin_ids = await user_data_manager.get_admin_users()

        # Отправляем отчёт каждому администратору
        for admin_id in admin_ids:
            try:
                await bot.send_message(chat_id=admin_id, text=report, parse_mode="HTML")
                logger.info(f"Monthly report sent to admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to send monthly report to admin {admin_id}: {e}")

    except Exception as e:
        logger.error(f"Error in send_monthly_reports: {e}")

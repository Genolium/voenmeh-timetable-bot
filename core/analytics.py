"""
Модуль расширенной аналитики: отслеживание популярных функций
и генерация графиков динамики через matplotlib.
"""

from __future__ import annotations

import inspect
import io
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import matplotlib

# Использовать неинтерактивный бэкенд Agg для работы без GUI / в контейнере
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)

# Сопоставление технических названий функций с читаемыми названиями
FEATURE_NAMES: Dict[str, str] = {
    "inline_teacher": "Inline-поиск преподавателя",
    "day_nav": "Переход по дням недели",
    "image_export": "Генерация расписания картинкой",
    "classroom_search": "Поиск аудитории",
    "teacher_search": "Поиск преподавателя",
}

FEATURE_KEY_PREFIX = "timetable:feature_clicks"


async def track_feature_click(redis_client, feature_name: str) -> None:
    """
    Фиксирует использование конкретной функции в Redis.
    Сохраняет как общий счётчик, так и посуточный.
    """
    if not redis_client:
        return

    try:
        today_str = datetime.now().strftime("%Y%m%d")
        daily_key = f"{FEATURE_KEY_PREFIX}:daily:{today_str}"
        total_key = f"{FEATURE_KEY_PREFIX}:total"

        # Увеличиваем посуточный и общий счётчики
        pipe = redis_client.pipeline()
        if inspect.isawaitable(pipe):
            pipe = await pipe
        pipe.hincrby(daily_key, feature_name, 1)
        pipe.expire(daily_key, 86400 * 60)  # Хранить 60 дней
        pipe.hincrby(total_key, feature_name, 1)
        await pipe.execute()
    except Exception as e:
        logger.warning(f"Failed to track feature click '{feature_name}': {e}")


async def get_top_features(
    redis_client,
    days: int = 7,
    limit: int = 5,
) -> List[Tuple[str, str, int]]:
    """
    Возвращает список самых популярных функций за указанный период (в днях).
    Формат результата: [(feature_code, human_name, count), ...]
    """
    if not redis_client:
        return []

    try:
        aggregated: Dict[str, int] = {}
        now = datetime.now()

        # Суммируем посуточные счётчики
        for i in range(days):
            date_str = (now - timedelta(days=i)).strftime("%Y%m%d")
            daily_key = f"{FEATURE_KEY_PREFIX}:daily:{date_str}"
            day_data = await redis_client.hgetall(daily_key)
            if isinstance(day_data, dict):
                for f_name, count in day_data.items():
                    key = f_name.decode("utf-8") if isinstance(f_name, bytes) else str(f_name)
                    try:
                        cnt = int(count)
                    except (ValueError, TypeError):
                        cnt = 1
                    aggregated[key] = aggregated.get(key, 0) + cnt

        # Если посуточные ключи пусты (например, только запустились), читаем общий хэш
        if not aggregated:
            total_key = f"{FEATURE_KEY_PREFIX}:total"
            total_data = await redis_client.hgetall(total_key)
            if isinstance(total_data, dict):
                for f_name, count in total_data.items():
                    key = f_name.decode("utf-8") if isinstance(f_name, bytes) else str(f_name)
                    try:
                        cnt = int(count)
                    except (ValueError, TypeError):
                        cnt = 1
                    aggregated[key] = cnt

        if not aggregated:
            return []

        sorted_features = sorted(aggregated.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [
            (code, FEATURE_NAMES.get(code, code), count)
            for code, count in sorted_features
        ]
    except Exception as e:
        logger.warning(f"Failed to get top features: {e}")
        return []


def generate_dynamics_chart(
    dates: List[datetime],
    new_users: List[int],
    active_users: List[int],
    notifications_sent: Optional[List[int]] = None,
    period_title: str = "7 дней",
) -> io.BytesIO:
    """
    Генерирует наглядный график динамики в современном темном стиле (High-DPI PNG).
    
    Args:
        dates: Список дат для оси X
        new_users: Список новых пользователей по дням
        active_users: Список активных пользователей (DAU) по дням
        notifications_sent: Список отправленных уведомлений по дням (опционально)
        period_title: Текстовая подпись периода для заголовка
        
    Returns:
        BytesIO буфер с PNG изображением
    """
    # Стилизация графиков под аккуратный современный тёмный UI
    bg_color = "#181825"
    card_color = "#1E1E2E"
    grid_color = "#313244"
    text_color = "#CDD6F4"
    subtext_color = "#A6ADC8"

    color_dau = "#A6E3A1"        # Зелёный/мятный для активных (DAU)
    color_new = "#89DCEB"        # Голубой/бирюзовый для новых
    color_notify = "#CBA6F7"     # Лавандовый для уведомлений

    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=150, facecolor=bg_color)
    ax.set_facecolor(card_color)

    if not dates:
        dates = [datetime.now() - timedelta(days=i) for i in reversed(range(7))]
        new_users = [0] * len(dates)
        active_users = [0] * len(dates)

    # Построение линий с маркерами
    ax.plot(
        dates,
        active_users,
        label="Активные за день (DAU)",
        color=color_dau,
        linewidth=2.2,
        marker="o",
        markersize=4,
    )
    ax.plot(
        dates,
        new_users,
        label="Новые пользователи",
        color=color_new,
        linewidth=2.0,
        marker="s",
        markersize=4,
    )

    if notifications_sent and len(notifications_sent) == len(dates):
        ax.plot(
            dates,
            notifications_sent,
            label="Отправлено рассылок",
            color=color_notify,
            linewidth=1.8,
            linestyle="--",
            marker="^",
            markersize=4,
        )

    # Заголовок и подписи
    ax.set_title(
        f"Динамика активности бота (за {period_title})",
        fontsize=13,
        fontweight="bold",
        color=text_color,
        pad=15,
    )
    ax.tick_params(colors=subtext_color, labelsize=9)

    # Форматирование дат на оси X
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    fig.autofmt_xdate(rotation=0, ha="center")

    # Сетка
    ax.grid(True, linestyle=":", alpha=0.5, color=grid_color)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(grid_color)
    ax.spines["bottom"].set_color(grid_color)

    # Легенда
    legend = ax.legend(
        facecolor=bg_color,
        edgecolor=grid_color,
        labelcolor=text_color,
        fontsize=9,
        loc="upper left",
    )
    legend.get_frame().set_alpha(0.85)

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

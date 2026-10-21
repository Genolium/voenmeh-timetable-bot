#!/usr/bin/env python3
"""
Тестовый скрипт для проверки настройки планировщика задач
"""

from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone


def test_scheduler_setup():
    """Тестирует настройку планировщика задач."""

    # Создаем планировщик
    MOSCOW_TZ = timezone("Europe/Moscow")
    scheduler = AsyncIOScheduler(timezone=str(MOSCOW_TZ))

    # Добавляем задачу генерации полного расписания
    # В 4 утра в воскресенье
    scheduler.add_job(
        lambda: print("🔄 Генерация полного расписания запущена"),
        "cron",
        day_of_week="sun",
        hour=4,
        minute=0,
        id="generate_full_schedule",
    )

    # Проверяем настройки задачи
    job = scheduler.get_job("generate_full_schedule")
    if job:
        print("✅ Задача генерации полного расписания добавлена в планировщик")
        print(f"📅 Расписание: {job.trigger}")

        # Проверяем, что это действительно воскресенье в 4 утра
        trigger_str = str(job.trigger)
        if "day_of_week='sun'" in trigger_str and "hour='4'" in trigger_str and "minute='0'" in trigger_str:
            print("✅ Настройки корректны: воскресенье в 4:00")
        else:
            print("❌ Настройки некорректны")
    else:
        print("❌ Задача не найдена в планировщике")

    # Показываем текущее время в Москве
    now = datetime.now(MOSCOW_TZ)
    print(f"\n🕐 Текущее время в Москве: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"📅 День недели: {now.strftime('%A')}")

    # Показываем информацию о задаче
    if job:
        print(f"🆔 ID задачи: {job.id}")
        print(f"📋 Имя функции: {job.func}")
        print(f"🔄 Повторяется: {job.trigger}")


if __name__ == "__main__":
    test_scheduler_setup()

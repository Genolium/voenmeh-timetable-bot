#!/usr/bin/env python3
"""
Скрипт для мониторинга состояния системы и предотвращения зависаний
"""

import asyncio
import logging
import os
import subprocess
import time
from typing import Any, Dict

import aiohttp
import psutil
from redis.asyncio import Redis

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class SystemMonitor:
    def __init__(self):
        self.redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        self.rabbitmq_url = os.getenv("DRAMATIQ_BROKER_URL", "amqp://guest:guest@rabbitmq:5672/")
        self.management_url = os.getenv("RABBITMQ_MANAGEMENT_URL", "http://rabbitmq:15672")
        self.check_interval = 30  # секунды

    async def get_system_stats(self) -> Dict[str, Any]:
        """Получает статистику системы"""
        try:
            # CPU и память
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage("/")

            # Процессы Python (бот, worker)
            python_processes = []
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "cmdline"]):
                try:
                    if "python" in proc.info["name"].lower():
                        cmdline = " ".join(proc.info["cmdline"] or [])
                        if "main.py" in cmdline or "dramatiq" in cmdline:
                            python_processes.append(
                                {
                                    "pid": proc.info["pid"],
                                    "name": proc.info["name"],
                                    "cpu": proc.info["cpu_percent"],
                                    "memory": proc.info["memory_percent"],
                                    "cmdline": cmdline[:100] + "..." if len(cmdline) > 100 else cmdline,
                                }
                            )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            return {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_used": memory.used // 1024 // 1024,  # MB
                "memory_total": memory.total // 1024 // 1024,  # MB
                "disk_percent": disk.percent,
                "python_processes": python_processes,
                "process_count": len(python_processes),
            }
        except Exception as e:
            logger.error(f"Ошибка получения статистики системы: {e}")
            return {}

    async def check_redis_health(self) -> bool:
        """Проверяет состояние Redis"""
        try:
            redis = Redis.from_url(self.redis_url)
            await redis.ping()
            await redis.aclose()
            return True
        except Exception as e:
            logger.error(f"Redis не отвечает: {e}")
            return False

    async def check_rabbitmq_health(self) -> bool:
        """Проверяет состояние RabbitMQ"""
        try:
            if "@" in self.rabbitmq_url:
                auth_part = self.rabbitmq_url.split("@")[0].replace("amqp://", "")
                username, password = auth_part.split(":")
            else:
                username, password = "guest", "guest"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.management_url}/api/overview", auth=aiohttp.BasicAuth(username, password), timeout=10
                ) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"RabbitMQ не отвечает: {e}")
            return False

    async def get_queue_stats(self) -> Dict[str, Any]:
        """Получает статистику очередей RabbitMQ"""
        try:
            if "@" in self.rabbitmq_url:
                auth_part = self.rabbitmq_url.split("@")[0].replace("amqp://", "")
                username, password = auth_part.split(":")
            else:
                username, password = "guest", "guest"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.management_url}/api/queues", auth=aiohttp.BasicAuth(username, password), timeout=10
                ) as response:
                    if response.status == 200:
                        queues = await response.json()
                        stats = {}
                        for queue in queues:
                            name = queue.get("name", "")
                            if "dramatiq" in name:
                                stats[name] = {
                                    "messages": queue.get("messages", 0),
                                    "consumers": queue.get("consumers", 0),
                                    "messages_ready": queue.get("messages_ready", 0),
                                    "messages_unacknowledged": queue.get("messages_unacknowledged", 0),
                                }
                        return stats
                    return {}
        except Exception as e:
            logger.error(f"Ошибка получения статистики очередей: {e}")
            return {}

    async def kill_hung_processes(self):
        """Убивает зависшие процессы"""
        try:
            killed_count = 0
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "create_time"]):
                try:
                    # Проверяем процессы старше 30 минут с высокой нагрузкой CPU
                    if (
                        proc.info["name"] == "python"
                        and proc.info["cpu_percent"] > 80
                        and time.time() - proc.info["create_time"] > 1800
                    ):  # 30 минут
                        logger.warning(f"Убиваем зависший процесс {proc.info['pid']} (CPU: {proc.info['cpu_percent']}%)")
                        proc.kill()
                        killed_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if killed_count > 0:
                logger.info(f"Убито {killed_count} зависших процессов")
        except Exception as e:
            logger.error(f"Ошибка при убийстве процессов: {e}")

    async def monitor_loop(self):
        """Основной цикл мониторинга"""
        logger.info("Запуск мониторинга системы...")

        while True:
            try:
                # Получаем статистику системы
                stats = await self.get_system_stats()

                if stats:
                    logger.info(
                        f"📊 Система: CPU {stats['cpu_percent']}%, RAM {stats['memory_percent']}% "
                        f"({stats['memory_used']}/{stats['memory_total']}MB), "
                        f"Диск {stats['disk_percent']}%, Процессов Python: {stats['process_count']}"
                    )

                    # Проверяем критические показатели
                    if stats["cpu_percent"] > 90:
                        logger.warning("⚠️ Высокая нагрузка CPU!")
                    if stats["memory_percent"] > 90:
                        logger.warning("⚠️ Высокая нагрузка памяти!")
                    if stats["disk_percent"] > 95:
                        logger.warning("⚠️ Мало места на диске!")

                    # Логируем процессы Python
                    for proc in stats.get("python_processes", []):
                        logger.info(
                            f"  🐍 PID {proc['pid']}: CPU {proc['cpu']:.1f}%, RAM {proc['memory']:.1f}%, "
                            f"CMD: {proc['cmdline']}"
                        )

                # Проверяем Redis и RabbitMQ
                redis_ok = await self.check_redis_health()
                rabbitmq_ok = await self.check_rabbitmq_health()

                if not redis_ok:
                    logger.error("❌ Redis не отвечает!")
                if not rabbitmq_ok:
                    logger.error("❌ RabbitMQ не отвечает!")

                # Получаем статистику очередей
                queue_stats = await self.get_queue_stats()
                if queue_stats:
                    for queue_name, stat in queue_stats.items():
                        logger.info(
                            f"📋 Очередь {queue_name}: {stat['messages']} сообщений, " f"{stat['consumers']} потребителей"
                        )

                # Проверяем на зависшие процессы каждый 5-й цикл
                if int(time.time()) % (self.check_interval * 5) < self.check_interval:
                    await self.kill_hung_processes()

            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")

            await asyncio.sleep(self.check_interval)


async def main():
    monitor = SystemMonitor()
    await monitor.monitor_loop()


if __name__ == "__main__":
    asyncio.run(main())

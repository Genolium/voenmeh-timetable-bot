#!/usr/bin/env python3
"""
Скрипт для graceful shutdown системы
"""

import asyncio
import logging
import os
import signal
import subprocess
import time
from typing import List

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class GracefulShutdown:
    def __init__(self):
        self.shutdown_timeout = 60  # секунды

    def get_container_processes(self) -> List[dict]:
        """Получает список процессов в контейнерах"""
        try:
            result = subprocess.run(["docker", "ps", "--format", "json"], capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                import json

                containers = []
                for line in result.stdout.strip().split("\n"):
                    if line.strip():
                        try:
                            container = json.loads(line)
                            if any(name in container.get("Names", "") for name in ["bot", "worker"]):
                                containers.append(container)
                        except json.JSONDecodeError:
                            continue
                return containers
        except Exception as e:
            logger.error(f"Ошибка получения списка контейнеров: {e}")

        return []

    def stop_container(self, container_name: str, timeout: int = 30) -> bool:
        """Останавливает контейнер gracefully"""
        try:
            logger.info(f"Останавливаем контейнер {container_name}...")

            # Сначала отправляем SIGTERM
            result = subprocess.run(
                ["docker", "stop", "--time", str(timeout), container_name],
                capture_output=True,
                text=True,
                timeout=timeout + 10,
            )

            if result.returncode == 0:
                logger.info(f"✅ Контейнер {container_name} остановлен")
                return True
            else:
                logger.warning(f"⚠️ Контейнер {container_name} не остановился gracefully: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"⏰ Таймаут остановки контейнера {container_name}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка остановки контейнера {container_name}: {e}")
            return False

    def kill_container(self, container_name: str) -> bool:
        """Принудительно убивает контейнер"""
        try:
            logger.warning(f"Убиваем контейнер {container_name} принудительно...")

            result = subprocess.run(["docker", "kill", container_name], capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                logger.info(f"✅ Контейнер {container_name} убит")
                return True
            else:
                logger.error(f"❌ Не удалось убить контейнер {container_name}: {result.stderr}")
                return False

        except Exception as e:
            logger.error(f"❌ Ошибка при убийстве контейнера {container_name}: {e}")
            return False

    def cleanup_docker_resources(self):
        """Очищает неиспользуемые Docker ресурсы"""
        try:
            logger.info("Очищаем Docker ресурсы...")

            # Останавливаем все контейнеры проекта
            subprocess.run(["docker", "compose", "stop"], capture_output=True, text=True, timeout=30)

            # Удаляем остановленные контейнеры
            subprocess.run(["docker", "container", "prune", "-f"], capture_output=True, text=True, timeout=30)

            # Очищаем неиспользуемые образы
            subprocess.run(["docker", "image", "prune", "-f"], capture_output=True, text=True, timeout=30)

            logger.info("✅ Docker ресурсы очищены")

        except Exception as e:
            logger.error(f"❌ Ошибка очистки Docker ресурсов: {e}")

    def graceful_shutdown(self):
        """Выполняет graceful shutdown всей системы"""
        logger.info("🚀 Начинаем graceful shutdown системы...")

        # Получаем список активных контейнеров
        containers = self.get_container_processes()
        logger.info(f"Найдено активных контейнеров: {len(containers)}")

        # Сначала останавливаем worker, потом bot
        stop_order = ["worker", "bot"]

        for container_type in stop_order:
            containers_to_stop = [c for c in containers if container_type in c.get("Names", "")]
            for container in containers_to_stop:
                container_name = container.get("Names", "unknown")
                if not self.stop_container(container_name, timeout=30):
                    # Если graceful не сработал, убиваем принудительно
                    self.kill_container(container_name)

        # Очищаем ресурсы
        self.cleanup_docker_resources()

        logger.info("✅ Graceful shutdown завершен")


def signal_handler(signum, frame):
    """Обработчик сигналов"""
    logger.info(f"Получен сигнал {signum}, начинаем graceful shutdown...")
    shutdown = GracefulShutdown()
    shutdown.graceful_shutdown()
    sys.exit(0)


def main():
    """Основная функция"""
    logger.info("Запуск системы graceful shutdown...")

    # Регистрируем обработчики сигналов
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        # Бесконечный цикл ожидания
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Получен KeyboardInterrupt, завершаем работу...")
        shutdown = GracefulShutdown()
        shutdown.graceful_shutdown()


if __name__ == "__main__":
    main()

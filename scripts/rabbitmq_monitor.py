#!/usr/bin/env python3
"""
Скрипт для мониторинга состояния RabbitMQ и автоматического перезапуска при проблемах
"""

import asyncio
import logging
import os
import subprocess
import time
from typing import Optional
import aiohttp
from redis.asyncio import Redis

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RabbitMQMonitor:
    def __init__(self):
        self.rabbitmq_url = os.getenv("DRAMATIQ_BROKER_URL", "amqp://guest:guest@rabbitmq:5672/")
        self.redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        self.management_url = os.getenv("RABBITMQ_MANAGEMENT_URL", "http://rabbitmq:15672")
        self.check_interval = 120  # секунды
        self.max_failures = 5
        self.failure_count = 0
        
    def _extract_credentials(self):
        """Extract management credentials from DRAMATIQ_BROKER_URL or env overrides."""
        # Allow explicit overrides
        username = os.getenv("RABBITMQ_MANAGEMENT_USER")
        password = os.getenv("RABBITMQ_MANAGEMENT_PASSWORD")
        if username and password:
            return username, password

        try:
            from urllib.parse import urlparse
            parsed = urlparse(self.rabbitmq_url)
            if parsed.username and parsed.password:
                return parsed.username, parsed.password
        except Exception:
            pass

        # Fallback to defaults
        return os.getenv("RABBITMQ_USER", "guest"), os.getenv("RABBITMQ_PASSWORD", "guest")

    async def check_rabbitmq_health(self) -> bool:
        """Проверяет состояние RabbitMQ через Management API"""
        try:
            username, password = self._extract_credentials()
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.management_url}/api/overview",
                    auth=aiohttp.BasicAuth(username, password),
                    timeout=10
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"RabbitMQ здоров: {data.get('node', 'unknown')}")
                        return True
                    else:
                        logger.warning(f"RabbitMQ Management API вернул статус {response.status}")
                        return False
        except Exception as e:
            logger.error(f"Ошибка при проверке RabbitMQ: {e}")
            return False
    
    async def check_redis_health(self) -> bool:
        """Проверяет состояние Redis"""
        try:
            redis = Redis.from_url(self.redis_url)
            await redis.ping()
            await redis.aclose()
            logger.info("Redis здоров")
            return True
        except Exception as e:
            logger.error(f"Ошибка при проверке Redis: {e}")
            return False
    
    async def restart_rabbitmq(self) -> bool:
        """Перезапускает RabbitMQ контейнер"""
        try:
            logger.info("Перезапуск RabbitMQ контейнера...")
            result = subprocess.run(
                ["docker-compose", "restart", "rabbitmq"],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                logger.info("RabbitMQ успешно перезапущен")
                return True
            else:
                logger.error(f"Ошибка при перезапуске RabbitMQ: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"Исключение при перезапуске RabbitMQ: {e}")
            return False
    
    async def wait_for_rabbitmq_startup(self, timeout: int = 120) -> bool:
        """Ждет запуска RabbitMQ после перезапуска"""
        logger.info(f"Ожидание запуска RabbitMQ (таймаут: {timeout}с)...")

        start_time = time.time()
        while time.time() - start_time < timeout:
            if await self.check_rabbitmq_health():
                logger.info("RabbitMQ успешно запустился")
                return True
            await asyncio.sleep(5)

    async def check_worker_health(self) -> bool:
        """Проверяет состояние dramatiq worker"""
        try:
            # Проверяем количество активных соединений
            username, password = self._extract_credentials()

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.management_url}/api/connections",
                    auth=aiohttp.BasicAuth(username, password),
                    timeout=10
                ) as response:
                    if response.status == 200:
                        connections = await response.json()
                        active_connections = len([c for c in connections if 'dramatiq' in c.get('client_properties', {}).get('product', '')])
                        logger.info(f"Активных dramatiq соединений: {active_connections}")
                        return active_connections > 0
                    else:
                        logger.warning(f"Не удалось получить информацию о соединениях: {response.status}")
                        return False
        except Exception as e:
            logger.error(f"Ошибка при проверке worker: {e}")
            return False
        
        logger.error("Таймаут ожидания запуска RabbitMQ")
        return False
    
    async def monitor_loop(self):
        """Основной цикл мониторинга"""
        logger.info("Запуск мониторинга RabbitMQ...")
        
        while True:
            try:
                # Проверяем здоровье RabbitMQ
                rabbitmq_healthy = await self.check_rabbitmq_health()
                redis_healthy = await self.check_redis_health()
                
                if not rabbitmq_healthy:
                    self.failure_count += 1
                    logger.warning(f"RabbitMQ нездоров (попытка {self.failure_count}/{self.max_failures})")
                    
                    if self.failure_count >= self.max_failures:
                        logger.error("Достигнуто максимальное количество неудач, перезапуск RabbitMQ")
                        
                        if await self.restart_rabbitmq():
                            # Ждем запуска
                            if await self.wait_for_rabbitmq_startup():
                                self.failure_count = 0
                                logger.info("RabbitMQ восстановлен")
                            else:
                                logger.error("Не удалось дождаться запуска RabbitMQ")
                        else:
                            logger.error("Не удалось перезапустить RabbitMQ")
                else:
                    # Сбрасываем счетчик неудач при успешной проверке
                    if self.failure_count > 0:
                        logger.info("RabbitMQ восстановлен, сброс счетчика неудач")
                        self.failure_count = 0
                
                # Ждем следующей проверки
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
                await asyncio.sleep(self.check_interval)

async def main():
    """Точка входа"""
    monitor = RabbitMQMonitor()
    await monitor.monitor_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Мониторинг остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        exit(1)

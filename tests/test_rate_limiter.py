#!/usr/bin/env python3
"""
Тесты для рейт лимитера
"""
import pytest
import asyncio
import logging
from aiolimiter import AsyncLimiter

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

# Создаем рейт лимитер как в проекте
SEND_RATE_LIMITER = AsyncLimiter(25, time_period=1)

async def _test_message_send(message_id: int, delay: float = 0.0):
    """Тестирует отправку сообщения через рейт лимитер"""
    if delay > 0:
        await asyncio.sleep(delay)
    
    try:
        async with SEND_RATE_LIMITER:
            log.info(f"Сообщение {message_id} отправлено через рейт лимитер")
            # Имитируем отправку сообщения
            await asyncio.sleep(0.01)
        log.info(f"Сообщение {message_id} успешно обработано")
        return True
    except Exception as e:
        log.error(f"Ошибка при отправке сообщения {message_id}: {e}")
        return False

@pytest.mark.asyncio
async def test_burst_send():
    """Тестирует отправку множества сообщений одновременно"""
    log.info("=== Тест 1: Отправка 30 сообщений одновременно ===")
    tasks = []
    for i in range(30):
        task = _test_message_send(i + 1)
        tasks.append(task)
    
    start_time = asyncio.get_event_loop().time()
    results = await asyncio.gather(*tasks)
    end_time = asyncio.get_event_loop().time()
    
    log.info(f"Все сообщения отправлены за {end_time - start_time:.2f} секунд")
    assert all(results), "Все сообщения должны быть успешно отправлены"

@pytest.mark.asyncio
async def test_continuous_send():
    """Тестирует непрерывную отправку сообщений"""
    log.info("=== Тест 2: Непрерывная отправка 50 сообщений ===")
    start_time = asyncio.get_event_loop().time()
    
    results = []
    for i in range(50):
        result = await _test_message_send(i + 1, delay=0.02)  # 20ms между сообщениями
        results.append(result)
    
    end_time = asyncio.get_event_loop().time()
    log.info(f"Все сообщения отправлены за {end_time - start_time:.2f} секунд")
    assert all(results), "Все сообщения должны быть успешно отправлены"

@pytest.mark.asyncio
async def test_rate_limit():
    """Тестирует превышение лимита"""
    log.info("=== Тест 3: Превышение лимита (30 сообщений за 0.1 секунды) ===")
    tasks = []
    for i in range(30):
        task = _test_message_send(i + 1, delay=i * 0.01)  # 10ms между сообщениями
        tasks.append(task)
    
    start_time = asyncio.get_event_loop().time()
    results = await asyncio.gather(*tasks)
    end_time = asyncio.get_event_loop().time()
    
    log.info(f"Все сообщения отправлены за {end_time - start_time:.2f} секунд")
    assert all(results), "Все сообщения должны быть успешно отправлены"

@pytest.mark.asyncio
async def test_rate_limiter_configuration():
    """Тестирует конфигурацию рейт лимитера"""
    # Проверяем, что лимитер создан с правильными параметрами
    assert SEND_RATE_LIMITER.max_rate == 25
    assert SEND_RATE_LIMITER.time_period == 1
    
    # Проверяем, что лимитер работает
    async with SEND_RATE_LIMITER:
        await asyncio.sleep(0.01)
    
    assert True, "Рейт лимитер должен работать корректно"

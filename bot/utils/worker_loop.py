import asyncio
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_worker_loop: Optional[asyncio.AbstractEventLoop] = None
_worker_loop_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def get_worker_loop() -> asyncio.AbstractEventLoop:
    """
    Returns a global asyncio event loop running in a background thread.
    Reference: https://docs.python.org/3/library/asyncio-task.html#asyncio.run_coroutine_threadsafe
    """
    global _worker_loop, _worker_loop_thread
    
    with _lock:
        if _worker_loop is None or _worker_loop.is_closed():
            _worker_loop = asyncio.new_event_loop()
            
            def run_loop(loop):
                asyncio.set_event_loop(loop)
                logger.info("背景 event loop 开始运行")
                try:
                    loop.run_forever()
                except Exception as e:
                    logger.error(f"Worker loop crashed: {e}")
                finally:
                    # Убеждаемся, что луп закрыт корректно
                    if not loop.is_closed():
                        loop.close()
                    logger.info("背景 event loop 已停止")
            
            _worker_loop_thread = threading.Thread(
                target=run_loop, 
                args=(_worker_loop,), 
                daemon=True,
                name="AsyncWorkerLoop"
            )
            _worker_loop_thread.start()
            logger.info("✅ Global persistent worker loop started")
        
        # Проверка здоровья лупа
        if not _worker_loop_thread.is_alive():
            logger.error("❌ Worker loop thread is dead! Attempting to restart...")
            _worker_loop = None # Сброс для перезапуска при следующем вызове
            return get_worker_loop()
            
    return _worker_loop


def stop_worker_loop():
    """Stops the global worker loop and joins the thread."""
    global _worker_loop
    if _worker_loop and _worker_loop.is_running():
        _worker_loop.call_soon_threadsafe(_worker_loop.stop)
        logger.info("Stopping worker loop...")


def run_async(coro):
    """
    Runs a coroutine in the global worker loop and waits for the result (synchronously).
    Timeout должен быть больше чем Dramatiq time_limit, чтобы позволить задаче завершиться.
    """
    try:
        loop = get_worker_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        # Timeout 1900 сек = ~31 мин (чуть больше consumer_timeout и time_limit в Dramatiq)
        return future.result(timeout=1900)
    except asyncio.TimeoutError:
        logger.error("Coroutine timed out in worker loop after 1900 seconds")
        raise
    except Exception as e:
        logger.error(f"Error running async task: {e}")
        raise

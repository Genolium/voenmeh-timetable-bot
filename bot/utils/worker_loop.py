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
        if _worker_loop is None:
            _worker_loop = asyncio.new_event_loop()
            
            def run_loop(loop):
                asyncio.set_event_loop(loop)
                try:
                    loop.run_forever()
                except Exception as e:
                    logger.error(f"Worker loop crashed: {e}")
                finally:
                    loop.close()
            
            _worker_loop_thread = threading.Thread(
                target=run_loop, 
                args=(_worker_loop,), 
                daemon=True,
                name="AsyncWorkerLoop"
            )
            _worker_loop_thread.start()
            logger.info("✅ Global persistent worker loop started")
            
    return _worker_loop


def run_async(coro):
    """
    Runs a coroutine in the global worker loop and waits for the result (synchronously).
    Useful for bridging logic in synchronous Dramatiq workers.
    """
    loop = get_worker_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    # This will block the current thread until the coroutine finishes
    return future.result()

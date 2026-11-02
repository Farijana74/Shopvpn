"""Own process signals so stopping polling also stops the service supervisor."""
import asyncio
import logging
import signal

logger = logging.getLogger(__name__)


async def run_service(main):
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    stopping = False

    def stop():
        nonlocal stopping
        if not stopping:
            stopping = True
            logger.info("Service shutdown requested")
            task.cancel()

    signals = (signal.SIGTERM, signal.SIGINT)
    for sig in signals:
        loop.add_signal_handler(sig, stop)
    try:
        await main()
    except asyncio.CancelledError:
        if not stopping:
            raise
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)

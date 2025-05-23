import signal
import asyncio
import logging
import logging.config
import yaml
import argparse

from event_loop import EventLoop

"""Driver module"""

logger = logging.getLogger(__name__)

def init_argparse() -> None:
    """Fetch the command line arguments and operate on them"""
    global logger
    parser = argparse.ArgumentParser(description='Event Loop')
    parser.add_argument('-d', '--debug', action='store_true', help='set the logging level to logging.DEBUG')
    args = parser.parse_args()
    with open('log_config.yml', 'r') as f:
        log_cfg = yaml.safe_load(f.read())
        if args.debug:
            log_cfg['root']['level'] = 'DEBUG'
        else:
            log_cfg['root']['level'] = 'INFO'
        logging.config.dictConfig(log_cfg)
        logger = logging.getLogger(__name__)
        logger.debug('DEBUG MODE ENABLED')

async def shutdown(signal=None):
    """Shutdown function to gracefully stop asyncio tasks and the event loop."""
    if signal:
        logger.info(f"Received exit signal {signal.name}...")
    else:
        logger.info("Shutdown initiated...")

    logger.info('Cancelling outstanding asyncio tasks...')
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]

    logger.info('Stopping the asyncio event loop...')
    loop = asyncio.get_event_loop()
    loop.stop()

    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Shutdown complete.")

def register_signals(loop):
    """Register signal handlers for graceful shutdown."""
    signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    for sig in signals:
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))

def main():
    """Main function that runs the application."""
    init_argparse()
    logger.debug('Initializing the event loop')
    event_loop = EventLoop()
    loop = asyncio.get_event_loop()

    # Register signal handlers for safe shutdown
    register_signals(loop)

    try:
        # Schedule the coroutines to run
        asyncio.ensure_future(event_loop.data_provider_loop())
        asyncio.ensure_future(event_loop.plugins_runner_loop())
        asyncio.ensure_future(event_loop.plugins_refresh_loop())
        asyncio.ensure_future(event_loop.plugins_health_check_loop())
        asyncio.ensure_future(event_loop.decision_maker_loop())
        logger.info("Starting the event loop...")
        loop.run_forever()
    except KeyboardInterrupt:
        logger.warning("Keyboard interrupt detected, shutting down...")
    finally:
        logger.info('Shutting down event loop')
        loop.run_until_complete(shutdown())
        loop.close()
        logger.info("Event loop closed")

if __name__ == '__main__':
    main()

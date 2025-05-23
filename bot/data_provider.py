import asyncio
import logging
import os
import time
import json
from typing import Dict, Optional
from observer import Observer, Observable
from storage import Storage
from database import PostgresDatabase
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Constants
DEFAULT_STREAM_NAME = "Data::Provider"
DEFAULT_PROVIDER_NAME = 'DataProvider'

class DataProvider(Observable):
    """Base data provider class for handling data and notifying observers.

    Supports stateful data storage in PostgreSQL and Redis, and backtesting capabilities
    while maintaining compatibility with existing observers and subclasses.
    """

    def __init__(
        self,
        stream_name: str = DEFAULT_STREAM_NAME,
        provider_name: str = DEFAULT_PROVIDER_NAME,
        retention_period: int = 30 * 24 * 3600  # 30 days in seconds
    ):
        """Initialize the DataProvider with storage and configuration.

        :param stream_name: Name of the Redis stream for data.
        :param provider_name: Name of the provider for stream identification.
        :param retention_period: Retention period for historical data in seconds.
        """
        super().__init__()
        self.redis = Storage()  # Uses REDIS_HOST and REDIS_PORT from env
        self.db = PostgresDatabase()  # Uses POSTGRES_* env variables
        self.stream_name = stream_name
        self.provider_name = provider_name
        self.retention_period = retention_period
        self.data = {}
        self.last_broadcast_time = 0.0
        self._init_db()
        # Initialize database asynchronously
        # asyncio.create_task(self._init_db())

    async def _init_db(self):
        """Initialize the PostgreSQL database asynchronously."""
        try:
            await self.db.init_db()
        except Exception as e:
            logger.error("Failed to initialize PostgreSQL database", error=str(e))

    def set_data(self, data: dict) -> None:
        """Sets the data to be used by observers.

        :param data: Dictionary object to be set.
        :type data: dict
        """
        if not isinstance(data, dict):
            logger.error("Data must be a dictionary", data_type=type(data).__name__)
            return
        self.data = data
        logger.debug("Data set", data_keys=list(data.keys()))

    def get_data(self) -> dict:
        """Gets the current data.

        :return: Dictionary object containing the current data.
        :rtype: dict
        """
        return self.data

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def stream(self, data: dict) -> None:
        """Sends data to the Redis stream and PostgreSQL database.

        :param data: Dictionary object to be sent to the stream.
        :type data: dict
        """
        try:
            if not isinstance(data, dict):
                raise ValueError("Data must be a dictionary")
            
            # Stream to Redis
            message_id = await self.redis.add_to_stream(self.provider_name, self.stream_name, data)
            logger.debug("Data streamed to Redis", message_id=message_id, stream=self.stream_name)

            # Save to PostgreSQL
            timestamp = time.time()
            for pair, pair_data in data.items():
                await self.db.save_market_data(
                    provider=self.provider_name,
                    pair=pair,
                    data={
                        "timestamp": timestamp,
                        "tick": pair_data.get("tick", {}),
                        "order_book": pair_data.get("order_book", {}),
                        "trades": pair_data.get("trades", {}),
                        "balance": pair_data.get("balance", {})
                    }
                )
            
            # Clean up old data
            await self.db.cleanup_old_data(timestamp - self.retention_period)
        except Exception as e:
            logger.error("Error publishing data", error=str(e), stream=self.stream_name)

    async def broadcast(self, data: dict, backtest_mode: bool = False, historical_data: Optional[Dict] = None) -> None:
        """Broadcasts data to all observers and streams it to storage.

        :param data: Data to be broadcasted and streamed.
        :type data: dict
        :param backtest_mode: Whether to run in backtest mode with historical data.
        :param historical_data: Historical data to use in backtest mode.
        """
        start_time = time.time()
        try:
            if backtest_mode and historical_data:
                self.set_data(historical_data)
            else:
                self.set_data(data)
                await self.stream(data)
            
            self.set_changed()
            await self.notify_observers()
            latency = time.time() - start_time
            self.last_broadcast_time = start_time
            logger.info(
                "Data broadcasted",
                mode="backtest" if backtest_mode else "live",
                latency=latency,
                data_keys=list(self.data.keys())
            )
        except Exception as e:
            logger.error("Error in broadcast", error=str(e), mode="backtest" if backtest_mode else "live")

    async def replay_historical_data(self, start_time: float, end_time: float, pair: str) -> None:
        """Replays historical data for backtesting.

        :param start_time: Start timestamp for historical data.
        :param end_time: End timestamp for historical data.
        :param pair: Trading pair to replay data for.
        """
        try:
            historical_data = await self.db.fetch_historical_data(self.provider_name, pair, start_time, end_time)
            if not historical_data:
                logger.warning("No historical data found", provider=self.provider_name, pair=pair)
                return
            
            for record in historical_data:
                await self.broadcast({}, backtest_mode=True, historical_data=record)
                # Simulate real-time delay (optional, adjustable)
                await asyncio.sleep(0.1)
            logger.info(
                "Historical data replay completed",
                provider=self.provider_name,
                pair=pair,
                records=len(historical_data)
            )
        except Exception as e:
            logger.error("Error replaying historical data", error=str(e), provider=self.provider_name, pair=pair)

    async def cleanup_old_data(self):
        """Clean up old data from storage based on retention period."""
        try:
            cutoff_time = time.time() - self.retention_period
            await self.db.cleanup_old_data(cutoff_time)
            logger.debug("Cleaned up old data", cutoff_time=cutoff_time)
        except Exception as e:
            logger.error("Error cleaning up old data", error=str(e))

    async def close(self):
        """Close database connections."""
        try:
            await self.db.close()
            logger.debug("Closed DataProvider storage connections")
        except Exception as e:
            logger.error("Error closing storage connections", error=str(e))

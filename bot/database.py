import asyncio
import asyncpg
import json
import logging
import os
from typing import List, Dict

logger = logging.getLogger(__name__)

class PostgresDatabase:
    """Handles storage of historical market data and state in PostgreSQL."""

    def __init__(self):
        """Initialize the PostgreSQL database connection using environment variables."""
        self.dsn = self._get_dsn()
        self.pool = None

    def _get_dsn(self) -> str:
        """Construct PostgreSQL DSN from environment variables."""
        host = os.getenv('POSTGRES_HOST', 'postgres')
        port = os.getenv('POSTGRES_PORT', '5432')
        db = os.getenv('POSTGRES_DB', 'tradingbot')
        user = os.getenv('POSTGRES_USER', 'botuser')
        password = os.getenv('POSTGRES_PASSWORD', 'botpassword')
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"

    async def init_db(self):
        """Initialize the PostgreSQL database and create tables."""
        try:
            self.pool = await asyncpg.create_pool(self.dsn)
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS market_data (
                        id SERIAL PRIMARY KEY,
                        provider TEXT NOT NULL,
                        pair TEXT NOT NULL,
                        timestamp DOUBLE PRECISION NOT NULL,
                        tick JSONB,
                        order_book JSONB,
                        trades JSONB,
                        balance JSONB
                    );
                    CREATE INDEX IF NOT EXISTS idx_market_data_provider_pair_timestamp
                    ON market_data (provider, pair, timestamp);
                """)
                logger.info("Initialized PostgreSQL database", dsn=self.dsn)
        except Exception as e:
            logger.error(f"Error initializing database: {e}", dsn=self.dsn)
            raise

    async def save_market_data(self, provider: str, pair: str, data: Dict):
        """Save market data to PostgreSQL.

        :param provider: Name of the data provider (e.g., Luno).
        :param pair: Trading pair (e.g., XBTMYR).
        :param data: Dictionary containing tick, order_book, trades, and balance.
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO market_data (provider, pair, timestamp, tick, order_book, trades, balance)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                """, (
                    provider,
                    pair,
                    data.get('timestamp', time.time()),
                    json.dumps(data.get('tick', {})),
                    json.dumps(data.get('order_book', {})),
                    json.dumps(data.get('trades', {})),
                    json.dumps(data.get('balance', {}))
                ))
                logger.debug(f"Saved market data for {provider} {pair}")
        except Exception as e:
            logger.error(f"Error saving market data: {e}")

    async def fetch_historical_data(self, provider: str, pair: str, start_time: float, end_time: float) -> List[Dict]:
        """Fetch historical market data from PostgreSQL.

        :param provider: Name of the data provider.
        :param pair: Trading pair.
        :param start_time: Start timestamp for data.
        :param end_time: End timestamp for data.
        :return: List of dictionaries containing historical data.
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT timestamp, tick, order_book, trades, balance
                    FROM market_data
                    WHERE provider = $1 AND pair = $2 AND timestamp BETWEEN $3 AND $4
                    ORDER BY timestamp ASC
                """, provider, pair, start_time, end_time)
                return [{
                    "timestamp": row['timestamp'],
                    "tick": json.loads(row['tick']),
                    "order_book": json.loads(row['order_book']),
                    "trades": json.loads(row['trades']),
                    "balance": json.loads(row['balance'])
                } for row in rows]
        except Exception as e:
            logger.error(f"Error fetching historical data: {e}")
            return []

    async def cleanup_old_data(self, cutoff_time: float):
        """Remove data older than the cutoff time.

        :param cutoff_time: Timestamp before which data should be deleted.
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    DELETE FROM market_data
                    WHERE timestamp < $1
                """, cutoff_time)
                logger.debug(f"Deleted data older than {cutoff_time}")
        except Exception as e:
            logger.error(f"Error cleaning up old data: {e}")

    async def close(self):
        """Close the PostgreSQL connection pool."""
        if self.pool:
            await self.pool.close()
            logger.info("Closed PostgreSQL connection pool")

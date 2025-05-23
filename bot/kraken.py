import asyncio
import logging
import os
import json
from data_provider import DataProvider
import ccxt.async_support as ccxt
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

class Kraken(DataProvider):
    """Data provider for the Kraken exchange."""

    def __init__(self):
        super().__init__()
        self.kraken_key = os.getenv('KRAKEN_KEY', self._get_config_key('KRAKEN', 'KRAKEN_KEY'))
        self.kraken_secret = os.getenv('KRAKEN_SECRET', self._get_config_key('KRAKEN', 'KRAKEN_SECRET'))

    def _get_config_key(self, section: str, key: str) -> str:
        """Fallback to config file if environment variable is not set."""
        try:
            import configparser
            config = configparser.ConfigParser()
            config.read("config.cfg")
            return config.get(section, key)
        except Exception as e:
            logger.error(f"Error reading {key} from config: {e}")
            return ""

    def connect(self):
        """Establish connection to the Kraken exchange."""
        try:
            self.exchange = ccxt.kraken({
                'apiKey': self.kraken_key,
                'secret': self.kraken_secret,
            })
            logger.debug('Connected to Kraken')
        except Exception as e:
            logger.error(f'Exception from CCXT.KRAKEN while attempting to connect: {e}')

    async def disconnect(self):
        """Close connection to the Kraken exchange."""
        try:
            await self.exchange.close()
            logger.debug('Disconnected from Kraken')
        except Exception as e:
            logger.error(f'Exception from CCXT.KRAKEN while attempting to disconnect: {e}')

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def fetch_balance(self, pair=None):
        """Fetch account balance from Kraken."""
        balance = {}
        try:
            balance = await self.exchange.fetch_balance()
            logger.info(f"Kraken balance keys: {balance.keys()}")
            logger.debug(f'Balance = {json.dumps(balance.get("total", {}), sort_keys=True, indent=4)}')
        except Exception as e:
            logger.error(f'Exception from Kraken fetch_balance: {e}')
            raise
        return balance

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def fetch_tick(self, pair):
        """Fetch ticker data for a trading pair from Kraken."""
        tick = {}
        try:
            tick = await self.exchange.fetch_ticker(symbol=str(pair))
            logger.debug(f'Tick: {json.dumps(tick, sort_keys=True, indent=4)}')
        except Exception as e:
            logger.error(f'Exception from Kraken fetch_tick for {pair}: {e}')
            raise
        return tick

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def fetch_order_book(self, pair):
        """Fetch order book for a trading pair from Kraken."""
        order_book = {}
        try:
            order_book = await self.exchange.fetch_order_book(symbol=str(pair))
            logger.debug(f'OrderBook: {json.dumps(order_book, sort_keys=True, indent=4)}')
        except Exception as e:
            logger.error(f'Exception from Kraken fetch_order_book for {pair}: {e}')
            raise
        return order_book

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def fetch_trades(self, pair):
        """Fetch recent trades for a trading pair from Kraken."""
        trades = {}
        try:
            trades = await self.exchange.fetch_trades(symbol=str(pair))
            logger.debug(f'Trades: {json.dumps(trades, sort_keys=True, indent=4)}')
        except Exception as e:
            logger.error(f'Exception from Kraken fetch_trades for {pair}: {e}')
            raise
        return trades

    async def collect(self):
        """Collect data (balance, ticker, order book, trades) for Kraken trading pairs."""
        pairs = ['BTC/USD', 'ETH/USD', 'XRP/USD']  # Kraken-specific pairs
        data = {}
        for pair in pairs:
            try:
                balance = await self.fetch_balance()
                tick = await self.fetch_tick(pair)
                orderbook = await self.fetch_order_book(pair)
                trades = await self.fetch_trades(pair)
                data.update({pair: {'tick': tick, 'order_book': orderbook, 'trades': trades, 'balance': balance}})
            except Exception as e:
                logger.error(f'Exception from CCXT.KRAKEN for pair {pair}: {e}')
        return data

    async def tick(self):
        """Main method to collect and broadcast data from Kraken."""
        data = None
        self.connect()
        try:
            data = await self.collect()
            if data:
                await super().broadcast(data)
            else:
                logger.info('No data provided from Kraken')
        except Exception as e:
            logger.error(f'Exception from Kraken Provider: {e}')
        finally:
            await self.disconnect()

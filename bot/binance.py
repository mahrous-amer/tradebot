import asyncio
import logging
import os
import json
from data_provider import DataProvider
import ccxt.async_support as ccxt
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

class Binance(DataProvider):
    """Data provider for the Binance exchange."""

    def __init__(self):
        super().__init__()
        self.binance_key = os.getenv('BINANCE_KEY', self._get_config_key('BINANCE', 'BINANCE_KEY'))
        self.binance_secret = os.getenv('BINANCE_SECRET', self._get_config_key('BINANCE', 'BINANCE_SECRET'))

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
        """Establish connection to the Binance exchange."""
        try:
            self.exchange = ccxt.binance({
                'apiKey': self.binance_key,
                'secret': self.binance_secret,
            })
            logger.debug('Connected to Binance')
        except Exception as e:
            logger.error(f'Exception from CCXT.BINANCE while attempting to connect: {e}')

    async def disconnect(self):
        """Close connection to the Binance exchange."""
        try:
            await self.exchange.close()
            logger.debug('Disconnected from Binance')
        except Exception as e:
            logger.error(f'Exception from CCXT.BINANCE while attempting to disconnect: {e}')

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def fetch_balance(self, pair=None):
        """Fetch account balance from Binance."""
        balance = {}
        try:
            balance = await self.exchange.fetch_balance()
            logger.info(f"Binance balance keys: {balance.keys()}")
            logger.debug(f'Balance = {json.dumps(balance.get("total", {}), sort_keys=True, indent=4)}')
        except Exception as e:
            logger.error(f'Exception from Binance fetch_balance: {e}')
            raise
        return balance

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def fetch_tick(self, pair):
        """Fetch ticker data for a trading pair from Binance."""
        tick = {}
        try:
            tick = await self.exchange.fetch_ticker(symbol=str(pair))
            logger.debug(f'Tick: {json.dumps(tick, sort_keys=True, indent=4)}')
        except Exception as e:
            logger.error(f'Exception from Binance fetch_tick for {pair}: {e}')
            raise
        return tick

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def fetch_order_book(self, pair):
        """Fetch order book for a trading pair from Binance."""
        order_book = {}
        try:
            order_book = await self.exchange.fetch_order_book(symbol=str(pair))
            logger.debug(f'OrderBook: {json.dumps(order_book, sort_keys=True, indent=4)}')
        except Exception as e:
            logger.error(f'Exception from Binance fetch_order_book for {pair}: {e}')
            raise
        return order_book

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def fetch_trades(self, pair):
        """Fetch recent trades for a trading pair from Binance."""
        trades = {}
        try:
            trades = await self.exchange.fetch_trades(symbol=str(pair))
            logger.debug(f'Trades: {json.dumps(trades, sort_keys=True, indent=4)}')
        except Exception as e:
            logger.error(f'Exception from Binance fetch_trades for {pair}: {e}')
            raise
        return trades

    async def collect(self):
        """Collect data (balance, ticker, order book, trades) for Binance trading pairs."""
        pairs = ['BTC/USDT', 'ETH/USDT', 'XRP/USDT']  # Binance-specific pairs
        data = {}
        for pair in pairs:
            try:
                balance = await self.fetch_balance()
                tick = await self.fetch_tick(pair)
                orderbook = await self.fetch_order_book(pair)
                trades = await self.fetch_trades(pair)
                data.update({pair: {'tick': tick, 'order_book': orderbook, 'trades': trades, 'balance': balance}})
            except Exception as e:
                logger.error(f'Exception from CCXT.BINANCE for pair {pair}: {e}')
        return data

    async def tick(self):
        """Main method to collect and broadcast data from Binance."""
        data = None
        self.connect()
        try:
            data = await self.collect()
            if data:
                await super().broadcast(data)
            else:
                logger.info('No data provided from Binance')
        except Exception as e:
            logger.error(f'Exception from Binance Provider: {e}')
        finally:
            await self.disconnect()

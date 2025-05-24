import asyncio
import logging
import os
import time
import json
from typing import Dict, Optional, List

import redis.asyncio as redis # Added for consumer
from observer import Observer, Observable
from storage import Storage # Existing sync Redis client for Storage.add_to_stream
from database import PostgresDatabase
from tenacity import retry, stop_after_attempt, wait_exponential

from common_models.market_data_models import Ticker, OrderBook, Trade
from common_models.commands_events_models import AccountBalanceEvent # Added for balance events

logger = logging.getLogger(__name__)

# Constants
DEFAULT_STREAM_NAME = "Data::Provider" 
DEFAULT_PROVIDER_NAME = 'DataProvider'
MAX_RECENT_TRADES = 20 

# Stream names for different connectors and event types
BINANCE_CONNECTOR_NAME = "binance_connector"
LUNO_CONNECTOR_NAME = "luno_connector"

MARKET_DATA_TYPES = ["ticker", "orderbook", "trades"]
BALANCE_EVENT_TYPE = "account_balance" # from connectors_redis_api.md

class DataProvider(Observable):
    """Base data provider class for handling data and notifying observers.
    Consumes market data and balance events from multiple exchange connectors via Redis Streams.
    """

    def __init__(
        self,
        stream_name: str = DEFAULT_STREAM_NAME,
        provider_name: str = DEFAULT_PROVIDER_NAME,
        retention_period: int = 30 * 24 * 3600  # 30 days in seconds
    ):
        """Initialize the DataProvider with storage and configuration."""
        super().__init__()
        self.redis = Storage() 
        self.db = PostgresDatabase()
        self.stream_name = stream_name
        self.provider_name = provider_name # Name for DataProvider's own output stream
        self.retention_period = retention_period
        self.data = {} # This will be populated by the market data consumer
        self.last_broadcast_time = 0.0
        
        redis_host = os.getenv('REDIS_HOST', 'redis')
        redis_port = int(os.getenv('REDIS_PORT', 6379))
        self.consumer_redis = redis.from_url(f"redis://{redis_host}:{redis_port}")
        logger.info(f"DataProvider: Consumer Redis client initialized for {redis_host}:{redis_port}")

        # Configuration for exchanges and symbols
        self.monitored_exchanges = {
            "binance": {
                "connector_name": BINANCE_CONNECTOR_NAME,
                "symbols": ["BTC/USDT", "ETH/USDT", "BNB/USDT"],
                "symbol_map": {"BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT", "BNBUSDT": "BNB/USDT"} # Stream symbol to standard
            },
            "luno": {
                "connector_name": LUNO_CONNECTOR_NAME,
                "symbols": ["XBT/ZAR", "ETH/ZAR"], # Standard format
                "symbol_map": {"XBTZAR": "XBT/ZAR", "ETHZAR": "ETH/ZAR"} # Stream symbol to standard
            }
        }
        
        self.market_data_stream_ids = {}
        self.aggregated_market_data: Dict[str, Dict[str, Dict]] = {} # e.g. {"binance": {"BTC/USDT": {"trades": []}}}

        for ex_name, ex_config in self.monitored_exchanges.items():
            self.aggregated_market_data[ex_name] = {
                symbol: {"trades": []} for symbol in ex_config["symbols"]
            }
            for std_symbol in ex_config["symbols"]:
                # Find the stream-specific symbol format (e.g., BTCUSDT for binance, XBTZAR for luno)
                stream_symbol = next((s_map_key for s_map_key, s_map_val in ex_config["symbol_map"].items() if s_map_val == std_symbol), std_symbol.replace('/', ''))
                for data_type in MARKET_DATA_TYPES:
                    stream_key = f"{ex_config['connector_name']}:{stream_symbol}:{data_type}"
                    self.market_data_stream_ids[stream_key] = "$"
        
        logger.info(f"DataProvider: Initialized Market Data Stream IDs: {self.market_data_stream_ids}")

        self.account_balances_cache: Dict[str, Dict[str, AccountBalanceEvent]] = {
            ex_name: {} for ex_name in self.monitored_exchanges
        }
        self.balance_event_stream_ids = {
            f"{ex_config['connector_name']}:events:{BALANCE_EVENT_TYPE}": "$"
            for ex_config in self.monitored_exchanges.values()
        }
        logger.info(f"DataProvider: Initialized Balance Event Stream IDs: {self.balance_event_stream_ids}")

        self._init_db()
        
        self.market_data_consumer_task = asyncio.create_task(self.consume_market_data_streams())
        logger.info("DataProvider: Market data stream consumer task created.")
        self.balance_event_consumer_task = asyncio.create_task(self.consume_account_balance_events())
        logger.info("DataProvider: Account balance event consumer task created.")

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

    async def consume_market_data_streams(self): # Renamed from consume_binance_streams
        logger.info("Starting Market Data stream consumer loop...")
        while True:
            try:
                messages = await self.consumer_redis.xread(
                    self.market_data_stream_ids, count=10, block=1000
                )
                
                if messages:
                    logger.debug(f"Received {len(messages)} messages from market data streams.")
                    something_changed = False
                    for stream_name_bytes, message_list in messages:
                        stream_name_str = stream_name_bytes.decode('utf-8')
                        # Expected format: <exchange_connector_name>:<STREAM_SYMBOL>:<data_type>
                        parts = stream_name_str.split(':')
                        if len(parts) != 3:
                            logger.warning(f"Unexpected stream name format: {stream_name_str}")
                            continue
                        
                        connector_name, stream_symbol, data_type = parts
                        
                        # Determine exchange and standard symbol from connector_name and stream_symbol
                        current_exchange_name = None
                        standard_symbol = None
                        for ex_name, ex_config in self.monitored_exchanges.items():
                            if ex_config["connector_name"] == connector_name:
                                current_exchange_name = ex_name
                                standard_symbol = ex_config["symbol_map"].get(stream_symbol)
                                break
                        
                        if not current_exchange_name or not standard_symbol:
                            logger.warning(f"Could not map stream {stream_name_str} to configured exchange/symbol.")
                            continue

                        for message_id_bytes, message_data_dict in message_list:
                            message_id_str = message_id_bytes.decode('utf-8')
                            try:
                                data_json = message_data_dict[b'data'].decode('utf-8')
                                parsed_data = json.loads(data_json)

                                if data_type == "ticker":
                                    model = Ticker(**parsed_data)
                                    self.aggregated_market_data[current_exchange_name][standard_symbol]['tick'] = model
                                    something_changed = True
                                elif data_type == "orderbook":
                                    model = OrderBook(**parsed_data)
                                    self.aggregated_market_data[current_exchange_name][standard_symbol]['order_book'] = model
                                    something_changed = True
                                elif data_type == "trades":
                                    model = Trade(**parsed_data)
                                    if 'trades' not in self.aggregated_market_data[current_exchange_name][standard_symbol]:
                                        self.aggregated_market_data[current_exchange_name][standard_symbol]['trades'] = []
                                    
                                    trade_list = self.aggregated_market_data[current_exchange_name][standard_symbol]['trades']
                                    trade_list.append(model)
                                    self.aggregated_market_data[current_exchange_name][standard_symbol]['trades'] = trade_list[-MAX_RECENT_TRADES:]
                                    something_changed = True
                                else:
                                    logger.warning(f"Unknown data type '{data_type}' from stream {stream_name_str}")

                                self.market_data_stream_ids[stream_name_str] = message_id_str
                                logger.debug(f"Processed message {message_id_str} from {stream_name_str} for {current_exchange_name}:{standard_symbol}")

                            except json.JSONDecodeError as e:
                                logger.error(f"JSON decode error for message in {stream_name_str}: {e}. Data: {message_data_dict.get(b'data')}")
                            except Exception as e: 
                                logger.error(f"Error processing message from {stream_name_str}: {e}. Data: {data_json if 'data_json' in locals() else message_data_dict.get(b'data')}")
                    
                    if something_changed:
                        # Construct the snapshot for observers, now potentially multi-exchange
                        # For now, the bot's plugins might expect a single exchange's data or a specific structure.
                        # This example flattens or prioritizes one exchange if plugins are not multi-exchange aware.
                        # A better approach would be to make plugins specify which exchange/symbol they need.
                        # For simplicity, we'll merge data, prioritizing by order in self.monitored_exchanges if symbols overlap.
                        # Or, structure self.data to be multi-exchange aware: self.data[exchange][symbol]
                        
                        # Let's make self.data multi-exchange aware:
                        # self.data = {"binance": {"BTC/USDT": {...}}, "luno": {"XBT/ZAR": {...}}}
                        
                        # Create a deep copy for self.data to avoid Pydantic models being modified by plugins
                        # and to ensure the structure is just dicts/lists for observers not expecting Pydantic.
                        data_for_observers = {}
                        db_save_timestamp = time.time()

                        for ex_name, ex_market_data in self.aggregated_market_data.items():
                            data_for_observers[ex_name] = {}
                            for sym, data_content in ex_market_data.items():
                                data_for_observers[ex_name][sym] = {
                                    'tick': data_content.get('tick').model_dump() if data_content.get('tick') else {},
                                    'order_book': data_content.get('order_book').model_dump() if data_content.get('order_book') else {},
                                    'trades': [t.model_dump() for t in data_content.get('trades', [])]
                                }
                                # Save to PostgreSQL for this exchange and symbol
                                try:
                                    await self.db.save_market_data(
                                        provider=f"{ex_name}_connector", # e.g., "binance_connector"
                                        pair=sym,
                                        data={
                                            "timestamp": db_save_timestamp,
                                            "tick": data_for_observers[ex_name][sym]['tick'],
                                            "order_book": data_for_observers[ex_name][sym]['order_book'],
                                            "trades": data_for_observers[ex_name][sym]['trades']
                                        }
                                    )
                                    logger.debug(f"Saved market data for {ex_name}:{sym} to PostgreSQL.")
                                except Exception as db_e:
                                    logger.error(f"Error saving market data for {ex_name}:{sym} to PostgreSQL: {db_e}")
                        
                        self.set_data(data_for_observers) # Update internal state for observers
                        
                        try:
                            await self.db.cleanup_old_data(db_save_timestamp - self.retention_period)
                        except Exception as cleanup_e:
                            logger.error(f"Error cleaning up old market data from PostgreSQL: {cleanup_e}")

                        self.set_changed()
                        await self.notify_observers()
                        logger.info("Market data updated from streams, saved to DB, and observers notified.")

            except redis.exceptions.ConnectionError as e:
                logger.error(f"Redis connection error in market data consumer loop: {e}. Attempting to reconnect...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Unexpected error in consume_market_data_streams: {e}")
                await asyncio.sleep(5)

    async def close(self):
        """Close database connections and consumer task/redis."""
        logger.info("Closing DataProvider resources...")
        if hasattr(self, 'market_data_consumer_task') and self.market_data_consumer_task: # Renamed
            if not self.market_data_consumer_task.done():
                self.market_data_consumer_task.cancel()
                try:
                    await self.market_data_consumer_task
                except asyncio.CancelledError:
                    logger.info("Market data stream consumer task cancelled.") # Renamed
                except Exception as e:
                    logger.error(f"Error during market data consumer task cancellation: {e}") # Renamed
        
        if hasattr(self, 'balance_event_consumer_task') and self.balance_event_consumer_task: # Renamed
            if not self.balance_event_consumer_task.done():
                self.balance_event_consumer_task.cancel()
                try:
                    await self.balance_event_consumer_task
                except asyncio.CancelledError:
                    logger.info("Account balance event consumer task cancelled.") # Renamed
                except Exception as e:
                    logger.error(f"Error during balance consumer task cancellation: {e}")

        if hasattr(self, 'consumer_redis') and self.consumer_redis:
            try:
                await self.consumer_redis.close()
                logger.info("Consumer Redis client closed.")
            except Exception as e:
                logger.error(f"Error closing consumer_redis: {e}")
        
        try:
            await self.db.close()
            logger.info("PostgreSQL database connection closed.")
        except Exception as e:
            logger.error("Error closing PostgreSQL connection", error=str(e))
        
        logger.info("DataProvider closed.")

    async def consume_account_balance_events(self): # Renamed from consume_binance_balance_events
        logger.info("Starting Account Balance event consumer loop...")
        while True:
            try:
                messages = await self.consumer_redis.xread(
                    streams=self.balance_event_stream_ids, # Updated to use multi-exchange dict
                    count=10,
                    block=1000 
                )
                if not messages:
                    continue

                for stream_name_bytes, message_list in messages:
                    stream_name_str = stream_name_bytes.decode('utf-8')
                    last_id_processed_for_stream = self.balance_event_stream_ids[stream_name_str]

                    for message_id_bytes, message_data_dict in message_list:
                        message_id_str = message_id_bytes.decode('utf-8')
                        try:
                            data_json = message_data_dict[b'data'].decode('utf-8')
                            event = AccountBalanceEvent.model_validate_json(data_json)
                            
                            # Ensure the exchange key exists in the cache
                            if event.exchange not in self.account_balances_cache:
                                self.account_balances_cache[event.exchange] = {}
                            
                            self.account_balances_cache[event.exchange][event.asset] = event
                            logger.info(f"Updated {event.exchange} balance for {event.asset}: Free={event.free}, Locked={event.locked}, Total={event.total}")
                            
                            last_id_processed_for_stream = message_id_str
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error for balance event message ID {message_id_str} in stream {stream_name_str}: {e}")
                        except Exception as e: 
                            logger.error(f"Error processing balance event message ID {message_id_str} from stream {stream_name_str}: {e}")
                    
                    self.balance_event_stream_ids[stream_name_str] = last_id_processed_for_stream
            
            except redis.exceptions.ConnectionError as e:
                logger.error(f"Redis connection error in balance event consumer: {e}. Attempting to reconnect...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Unexpected error in consume_account_balance_events: {e}")
                await asyncio.sleep(5)

    def get_account_balance(self, exchange: str, asset: str) -> Optional[AccountBalanceEvent]: # Renamed and signature updated
        """Retrieves the cached balance for a specific asset from a specific exchange."""
        return self.account_balances_cache.get(exchange, {}).get(asset)

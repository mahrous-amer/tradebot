import asyncio
import logging
import json
from abc import ABC, abstractmethod
from observer import Observer
from data_provider import DataProvider
from storage import Storage
from common_models.indicator_models import IndicatorValue # Added

class Plugin(Observer, ABC):
    """Generic Plugin base class."""

    def __init__(self, data_sink: DataProvider, exchange_id: str, symbol: str): # Modified
        """Initialize the Plugin.

        :param data_sink: Data provider for the plugin.
        :type data_sink: DataProvider
        :param exchange_id: The exchange this plugin instance targets (e.g., "binance", "luno").
        :type exchange_id: str
        :param symbol: The trading symbol this plugin instance targets (e.g., "BTC/USDT").
        :type symbol: str
        """
        self.name: str = "" # To be set by subclass, possibly dynamically
        self.description: str = ""
        self.input_data: dict = {} 
        # self.output_data: dict = {} # Removed, replaced by IndicatorValue publishing
        self.data_sink: DataProvider = data_sink
        self.exchange_id: str = exchange_id # Added
        self.symbol: str = symbol # Added
        self.is_running: bool = False
        # self.stream_name: str = "Plugins" # Removed, publishing to specific indicator streams
        self.redis: Storage = Storage() # Assumes self.redis.storage is the aioredis.Redis client
        self.logger: logging.Logger = logging.getLogger(self.__class__.__name__)

    async def heartbeat(self) -> bool:
        """Check if the plugin is responsive.

        :return: True if alive.
        :rtype: bool
        """
        return self.is_running

    async def update(self):
        """Update input data from the data provider."""
        self.input_data = self.data_sink.get_data()

    async def publish(self, indicator_name: str, value: float): # Modified
        """
        Constructs an IndicatorValue and publishes it to a dedicated Redis Stream.

        :param indicator_name: Name of the indicator (e.g., "rsi_14").
        :type indicator_name: str
        :param value: Calculated value of the indicator.
        :type value: float
        """
        indicator_event = IndicatorValue(
            value=value,
            indicator_name=indicator_name,
            symbol=self.symbol,
            exchange=self.exchange_id
            # timestamp is default_factory=utcnow in IndicatorValue model
        )
        
        # Stream name convention: indicators:<indicator_full_name>:<exchange_name>:<pair_symbol_concatenated>
        # Example: indicators:rsi_14:binance:BTCUSDT
        stream_key_symbol_part = self.symbol.replace('/', '')
        target_stream_name = f"indicators:{indicator_name}:{self.exchange_id}:{stream_key_symbol_part}"
        
        try:
            message_data = {"data": indicator_event.model_dump_json()}
            # Assuming self.redis.storage is the aioredis.Redis instance
            await self.redis.storage.xadd(target_stream_name, message_data)
            self.logger.debug(f"Plugin {self.name} published {indicator_name} for {self.exchange_id}:{self.symbol} (Value: {value}) to stream {target_stream_name}")
        except Exception as e:
            self.logger.exception(f"Error occurred while plugin {self.name} publishing {indicator_name} for {self.exchange_id}:{self.symbol} to stream {target_stream_name}: {e}")

    @abstractmethod
    async def main(self):
        """Abstract method to define the main logic.

        :raises NotImplementedError: Method must be implemented in subclass.
        """
        raise NotImplementedError

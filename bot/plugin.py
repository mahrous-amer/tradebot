import asyncio
import logging
import json
from abc import ABC, abstractmethod
from observer import Observer
from data_provider import DataProvider
from storage import Storage

class Plugin(Observer, ABC):
    """Generic Plugin base class."""

    def __init__(self, data_sink: DataProvider):
        """Initialize the Plugin.

        :param data_sink: Data provider for the plugin.
        :type data_sink: DataProvider
        """
        self.name: str = ""
        self.description: str = ""
        self.input_data: dict = {}
        self.output_data: dict = {}
        self.data_sink: DataProvider = data_sink
        self.is_running: bool = False
        self.stream_name: str = "Plugins"
        self.redis: Storage = Storage()
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

    async def publish(self, output_data: dict):
        """Publish output data to Redis stream.

        :param output_data: Data to be published.
        :type output_data: dict
        """
        self.output_data = output_data
        try:
            await self.redis.add_to_stream(self.name, self.stream_name, self.output_data)
        except Exception as e:
            self.logger.exception(f"Error occurred while publishing output_data by {self.name}")

    @abstractmethod
    async def main(self):
        """Abstract method to define the main logic.

        :raises NotImplementedError: Method must be implemented in subclass.
        """
        raise NotImplementedError

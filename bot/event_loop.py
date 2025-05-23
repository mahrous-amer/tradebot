import asyncio
import logging

from plugin_handler import PluginHandler
from data_provider import DataProvider
from decision_maker import DecisionMaker

from luno import Luno
from kraken import Kraken
from binance import Binance

PLUGIN_UPDATE_INTERVAL = 60
DATA_PROVIDER_INTERVAL = 1
DECISION_MAKER_INTERVAL = 60
PLUGIN_INVOKE_INTERVAL = 1
PLUGIN_HEALTH_CHECK_INTERVAL = 10

logger = logging.getLogger(__name__)

class EventLoop:
    """Manages the execution of plugins, data providers, and decision-making routines.

    :ivar data_provider: The primary data provider used for plugins.
    :type data_provider: DataProvider
    :ivar data_providers: List of data providers for fetching data from multiple exchanges.
    :type data_providers: list[DataProvider]
    :ivar plugin_handler: Handles plugin loading and management.
    :type plugin_handler: PluginHandler
    :ivar decision_maker: Manages decision-making processes.
    :type decision_maker: DecisionMaker
    """

    def __init__(self, config: dict = {}):
        """Initializes the EventLoop with the necessary components.

        Sets up the data providers, plugin handler, and decision maker.
        """
        # Initialize data providers for Luno, Kraken, and Binance
        self.data_providers = [
            Luno(),
            Kraken(),
            Binance()
        ]
        # Use the first provider (Luno) for plugins to match original behavior
        self.data_provider = self.data_providers[0]
        self.plugin_handler = PluginHandler('plugins', self.data_provider)
        self.decision_maker = DecisionMaker()

    async def plugins_health_check_loop(self) -> None:
        """Continuously check all enabled plugins.

        This loop updates the plugin handler at regular intervals.
        Displays the current responsiveness status of each loaded plugin.

        :raises Exception: If any error occurs during plugin execution.
        """
        while True:
            try:
                whois_alive = await self.plugin_handler.whois_alive()
                for plugin_name, is_alive in whois_alive.items():
                    status = "Running" if is_alive else "Not Running"
                    logger.info(f'Plugin: {plugin_name} is {status}')
            except Exception as e:
                logger.error(f'Error in plugins_health_check_loop: {e}', exc_info=True)
            await asyncio.sleep(PLUGIN_HEALTH_CHECK_INTERVAL)

    async def plugins_refresh_loop(self) -> None:
        """Continuously updates all enabled plugins.

        This loop updates the plugin handler at regular intervals.

        :raises Exception: If any error occurs during plugin execution.
        """
        while True:
            try:
                await self.plugin_handler.update()
                enabled_plugins = [p.name for p in self.plugin_handler.enabled_plugins]
                logger.info(f'Enabled plugins: {enabled_plugins}')
            except Exception as e:
                logger.error(f'Error in plugins_refresh_loop: {e}', exc_info=True)
            await asyncio.sleep(PLUGIN_UPDATE_INTERVAL)

    async def plugins_runner_loop(self) -> None:
        """Continuously executes all enabled plugins.

        This loop invokes the main function of each enabled plugin at regular intervals.

        :raises Exception: If any error occurs during plugin execution.
        """
        while True:
            try:
                await self.plugin_handler.invoke_callback('main')
                enabled_plugins = [p.name for p in self.plugin_handler.enabled_plugins]
                logger.info(f'Enabled plugins: {enabled_plugins}')
            except Exception as e:
                logger.error(f'Error in plugins_runner_loop: {e}', exc_info=True)
            await asyncio.sleep(PLUGIN_INVOKE_INTERVAL)

    async def data_provider_loop(self) -> None:
        """Continuously fetches and processes data from all data providers.

        This loop calls the `tick` method of each data provider at regular intervals.

        :raises Exception: If any error occurs during data processing.
        """
        while True:
            try:
                tasks = [provider.tick() for provider in self.data_providers]
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                logger.error(f'Error in data_provider_loop: {e}', exc_info=True)
                for provider in self.data_providers:
                    await provider.disconnect()
            await asyncio.sleep(DATA_PROVIDER_INTERVAL)

    async def decision_maker_loop(self) -> None:
        """Continuously makes decisions based on the decision maker.

        This loop invokes the `main` method of the decision maker at regular
        intervals to perform decision-making processes.

        :raises Exception: If any error occurs during decision making.
        """
        while True:
            try:
                await self.decision_maker.main()
            except Exception as e:
                logger.error(f'Error in decision_maker_loop: {e}', exc_info=True)
            await asyncio.sleep(DECISION_MAKER_INTERVAL)

import inspect
import os
import importlib.util
import asyncio
import aiofiles
import logging
from plugin import Plugin
from data_provider import DataProvider
from observer import Observable

logger = logging.getLogger(__name__)

class PluginHandler(Observable):
    """Container for managing plugins in a dedicated folder."""

    def __init__(self, plugin_folder: str, data_sink: DataProvider):
        """Initialize PluginHandler by locating and loading plugins.

        :param plugin_folder: Path to the plugin folder.
        :param data_sink: Data provider to be used by plugins.
        :type plugin_folder: str
        :type data_sink: DataProvider
        """
        self.plugin_folder = plugin_folder
        self.data_sink = data_sink
        self.plugins = []
        self.enabled_plugins = []
        self.completed_task = {}

    async def update(self) -> None:
        """Detect plugins in the plugin folder and handle changes asynchronously."""
        self.tmp_found_plugins = []
        await self.traverse_plugins(self.plugin_folder)

        # unload obsolete plugins
        await self.unload_obsolete_plugins()

        del self.tmp_found_plugins

    async def traverse_plugins(self, plugin_folder: str) -> None:
        """Asynchronously traverse the plugin folder to load .py files as plugins.

        :param plugin_folder: Folder containing plugin .py files (and subfolders).
        :type plugin_folder: str
        """
        logger.debug(f'Traversing plugin folder: {plugin_folder}')

        for root, dirs, files in os.walk(plugin_folder):
            for file in files:
                if file.endswith(".py") and not file.startswith("__"):
                    plugin_path = os.path.join(root, file)
                    module_name = file[:-3]  # strip .py extension
                    await self.load_plugin(plugin_path, module_name)

    async def load_plugin(self, file_path: str, module_name: str) -> None:
        """Dynamically load a plugin from a .py file asynchronously.

        :param file_path: Path to the .py plugin file.
        :param module_name: Name of the module to load.
        :type file_path: str
        :type module_name: str
        """
        try:
            # Use asyncio.to_thread to handle blocking importlib operations asynchronously
            module = await asyncio.to_thread(self.load_module, file_path, module_name)
            if not module:
                return

            loaded_plugins = {plugin.name for plugin in self.plugins}

            for _, cls in inspect.getmembers(module, inspect.isclass):
                if issubclass(cls, Plugin) and cls is not Plugin:
                    plugin_obj = cls(self.data_sink)
                    if plugin_obj.name not in loaded_plugins:
                        self.register_plugin(plugin_obj)
        except Exception as e:
            logger.error(f'Failed to load plugin {module_name} from {file_path}: {e}')

    def load_module(self, file_path: str, module_name: str):
        """Helper method to load a module using importlib."""
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None:
            logger.error(f"Could not load spec for {module_name} at {file_path}")
            return None

        module = importlib.util.module_from_spec(spec)
        # This is blocking, handled by asyncio.to_thread()
        spec.loader.exec_module(module)
        return module

    def register_plugin(self, plugin_obj: Plugin) -> None:
        """Register a new plugin, ensuring uniqueness."""
        logger.info(f'Loaded plugin class: {plugin_obj.__module__}.{plugin_obj.__class__.__name__}')
        self.plugins.append(plugin_obj)

        if plugin_obj.name not in [p.name for p in self.enabled_plugins]:
            self.enable(plugin_obj)

        self.tmp_found_plugins.append(plugin_obj.name)

    async def unload_obsolete_plugins(self) -> None:
        """Unload plugins that are no longer found in the plugin folder."""
        loaded_plugins = [plugin.name for plugin in self.plugins]
        obsolete_plugin_names = set(loaded_plugins) - set(self.tmp_found_plugins)
        obsolete_plugins = [plugin for plugin in self.plugins if plugin.name in obsolete_plugin_names]

        for plugin in obsolete_plugins:
            logger.info(f'Unloading plugin class: {plugin.name}.{plugin.__class__.__name__}')
            self.plugins.remove(plugin)
            if plugin in self.enabled_plugins:
                await self.disable(plugin)
            del plugin

    async def invoke_callback(self, *instr, plugin_list=None) -> dict:
        """Invoke a callback function on the specified plugins asynchronously.

        :param instr: Function name and arguments to pass.
        :param plugin_list: List of plugins to invoke the callback on. Defaults to enabled plugins.
        :return: Dictionary of plugins and their results.
        :rtype: dict
        """
        function, *args = instr
        plugin_list = plugin_list or self.enabled_plugins

        results = {}
        for plugin in plugin_list:
            if self.completed_task.get(plugin, None) and self.completed_task[plugin].done():
                callback = getattr(plugin, function, None)
                if callable(callback):
                    try:
                        task = asyncio.ensure_future(callback(*args))
                        self.completed_task[plugin] = task
                        results[plugin.name] = await task
                        logger.info(f'Executed {plugin.name}.{function} with args: {args}')
                    except Exception as e:
                        logger.error(f'Error invoking {plugin.name}.{function}: {e}')
        return results

    async def whois_alive(self) -> dict:
        """Check if each plugin is responsive asynchronously."""
        alive_status = await self.invoke_callback('heartbeat')
        logger.info(f'Completed heartbeat check: {alive_status}')
        return alive_status

    def get_plugin_obj(self, plugin_name: str) -> Plugin:
        """Retrieve the plugin object by its name."""
        return next((plugin for plugin in self.plugins if plugin.name == plugin_name), None)

    def is_enabled(self, plugin: Plugin) -> bool:
        """Check if a plugin is enabled."""
        return plugin in self.enabled_plugins

    async def disable(self, plugin: Plugin) -> bool:
        """Disable a plugin asynchronously."""
        if self.is_enabled(plugin):
            self.enabled_plugins.remove(plugin)
            await asyncio.to_thread(self.data_sink.delete_observer, plugin)
            logger.info(f'Disabled plugin {plugin.name}')
            return True
        return False

    def enable(self, plugin: Plugin) -> bool:
        """Enable a plugin."""
        if plugin not in self.enabled_plugins:
            self.enabled_plugins.append(plugin)
            self.data_sink.add_observer(plugin)
            self.completed_task[plugin] = asyncio.ensure_future(plugin.heartbeat())
            logger.info(f'Enabled plugin {plugin.name}')
            return True
        return False

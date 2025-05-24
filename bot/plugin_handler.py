import inspect
import os
import importlib.util
import asyncio
# import aiofiles # Not used in this refactoring
import logging
import json # Added for loading JSON config
from plugin import Plugin
from data_provider import DataProvider
from observer import Observable

logger = logging.getLogger(__name__)

class PluginHandler(Observable):
    """Container for managing plugins based on a configuration file."""

    def __init__(self, plugin_folder: str, data_sink: DataProvider, plugin_config_file: str = "config/plugin_configs.json"): # Modified
        """Initialize PluginHandler by loading plugins from a configuration file.

        :param plugin_folder: Base path to the plugin modules (e.g., "bot/plugins").
        :param data_sink: Data provider to be used by plugins.
        :param plugin_config_file: Path to the JSON file configuring plugin instances.
        """
        super().__init__() # Initialize Observable
        self.plugin_folder_base = plugin_folder # e.g., "bot/plugins"
        # Assuming technical_indicators are in a subfolder, adjust if structure is different
        # Example: plugin_folder could be "bot/plugins/technical_indicators" if JSON `module` is just "RSI"
        # For this implementation, we assume module in JSON means filename, and they are in a known subpath.
        # A common structure is plugins/technical_indicators/RSI.py
        # So, if plugin_folder = "plugins", module "RSI" could be in "plugins/technical_indicators/RSI.py"
        # The loading logic will need to derive the full path.
        # For simplicity, let's assume plugin_folder = "bot/plugins" and JSON module is like "technical_indicators.RSI"
        # Or, if plugin_folder = "bot/plugins/technical_indicators", JSON module can be just "RSI"

        self.data_sink = data_sink
        self.plugin_config_file = plugin_config_file
        self.plugins = [] # Stores all instantiated plugin objects
        self.enabled_plugins = [] # Stores only enabled plugin objects
        self.completed_task = {} # For tracking plugin tasks
        
        self.plugin_configurations = self._load_plugin_configurations()
        if self.plugin_configurations:
            self._register_configured_plugins()
        else:
            logger.warning("No plugin configurations loaded. No plugins will be active.")

    def _load_plugin_configurations(self) -> list:
        """Loads plugin configurations from the JSON file."""
        try:
            with open(self.plugin_config_file, 'r') as f:
                configs = json.load(f)
            logger.info(f"Successfully loaded {len(configs)} plugin configurations from {self.plugin_config_file}")
            return configs
        except FileNotFoundError:
            logger.error(f"Plugin configuration file not found: {self.plugin_config_file}")
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {self.plugin_config_file}: {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred while loading plugin configurations: {e}")
        return []

    def _register_configured_plugins(self):
        """Instantiates and registers plugins based on loaded configurations."""
        loaded_plugin_names = set() # To track names of successfully loaded and instantiated plugins

        for conf in self.plugin_configurations:
            if not conf.get("enabled", False):
                logger.info(f"Skipping disabled plugin configuration for module {conf.get('module')}, class {conf.get('class')}")
                continue

            module_name = conf.get("module")
            class_name = conf.get("class")
            config_params = conf.get("config", {})

            if not module_name or not class_name:
                logger.error(f"Invalid configuration entry: 'module' and 'class' are required. Entry: {conf}")
                continue
            
            try:
                # Construct module path. Assuming plugins are in subdirectories like 'technical_indicators'
                # under the self.plugin_folder_base.
                # Example: plugin_folder_base = "bot/plugins", module_name = "RSI" -> module_path = "bot.plugins.technical_indicators.RSI"
                # This assumes a structure like: bot/plugins/technical_indicators/RSI.py
                # The import path should be relative to the project root or PYTHONPATH.
                # If self.plugin_folder_base is "bot/plugins", and module "RSI" is in "bot/plugins/technical_indicators/RSI.py"
                # then the import name is "bot.plugins.technical_indicators.RSI"
                
                # Let's adjust the assumption: self.plugin_folder_base is "bot/plugins"
                # and the config "module" refers to the file name (e.g., "RSI", "SMA")
                # and these files are located in a known sub-package e.g. "technical_indicators"
                # Thus, the full module path would be e.g. "bot.plugins.technical_indicators.RSI"
                
                # Corrected module import logic:
                # Assuming self.plugin_folder_base = "bot/plugins"
                # And the actual plugin files are in "bot/plugins/technical_indicators/"
                # The module name in config ("RSI", "SMA") refers to the file.
                # The import path needs to be something like: bot.plugins.technical_indicators.RSI
                
                # Simplified: Assume module_name in config is the full path relative to project root if plugin_folder is not used directly for this.
                # Or, construct it. Let's assume plugin_folder is "bot/plugins/technical_indicators"
                # and module_name in JSON is "RSI"
                
                # Path for importlib.util.spec_from_file_location
                # Example: self.plugin_folder_base = "bot/plugins"
                # config module = "technical_indicators.RSI"
                # file_path = "bot/plugins/technical_indicators/RSI.py"
                # module_import_name = "bot.plugins.technical_indicators.RSI" (for inspection)
                
                # Let's assume plugin_folder = "bot/plugins" (passed to __init__)
                # And module in config is "technical_indicators.RSI" or just "RSI" if a default subfolder is assumed.
                # For this implementation, assume `module` in JSON is the direct filename "RSI", "SMA"
                # and they reside in a known subfolder, e.g., "technical_indicators" under `plugin_folder_base`.
                
                # This part is tricky and depends heavily on project structure and how `plugin_folder` is defined.
                # Let's assume `plugin_folder` is `bot/plugins/technical_indicators` for now.
                # So, if module_name is "RSI", file_path is `bot/plugins/technical_indicators/RSI.py`
                
                file_path = os.path.join(self.plugin_folder_base, f"{module_name}.py")
                # The module_name for importlib should be unique, often reflecting its path.
                # For spec_from_file_location, the 'name' argument can be arbitrary but good if it's the expected import name.
                # Let's use a unique name for loading, then get the class by its name.
                loader_module_name = f"plugin_loader_{module_name.lower()}" 
                
                spec = importlib.util.spec_from_file_location(loader_module_name, file_path)
                if spec is None:
                    logger.error(f"Could not create spec for module {module_name} at {file_path}")
                    continue
                
                module_obj = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module_obj) # This executes the module's code

                plugin_class = getattr(module_obj, class_name, None)

                if plugin_class and issubclass(plugin_class, Plugin):
                    plugin_instance = plugin_class(data_sink=self.data_sink, **config_params)
                    
                    # Check for duplicate plugin names (generated by plugin's __init__)
                    if plugin_instance.name in loaded_plugin_names:
                        logger.warning(f"Plugin with name '{plugin_instance.name}' already loaded. Skipping duplicate configuration for {module_name}.{class_name}.")
                        continue

                    self.register_plugin(plugin_instance) # Uses the old register_plugin logic
                    loaded_plugin_names.add(plugin_instance.name)
                else:
                    logger.error(f"Class '{class_name}' not found or not a subclass of Plugin in module '{module_name}'.")

            except FileNotFoundError:
                logger.error(f"Plugin file for module '{module_name}' not found at expected path: {file_path}")
            except AttributeError:
                 logger.error(f"Class '{class_name}' not found in module '{module_name}' at {file_path}.")
            except TypeError as te:
                logger.error(f"Error instantiating plugin {class_name} from module {module_name}. Check config params. Error: {te}")
            except Exception as e:
                logger.error(f"Failed to load or instantiate plugin from config {conf}: {e}", exc_info=True)


    async def update(self) -> None: # Modified
        """
        The primary mechanism for loading plugins is now config-driven at __init__.
        This method could be used for dynamic reloading of config in future, but not implemented.
        For now, it ensures that the internal state of enabled plugins matches what was loaded.
        """
        # The `self.plugins` and `self.enabled_plugins` are populated by _register_configured_plugins
        # No file system traversal or dynamic unloading/reloading based on file changes is done here anymore.
        # If dynamic updates are needed, this method would re-trigger config loading and diffing.
        logger.info("PluginHandler.update() called. Plugin loading is now config-driven at initialization.")
        # Ensure tmp_found_plugins is consistent if unload_obsolete_plugins were to be used (it's removed for now)
        # self.tmp_found_plugins = [p.name for p in self.plugins] 


    # Removed: traverse_plugins, load_plugin, load_module, unload_obsolete_plugins
    # These are replaced by the config-driven loading in __init__ and helper methods.

    def register_plugin(self, plugin_obj: Plugin) -> None: # Kept, but called by _register_configured_plugins
        """Register a new plugin instance."""
        logger.info(f"Registering plugin instance: {plugin_obj.name} (Class: {plugin_obj.__class__.__name__}, Exchange: {getattr(plugin_obj, 'exchange_id', 'N/A')}, Symbol: {getattr(plugin_obj, 'symbol', 'N/A')})")
        self.plugins.append(plugin_obj)

        # Enable plugin if it's not already (it should be, as we only process enabled configs)
        # and if its name is unique among currently enabled plugins.
        if plugin_obj.name not in [p.name for p in self.enabled_plugins]:
            self.enable(plugin_obj)
        else:
            # This case should ideally be prevented by the check in _register_configured_plugins
            logger.warning(f"Plugin with name '{plugin_obj.name}' was already enabled. This might indicate duplicate name generation logic in plugins.")


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

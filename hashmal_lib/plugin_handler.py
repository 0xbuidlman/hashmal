from functools import partial
from pkg_resources import iter_entry_points
from collections import OrderedDict
import sys

from PyQt4.QtGui import *
from PyQt4.QtCore import *

from gui_utils import required_plugins, default_plugins, add_shortcuts
from plugins.base import Category

class PluginHandler(QWidget):
    """Handles loading/unloading plugins and managing their dock widgets."""
    def __init__(self, main_window):
        super(PluginHandler, self).__init__(main_window)
        self.gui = main_window
        self.config = main_window.config
        self.loaded_plugins = []
        self.config.optionChanged.connect(self.on_option_changed)
        # Whether the initial plugin loading is done.
        self.plugins_loaded = False
        # Augmentations waiting until all plugins load.
        self.waiting_augmentations = []

    def get_plugin(self, plugin_name):
        for plugin in self.loaded_plugins:
            if plugin.name == plugin_name:
                return plugin
        return None

    def create_menu(self, menu):
        """Add plugins to menu."""
        _categories = OrderedDict()
        for c in sorted([x[0] for x in Category.categories()]):
            _categories[c] = []
        for plugin in self.loaded_plugins:
            _categories[plugin.dock.category[0]].append(plugin)

        shortcuts = add_shortcuts(_categories.keys())
        categories = OrderedDict()
        for k, v in zip(shortcuts, _categories.values()):
            categories[k] = v
        for i in categories.keys():
            plugins = categories[i]
            if len(plugins) == 0:
                continue
            category_menu = menu.addMenu(i)
            for plugin in sorted(plugins, key = lambda x: x.name):
                category_menu.addAction(plugin.dock.toggleViewAction())

    def load_plugins(self):
        """Load plugins from entry points."""
        for entry_point in iter_entry_points(group='hashmal.plugin'):
            plugin_maker = entry_point.load()
            plugin_instance = plugin_maker()

            dock_tool_name = plugin_instance.dock_class.tool_name
            plugin_instance.name = dock_tool_name if dock_tool_name else entry_point.name
            plugin_instance.instantiate_dock(self)

            self.loaded_plugins.append(plugin_instance)

        # Fail if core plugins aren't present.
        for req in required_plugins:
            if req not in [i.name for i in self.loaded_plugins]:
                print('Required plugin "{}" not found.\nTry running setup.py.'.format(req))
                sys.exit(1)

        self.update_enabled_plugins()
        self.plugins_loaded = True
        for i in self.waiting_augmentations:
            self.do_augment_hook(*i)

    def set_plugin_enabled(self, plugin_name, is_enabled):
        """Enable or disable a plugin and its dock."""
        plugin = self.get_plugin(plugin_name)
        if plugin is None:
            return

        # Do not disable required plugins.
        if not is_enabled and plugin_name in required_plugins:
            return

        dock = plugin.dock
        self.set_dock_signals(dock, is_enabled)
        dock.is_enabled = is_enabled
        if not is_enabled:
            dock.setVisible(False)
        self.assign_dock_shortcuts()

    def bring_to_front(self, dock):
        """Activate a dock by ensuring it is visible and raising it."""
        if not dock.is_enabled:
            return
        dock.setVisible(True)
        dock.raise_()

    def set_dock_signals(self, dock, do_connect):
        if do_connect:
            dock.needsFocus.connect(partial(self.bring_to_front, dock))
            dock.statusMessage.connect(self.gui.show_status_message)
        else:
            try:
                dock.needsFocus.disconnect()
                dock.statusMessage.disconnect()
            except TypeError:
                pass

    def assign_dock_shortcuts(self):
        """Assign shortcuts to visibility-toggling actions."""
        favorites = self.gui.config.get_option('favorite_plugins', [])
        for plugin in self.loaded_plugins:
            dock = plugin.dock
            # Keyboard shortcut
            shortcut = 'Alt+' + str(1 + favorites.index(plugin.name)) if plugin.name in favorites else ''
            dock.toggleViewAction().setShortcut(shortcut)
            dock.toggleViewAction().setEnabled(dock.is_enabled)
            dock.toggleViewAction().setVisible(dock.is_enabled)

    def add_plugin_actions(self, instance, menu, category, data):
        """Add the relevant actions to a context menu.

        Args:
            instance: Instance of class that is requesting actions.
            menu (QMenu): Context menu to add actions to.
            category (str): Category of actions (e.g. raw_transaction).
            data: Data to call the action(s) with.

        """
        separator_added = False
        for plugin in self.loaded_plugins:
            dock = plugin.dock
            if not dock.is_enabled:
                continue
            if dock.__class__ == instance.__class__:
                continue

            dock_actions = dock.get_actions(category)
            if dock_actions:
                # Add the separator before plugin actions.
                if not separator_added:
                    menu.addSeparator()
                    separator_added = True
                dock_menu = menu.addMenu(plugin.name)
                for action_name, action_receiver in dock_actions:
                    dock_menu.addAction(action_name, partial(action_receiver, data))

    def do_augment_hook(self, class_name, hook_name, data, callback=None):
        """Consult plugins that can augment hook_name."""
        if not self.plugins_loaded:
            augmentation = (class_name, hook_name, data, callback)
            if not augmentation in self.waiting_augmentations:
                self.waiting_augmentations.append(augmentation)
            return
        for plugin in self.loaded_plugins:
            dock = plugin.dock
            if class_name == dock.__class__.__name__:
                continue
            if hook_name in dock.augmenters:
                # Call the augmenter method.
                func = getattr(dock, hook_name)
                data = func(data)
                if callback:
                    callback(data)

    def evaluate_current_script(self):
        """Evaluate the script being edited with the Stack Evaluator tool."""
        script_hex = self.gui.script_editor.get_data('Hex')
        if not script_hex: return
        self.bring_to_front(self.get_plugin('Stack Evaluator').dock)
        self.get_plugin('Stack Evaluator').dock.tx_script.setPlainText(script_hex)
        self.get_plugin('Stack Evaluator').dock.do_evaluate()

    def do_default_layout(self):
        last_small = last_large = None
        for plugin in self.loaded_plugins:
            dock = plugin.dock
            # Large docks go to the bottom.
            if dock.is_large:
                self.gui.addDockWidget(Qt.BottomDockWidgetArea, dock)
                if last_large:
                    self.gui.tabifyDockWidget(last_large, dock)
                last_large = dock
            # Small docks go to the right.
            else:
                self.gui.addDockWidget(Qt.RightDockWidgetArea, dock)
                if last_small:
                    self.gui.tabifyDockWidget(last_small, dock)
                last_small = dock
            dock.setVisible(False)

        self.get_plugin('Variables').dock.setVisible(True)
        self.get_plugin('Stack Evaluator').dock.setVisible(True)

    def update_enabled_plugins(self):
        """Enable or disable plugin docks according to config file."""
        enabled_plugins = self.config.get_option('enabled_plugins', default_plugins)
        for plugin in self.loaded_plugins:
            is_enabled = plugin.name in enabled_plugins
            self.set_plugin_enabled(plugin.name, is_enabled)

    def on_option_changed(self, key):
        if key == 'enabled_plugins':
            self.update_enabled_plugins()
        elif self.loaded_plugins and key == 'favorite_plugins':
            self.assign_dock_shortcuts()


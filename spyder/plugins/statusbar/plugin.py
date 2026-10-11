# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
Status bar plugin.
"""

# Third-party imports
from qtpy.QtCore import Slot

# Local imports
from spyder.api.exceptions import SpyderAPIError
from spyder.api.plugins import Plugins, SpyderPluginV2
from spyder.api.plugin_registration.decorators import (
    on_plugin_available, on_plugin_teardown)
from spyder.api.translations import _
from spyder.api.config.decorators import on_conf_change
from spyder.api.widgets.status import StatusBarWidget
from spyder.config.base import running_under_pytest
from spyder.plugins.statusbar.confpage import StatusBarConfigPage
from spyder.plugins.statusbar.container import StatusBarContainer


class StatusBarWidgetPosition:
    Left = 0
    Right = -1


class StatusBar(SpyderPluginV2):
    """Status bar plugin."""

    NAME = 'statusbar'
    REQUIRES = [Plugins.Preferences]
    CONTAINER_CLASS = StatusBarContainer
    CONF_SECTION = NAME
    CONF_FILE = False
    CONF_WIDGET_CLASS = StatusBarConfigPage

    STATUS_WIDGETS = {}
    EXTERNAL_RIGHT_WIDGETS = {}
    EXTERNAL_LEFT_WIDGETS = {}
    INTERNAL_WIDGETS = {}
    INTERNAL_WIDGETS_IDS = {
        "clock_status",
        "cpu_status",
        "memory_status",
        "read_write_status",
        "eol_status",
        "encoding_status",
        "cursor_position_status",
        "vcs_status",
        "lsp_status",
        "pythonenv_status",
        "matplotlib_status",
        "update_manager_status",
        "inapp_appeal_status",
    }

    # ---- SpyderPluginV2 API
    @staticmethod
    def get_name():
        return _('Status Bar')

    @classmethod
    def get_icon(cls):
        return cls.create_icon('statusbar')

    @staticmethod
    def get_description():
        return _("Display the main window status bar.")

    def on_initialize(self):
        # --- Status widgets
        self.add_status_widget(self.mem_status, StatusBarWidgetPosition.Right)
        self.add_status_widget(self.cpu_status, StatusBarWidgetPosition.Right)
        self.add_status_widget(
            self.clock_status, StatusBarWidgetPosition.Right
        )

    def on_close(self, _unused):
        self._statusbar.setVisible(False)

    @on_plugin_available(plugin=Plugins.Preferences)
    def on_preferences_available(self):
        preferences = self.get_plugin(Plugins.Preferences)
        preferences.register_plugin_preferences(self)

    @on_plugin_teardown(plugin=Plugins.Preferences)
    def on_preferences_teardown(self):
        preferences = self.get_plugin(Plugins.Preferences)
        preferences.deregister_plugin_preferences(self)

    def after_container_creation(self):
        container = self.get_container()
        container.sig_show_status_bar_requested.connect(
            self.show_status_bar
        )
        # SmartOS (patch_spyder_statusbar_enable.py) : activation par widget de la barre d'etat.
        container.sig_status_widget_enable_requested.connect(
            self._smartos_apply_status_enable
        )

    # ---- Public API
    def add_status_widget(self, widget, position=StatusBarWidgetPosition.Left):
        """
        Add status widget to main application status bar.

        Parameters
        ----------
        widget: StatusBarWidget
            Widget to be added to the status bar.
        position: int
            Position where the widget will be added given the members of the
            StatusBarWidgetPosition enum.
        """
        # Check widget class
        if not isinstance(widget, StatusBarWidget):
            raise SpyderAPIError(
                'Any status widget must subclass StatusBarWidget!'
            )

        # Check ID
        id_ = widget.ID
        if id_ is None:
            raise SpyderAPIError(
                f"Status widget `{repr(widget)}` doesn't have an identifier!"
            )

        # Check it was not added before
        if id_ in self.STATUS_WIDGETS and not running_under_pytest():
            raise SpyderAPIError(f'Status widget `{id_}` already added!')

        if id_ in self.INTERNAL_WIDGETS_IDS:
            self.INTERNAL_WIDGETS[id_] = widget
        elif position == StatusBarWidgetPosition.Right:
            self.EXTERNAL_RIGHT_WIDGETS[id_] = widget
        else:
            self.EXTERNAL_LEFT_WIDGETS[id_] = widget

        self.STATUS_WIDGETS[id_] = widget
        self._statusbar.setStyleSheet('QStatusBar::item {border: None;}')

        if position == StatusBarWidgetPosition.Right:
            self._statusbar.addPermanentWidget(widget)
        else:
            self._statusbar.insertPermanentWidget(
                StatusBarWidgetPosition.Left, widget)
        self._statusbar.layout().setContentsMargins(0, 0, 0, 0)
        self._statusbar.layout().setSpacing(0)
        # SmartOS (patch_spyder_statusbar_enable.py) : appliquer l'activation par widget des
        # l'ajout - y compris pour les widgets ajoutes APRES _organize_status_widgets (ex.
        # lsp_status, a la connexion du serveur de langage), qui echapperaient sinon au
        # gate de disposition d'add_status_widget.
        widget.setVisible(self._smartos_status_enabled(id_))

    def remove_status_widget(self, id_):
        """
        Remove widget from status bar.

        Parameters
        ----------
        id_: str
            String identifier for the widget.
        """
        try:
            widget = self.get_status_widget(id_)
            self.STATUS_WIDGETS.pop(id_)
            self._statusbar.removeWidget(widget)
        except RuntimeError:
            # This can happen if the widget was already removed (tests fail
            # without this).
            pass

    def get_status_widget(self, id_):
        """
        Return an application status widget by name.

        Parameters
        ----------
        id_: str
            String identifier for the widget.
        """
        if id_ in self.STATUS_WIDGETS:
            return self.STATUS_WIDGETS[id_]
        else:
            raise SpyderAPIError(f'Status widget "{id_}" not found!')

    def get_status_widgets(self):
        """Return all status widgets."""
        return list(self.STATUS_WIDGETS.keys())

    def remove_status_widgets(self):
        """Remove all status widgets."""
        for w in self.get_status_widgets():
            self.remove_status_widget(w)

    @Slot(bool)
    def show_status_bar(self, value):
        """
        Show/hide status bar.

        Parameters
        ----------
        value: bool
            Decide whether to show or hide the status bar.
        """
        self._statusbar.setVisible(value)

    # ---- Default status widgets
    @property
    def mem_status(self):
        return self.get_container().mem_status

    @property
    def cpu_status(self):
        return self.get_container().cpu_status

    @property
    def clock_status(self):
        return self.get_container().clock_status

    # ---- Activation par widget de la barre d'etat (SmartOS, patch_spyder_statusbar_enable.py)
    def _smartos_status_enabled(self, id_):
        """Vrai si le widget <id_> de la barre d'etat doit etre visible, d'apres l'option
        statusbar/<id_>/enable. Defaut True pour tout widget non gere par SmartOS (update_manager,
        inapp_appeal, mem/cpu/heure...) : aucun impact sur eux."""
        return bool(self.get_conf(f"{id_}/enable", True))

    def _smartos_apply_status_enable(self, id_, value):
        """Applique en direct (sans redemarrage) l'activation d'un widget de la barre d'etat,
        puis montre la barre d'etat s'il reste au moins un widget actif, la masque sinon."""
        if id_ in self.STATUS_WIDGETS:
            self.STATUS_WIDGETS[id_].setVisible(bool(value))
        self._smartos_refresh_status_bar_visibility()

    def _smartos_refresh_status_bar_visibility(self):
        """Barre d'etat visible SSI au moins un de ses widgets est active (demande utilisateur :
        la barre suit la selection des widgets, pas de bascule separee)."""
        options = [
            "memory_usage/enable", "cpu_usage/enable", "clock/enable",
            "cursor_position_status/enable", "encoding_status/enable",
            "eol_status/enable", "vcs_status/enable", "lsp_status/enable",
            "read_write_status/enable", "pythonenv_status/enable",
            "matplotlib_status/enable",
        ]
        self._statusbar.setVisible(any(self.get_conf(o, False) for o in options))

    @on_conf_change(option="memory_usage/enable")
    def _smartos_on_mem_enable(self, value):
        self._smartos_refresh_status_bar_visibility()

    @on_conf_change(option="cpu_usage/enable")
    def _smartos_on_cpu_enable(self, value):
        self._smartos_refresh_status_bar_visibility()

    @on_conf_change(option="clock/enable")
    def _smartos_on_clock_enable(self, value):
        self._smartos_refresh_status_bar_visibility()

    # ---- Private API
    @property
    def _statusbar(self):
        """Reference to main window status bar."""
        return self._main.statusBar()

    def _organize_status_widgets(self):
        """
        Organize the status bar widgets once the application is loaded.
        """
        # Desired organization
        internal_layout = [
            "clock_status",
            "cpu_status",
            "memory_status",
            "read_write_status",
            "eol_status",
            "encoding_status",
            "cursor_position_status",
            "vcs_status",
            "lsp_status",
            "pythonenv_status",
            "matplotlib_status",
            "update_manager_status",
            "inapp_appeal_status",
        ]
        external_left = list(self.EXTERNAL_LEFT_WIDGETS.keys())

        # Remove all widgets from the statusbar, except the external right
        for id_ in self.INTERNAL_WIDGETS:
            self._statusbar.removeWidget(self.INTERNAL_WIDGETS[id_])

        for id_ in self.EXTERNAL_LEFT_WIDGETS:
            self._statusbar.removeWidget(self.EXTERNAL_LEFT_WIDGETS[id_])

        # Add the internal widgets in the desired layout
        for id_ in internal_layout:
            # This is needed in the case kite is installed but not enabled
            if id_ in self.INTERNAL_WIDGETS:
                self._statusbar.insertPermanentWidget(
                    StatusBarWidgetPosition.Left, self.INTERNAL_WIDGETS[id_]
                )
                self.INTERNAL_WIDGETS[id_].setVisible(
                    self._smartos_status_enabled(id_)
                )

        # Add the external left widgets
        for id_ in external_left:
            self._statusbar.insertPermanentWidget(
                StatusBarWidgetPosition.Left, self.EXTERNAL_LEFT_WIDGETS[id_]
            )
            self.EXTERNAL_LEFT_WIDGETS[id_].setVisible(
                self._smartos_status_enabled(id_)
            )

    def before_mainwindow_visible(self):
        """Perform actions before the mainwindow is visible"""
        # Organize widgets in the expected order
        self._statusbar.setVisible(False)
        self._organize_status_widgets()
        # SmartOS : auto-visibilite initiale de la barre d'etat (patch_spyder_statusbar_enable.py)
        # -> visible SSI au moins un widget est active (elle suit la selection des widgets).
        self._smartos_refresh_status_bar_visibility()

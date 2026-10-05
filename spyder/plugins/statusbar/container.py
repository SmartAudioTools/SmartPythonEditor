# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
Status bar container.
"""

# Third-party imports
from qtpy.QtCore import Signal

# Local imports
from spyder.api.config.decorators import on_conf_change
from spyder.api.widgets.main_container import PluginMainContainer
from spyder.plugins.statusbar.widgets.status import (
    ClockStatus, CPUStatus, MemoryStatus
)


class StatusBarContainer(PluginMainContainer):

    sig_show_status_bar_requested = Signal(bool)
    """
    This signal is emmitted when the user wants to show/hide the
    status bar.
    """

    sig_status_widget_enable_requested = Signal(str, bool)
    """
    SmartOS (patch_spyder_statusbar_enable.py) : demande d'activer/desactiver un widget de la
    barre d'etat par son ID (cases de Preferences > Barre d'etat).
    """

    def setup(self):
        # Basic status widgets
        self.mem_status = MemoryStatus(parent=self)
        self.cpu_status = CPUStatus(parent=self)
        self.clock_status = ClockStatus(parent=self)

    @on_conf_change(option='memory_usage/enable')
    def enable_mem_status(self, value):
        self.mem_status.setVisible(value)

    @on_conf_change(option='memory_usage/timeout')
    def set_mem_interval(self, value):
        self.mem_status.set_interval(value)

    @on_conf_change(option='cpu_usage/enable')
    def enable_cpu_status(self, value):
        self.cpu_status.setVisible(value)

    @on_conf_change(option='cpu_usage/timeout')
    def set_cpu_interval(self, value):
        self.cpu_status.set_interval(value)

    @on_conf_change(option='clock/enable')
    def enable_clock_status(self, value):
        self.clock_status.setVisible(value)

    @on_conf_change(option='clock/timeout')
    def set_clock_interval(self, value):
        self.clock_status.set_interval(value)

    @on_conf_change(option='show_status_bar')
    def show_status_bar(self, value):
        self.sig_show_status_bar_requested.emit(value)

    # ---- Activation par widget de la barre d'etat (SmartOS, patch_spyder_statusbar_enable.py)
    @on_conf_change(option='cursor_position_status/enable')
    def _smartos_enable_cursor_position_status(self, value):
        self.sig_status_widget_enable_requested.emit('cursor_position_status', value)

    @on_conf_change(option='encoding_status/enable')
    def _smartos_enable_encoding_status(self, value):
        self.sig_status_widget_enable_requested.emit('encoding_status', value)

    @on_conf_change(option='eol_status/enable')
    def _smartos_enable_eol_status(self, value):
        self.sig_status_widget_enable_requested.emit('eol_status', value)

    @on_conf_change(option='vcs_status/enable')
    def _smartos_enable_vcs_status(self, value):
        self.sig_status_widget_enable_requested.emit('vcs_status', value)

    @on_conf_change(option='lsp_status/enable')
    def _smartos_enable_lsp_status(self, value):
        self.sig_status_widget_enable_requested.emit('lsp_status', value)

    @on_conf_change(option='read_write_status/enable')
    def _smartos_enable_read_write_status(self, value):
        self.sig_status_widget_enable_requested.emit('read_write_status', value)

    @on_conf_change(option='pythonenv_status/enable')
    def _smartos_enable_pythonenv_status(self, value):
        self.sig_status_widget_enable_requested.emit('pythonenv_status', value)

    @on_conf_change(option='matplotlib_status/enable')
    def _smartos_enable_matplotlib_status(self, value):
        self.sig_status_widget_enable_requested.emit('matplotlib_status', value)

    def update_actions(self):
        pass

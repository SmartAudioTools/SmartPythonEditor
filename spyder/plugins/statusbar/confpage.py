# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
Status bar entry in Preferences.
"""

from qtpy.QtWidgets import QGridLayout, QGroupBox, QVBoxLayout

from spyder.api.translations import _
from spyder.api.preferences import PluginConfigPage


class StatusBarConfigPage(PluginConfigPage):

    def setup_page(self):
        newcb = self.create_checkbox

        # --- Status bar
        sbar_group = QGroupBox(_("Display"))

        memory_box = newcb(_("Show memory usage every"), 'memory_usage/enable')
        memory_spin = self.create_spinbox("", _(" ms"), 'memory_usage/timeout',
                                          min_=100, max_=1000000, step=100,
                                          tip=self.plugin.mem_status.toolTip())
        memory_box.checkbox.toggled.connect(memory_spin.setEnabled)
        memory_spin.setEnabled(self.get_option('memory_usage/enable'))

        cpu_box = newcb(_("Show CPU usage every"), 'cpu_usage/enable')
        cpu_spin = self.create_spinbox("", _(" ms"), 'cpu_usage/timeout',
                                       min_=100, max_=1000000, step=100,
                                       tip=self.plugin.cpu_status.toolTip())
        cpu_box.checkbox.toggled.connect(cpu_spin.setEnabled)
        cpu_spin.setEnabled(self.get_option('cpu_usage/enable'))

        clock_box = newcb(_("Show clock"), 'clock/enable')

        # Layout status bar
        cpu_memory_layout = QGridLayout()
        cpu_memory_layout.addWidget(memory_box, 0, 0)
        cpu_memory_layout.addWidget(memory_spin, 0, 1)
        cpu_memory_layout.addWidget(cpu_box, 1, 0)
        cpu_memory_layout.addWidget(cpu_spin, 1, 1)
        cpu_memory_layout.addWidget(clock_box, 2, 0)

        sbar_layout = QVBoxLayout()
        sbar_layout.addLayout(cpu_memory_layout)
        sbar_group.setLayout(sbar_layout)

        # Activation par widget (SmartOS, patch_spyder_statusbar_enable.py) : une case par
        # widget de la barre d'etat. Decoche par defaut (l'overlay par editeur reprend
        # ligne/colonne/encodage/fin de ligne). Chaque case pilote statusbar/<ID>/enable.
        widgets_group = QGroupBox(_("Widgets individuels de la barre d'etat"))
        smartos_box_0 = newcb(_("Position du curseur (ligne, colonne)"), 'cursor_position_status/enable')
        smartos_box_1 = newcb(_("Encodage du fichier"), 'encoding_status/enable')
        smartos_box_2 = newcb(_("Type de fin de ligne"), 'eol_status/enable')
        smartos_box_3 = newcb(_("Branche de controle de version (Git)"), 'vcs_status/enable')
        smartos_box_4 = newcb(_("Etat du serveur de langage (LSP)"), 'lsp_status/enable')
        smartos_box_5 = newcb(_("Indicateur lecture seule / ecriture"), 'read_write_status/enable')
        smartos_box_6 = newcb(_("Interpreteur Python de la console"), 'pythonenv_status/enable')
        smartos_box_7 = newcb(_("Backend graphique Matplotlib"), 'matplotlib_status/enable')
        widgets_layout = QVBoxLayout()
        for _b in (smartos_box_0, smartos_box_1, smartos_box_2, smartos_box_3, smartos_box_4, smartos_box_5, smartos_box_6, smartos_box_7):
            widgets_layout.addWidget(_b)
        widgets_group.setLayout(widgets_layout)

        vlayout = QVBoxLayout()
        vlayout.addWidget(sbar_group)
        vlayout.addWidget(widgets_group)
        vlayout.addStretch(1)
        self.setLayout(vlayout)

# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
Profiler Plugin.
"""

# Standard library imports
from typing import List

# Third party imports
from packaging.version import parse
from qtpy.QtCore import Qt  # SmartOS : F10 applicatif (cf. patch reroute)

# Local imports
from spyder.api.plugins import Plugins, SpyderDockablePlugin
from spyder.api.plugin_registration.decorators import (
    on_plugin_available, on_plugin_teardown)
from spyder.api.shellconnect.mixins import ShellConnectPluginMixin
from spyder.api.translations import _
from spyder.plugins.mainmenu.api import ApplicationMenus, RunMenuSections
from spyder.plugins.profiler.confpage import ProfilerConfigPage
from spyder.plugins.profiler.widgets.main_widget import ProfilerWidget
from spyder.plugins.toolbar.api import ApplicationToolbars
from spyder.plugins.ipythonconsole.api import IPythonConsolePyConfiguration
from spyder.plugins.run.api import (
    ExtendedRunExecutionParameters,
    RunConfiguration,
    RunContext,
    RunExecutor,
    RunResult,
    run_execute,
)
from spyder.plugins.ipythonconsole.widgets.run_conf import IPythonConfigOptions
from spyder.plugins.editor.api.run import CellRun, SelectionRun


# --- Plugin
# ----------------------------------------------------------------------------
class Profiler(SpyderDockablePlugin, ShellConnectPluginMixin, RunExecutor):
    """
    Profiler (after python's profile and pstats).
    """

    NAME = 'profiler'
    REQUIRES = [
        Plugins.Preferences,
        Plugins.IPythonConsole,
        Plugins.Run,
        Plugins.Toolbar,
    ]
    OPTIONAL = [Plugins.Editor, 'spyder_line_profiler']  # SmartOS : profilage combine
    TABIFY = [Plugins.VariableExplorer, Plugins.Help]
    WIDGET_CLASS = ProfilerWidget
    CONF_SECTION = NAME
    CONF_WIDGET_CLASS = ProfilerConfigPage
    CONF_FILE = False

    # ---- SpyderDockablePlugin API
    # -------------------------------------------------------------------------
    @staticmethod
    def get_name():
        return _("Profiler")

    @staticmethod
    def get_description():
        return _("Profile Python files to find execution bottlenecks.")

    @classmethod
    def get_icon(cls):
        return cls.create_icon('profiler')

    def on_initialize(self):

        self.python_editor_run_configuration = {
            'origin': self.NAME,
            'extension': ['py', 'ipy', 'pyw'],
            "contexts": [
                {"name": "File"},
                {"name": "Cell"},
                {"name": "Selection"},
            ],
        }

        self.executor_configuration = [
            {
                'input_extension': 'py',
                'context': {'name': 'File'},
                'output_formats': [],
                'configuration_widget': IPythonConfigOptions,
                'requires_cwd': True,
                'priority': 3
            },
            {
                'input_extension': 'ipy',
                'context': {'name': 'File'},
                'output_formats': [],
                'configuration_widget': IPythonConfigOptions,
                'requires_cwd': True,
                'priority': 10
            },
            {
                'input_extension': 'pyw',
                'context': {'name': 'File'},
                'output_formats': [],
                'configuration_widget': IPythonConfigOptions,
                'requires_cwd': True,
                'priority': 10
            },
            {
                'input_extension': ['py', 'ipy', 'pyw'],
                'context': {'name': 'Cell'},
                'output_formats': [],
                'configuration_widget': None,
                'requires_cwd': True,
                'priority': 10
            },
            {
                'input_extension': ['py', 'ipy', 'pyw'],
                'context': {'name': 'Selection'},
                'output_formats': [],
                'configuration_widget': None,
                'requires_cwd': True,
                'priority': 10
            },
        ]

    @on_plugin_available(plugin=Plugins.Run)
    def on_run_available(self):
        run = self.get_plugin(Plugins.Run)

        # Remove current parameters to recreate them due to the new
        # architecture for profiling introduced in PR spyder-ide/spyder#24794
        if (
            # This is needed when updgrading from Spyder 6.0 to 6.1
            (
                parse(self.old_spyder_conf_version) <= parse("87.3.0")
                and parse(self.spyder_conf_version) > parse("87.3.0")
            )
            # And this when downgrading from Spyder 6.1 to 6.0
            or (
                parse(self.old_spyder_conf_version) > parse("87.3.0")
                and parse(self.spyder_conf_version) <= parse("87.3.0")
            )
        ):
            all_execution_params = self.get_conf(
                "parameters", section="run", default={}
            )
            if self.NAME in all_execution_params:
                all_execution_params.pop(self.NAME)
                self.set_conf(
                    "parameters", all_execution_params, section="run"
                )

        run.register_executor_configuration(self, self.executor_configuration)

        run.create_run_in_executor_button(
            RunContext.File,
            self.NAME,
            text=_("Profile file"),
            tip=_("Profile file"),
            icon=self.create_icon('profiler'),
            shortcut_context=self.NAME,
            register_shortcut=True,
            # SmartOS : F10 doit partir depuis l'editeur, pas seulement quand le panneau
            # Profileur a le focus (defaut Qt.WidgetShortcut). Comme Shift+F10 du Line
            # Profiler, on rend le raccourci applicatif.
            shortcut_widget_context=Qt.ApplicationShortcut,
            add_to_menu={
                "menu": ApplicationMenus.Run,
                "section": RunMenuSections.Profile,
            },
            add_to_toolbar=ApplicationToolbars.Profile
        )

        run.create_run_in_executor_button(
            RunContext.Cell,
            self.NAME,
            text=_("Profile cell"),
            tip=_("Profile cell"),
            icon=self.create_icon('profile_cell'),
            shortcut_context=self.NAME,
            register_shortcut=True,
            add_to_menu={
                "menu": ApplicationMenus.Run,
                "section": RunMenuSections.Profile,
            },
            add_to_toolbar=ApplicationToolbars.Profile
        )

        run.create_run_in_executor_button(
            RunContext.Selection,
            self.NAME,
            text=_("Profile current line or selection"),
            tip=_("Profile current line or selection"),
            icon=self.create_icon('profile_selection'),
            shortcut_context=self.NAME,
            register_shortcut=True,
            add_to_menu={
                "menu": ApplicationMenus.Run,
                "section": RunMenuSections.Profile,
            },
            add_to_toolbar=ApplicationToolbars.Profile
        )

    @on_plugin_teardown(plugin=Plugins.Run)
    def on_run_teardown(self):
        run = self.get_plugin(Plugins.Run)
        run.deregister_executor_configuration(
            self, self.executor_configuration
        )

    @on_plugin_available(plugin=Plugins.Editor)
    def on_editor_available(self):
        widget = self.get_widget()
        editor = self.get_plugin(Plugins.Editor)

        editor.add_supported_run_configuration(
            self.python_editor_run_configuration
        )

        widget.sig_edit_goto_requested.connect(editor.load)

    @on_plugin_teardown(plugin=Plugins.Editor)
    def on_editor_teardown(self):
        widget = self.get_widget()
        editor = self.get_plugin(Plugins.Editor)

        editor.remove_supported_run_configuration(
            self.python_editor_run_configuration
        )

        widget.sig_edit_goto_requested.disconnect(editor.load)

    @on_plugin_available(plugin=Plugins.Preferences)
    def on_preferences_available(self):
        preferences = self.get_plugin(Plugins.Preferences)
        preferences.register_plugin_preferences(self)

    @on_plugin_teardown(plugin=Plugins.Preferences)
    def on_preferences_teardown(self):
        preferences = self.get_plugin(Plugins.Preferences)
        preferences.deregister_plugin_preferences(self)

    @on_plugin_available(plugin=Plugins.Toolbar)
    def on_toolbar_available(self):
        toolbar = self.get_plugin(Plugins.Toolbar)
        toolbar.create_application_toolbar(
            ApplicationToolbars.Profile, _("Profile toolbar")
        )

    @on_plugin_teardown(plugin=Plugins.Toolbar)
    def on_toolbar_teardown(self):
        toolbar = self.get_plugin(Plugins.Toolbar)
        toolbar.remove_application_toolbar(ApplicationToolbars.Profile)

    def on_mainwindow_visible(self):
        # Make plugin visible in case it's not but only once. For most users
        # this will display it in the UI when moving from 6.0 to 6.1
        if False:  # [SmartOS no-forced-switch] make_visible amont neutralise : il reaffichait le dock apres notre masquage par defaut
            if not self.get_widget().is_visible:
                self.get_widget().toggle_view(True)
            self.set_conf("make_visible", True)

        # SmartOS (patch_spyder_profiler_no_forced_switch.py) : meme garde que ci-dessus,
        # pour DESACTIVER switch_to_plugin UNE fois - son defaut amont (True) ramene ce dock
        # au premier plan a la fin de CHAQUE profilage, meme decoche d'Affichage > Panneaux,
        # contraire a la promesse de patch_spyder_hide_docks.py ("on respecte le choix de
        # l'utilisateur"). Ecrit le reglage UNE seule fois ; s'il le recoche ensuite depuis
        # Preferences > Profileur, ce choix est respecte aux lancements suivants.
        if not self.get_conf("smartos_switch_to_plugin_disabled_once", default=False):
            self.set_conf("switch_to_plugin", False)
            self.set_conf("smartos_switch_to_plugin_disabled_once", True)

    # ---- For execution
    # -------------------------------------------------------------------------
    @run_execute(context=RunContext.File)
    def profile_file(
        self,
        input: RunConfiguration,
        conf: ExtendedRunExecutionParameters
    ) -> List[RunResult]:

        # Ajout SmartOS (_smartos_lp) : aiguillage du profilage "fichier" (F10 / bouton
        # "Profiler le fichier"). Si le fichier lance a QUELQUE CHOSE a line-profiler (des marqueurs
        # n'importe ou, OU l'option "profiler tout le code utilisateur"), on lance le profilage
        # COMBINE (cProfile + lignes en une execution) qui remplit les deux panneaux ; sinon on
        # laisse le cProfile in-noyau d'origine (le corps ci-dessous) s'executer. Cf.
        # Commun/scripts/patch_spyder_profiler_reroute.py
        #
        # DECISION et LANCEMENT sont SEPARES a dessein (durcissement du 23/07/2026) :
        #   - la DECISION est sous try/except : si elle echoue, on se replie sur le cProfile
        #     integre, mais JAMAIS en silence. L'ancien `traceback.print_exc()` partait sur un
        #     stderr que personne ne lit (le processus graphique de Spyder l'envoie la ou il a ete
        #     lance, donc nulle part depuis le menu) ; il a fallu une soiree pour diagnostiquer un
        #     "le line profiler ne se lance plus" alors que toute la chaine etait intacte ;
        #   - le LANCEMENT est HORS du try : analyze() peut avoir DEJA demarre le sous-processus
        #     kernprof, donc enchainer sur le cProfile in-noyau REJOUERAIT le script - effets de
        #     bord en double (fichiers ecrits, requetes reseau). Une exception remonte alors a
        #     Spyder, qui l'affiche : bruyant, mais sans seconde execution.
        _smartos_cibles = None
        try:
            _smartos_lp = self.get_plugin('spyder_line_profiler', error=False)
            if _smartos_lp is not None:
                import os.path as _smartos_osp
                from spyder_line_profiler.spyder.profile_targets import (
                    config_lanceur as _smartos_config_lanceur)
                # config_lanceur honore les DEUX modes : des marqueurs (targets non vides,
                # n'importe ou) OU l'option "tout le code utilisateur" (all_user) => combine.
                _smartos_file = _smartos_osp.normpath(
                    _smartos_osp.abspath(input['run_input']['path']))
                _smartos_config = _smartos_config_lanceur(_smartos_file)
                _smartos_cibles = (bool(_smartos_config['targets'])
                                   or _smartos_config['all_user'])
        except Exception:
            import logging as _smartos_logging
            _smartos_logging.getLogger(__name__).exception(
                "SmartOS : la decision de reroutage vers le line profiler a echoue ; repli sur "
                "le cProfile integre. Des fonctions marquees seront ignorees.")
            _smartos_cibles = None
        if _smartos_cibles:
            _smartos_p = conf['params']
            _smartos_lp.get_widget().analyze(
                _smartos_file,
                wdir=_smartos_p['working_dir']['path'],
                args=_smartos_p['executor_params'].get('args'))
            return []
        console = self.get_plugin(Plugins.IPythonConsole)
        if console is None:
            return

        exec_params = conf['params']
        params: IPythonConsolePyConfiguration = exec_params['executor_params']
        params["run_method"] = "profilefile"

        return console.exec_files(input, conf)

    @run_execute(context=RunContext.Cell)
    def profile_cell(
        self,
        input: RunConfiguration,
        conf: ExtendedRunExecutionParameters
    ) -> List[RunResult]:
        console = self.get_plugin(Plugins.IPythonConsole)
        if console is None:
            return

        run_input: CellRun = input['run_input']
        if run_input['copy']:
            code = run_input['cell']
            if not code.strip():
                # Empty cell
                return

            console.run_selection("%%profile\n" + code)
            return

        exec_params = conf['params']
        params: IPythonConsolePyConfiguration = exec_params['executor_params']
        params["run_method"] = "profilecell"

        return console.exec_cell(input, conf)

    @run_execute(context=RunContext.Selection)
    def profile_selection(
        self,
        input: RunConfiguration,
        conf: ExtendedRunExecutionParameters
    ) -> List[RunResult]:

        console = self.get_plugin(Plugins.IPythonConsole)
        if console is None:
            return

        run_input: SelectionRun = input['run_input']
        code = run_input['selection']
        if not code.strip():
            # No selection
            return

        run_input['selection'] = "%%profile\n" + code

        return console.exec_selection(input, conf)

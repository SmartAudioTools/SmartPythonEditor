# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""Status bar widgets."""

# Standard library imports
import functools
import logging
import sys
import textwrap
import os.path as osp

# Third-party imports
from IPython.core import release as ipython_release
from qtpy.QtCore import QPoint, Signal
from qtpy.QtGui import QFontMetrics
from spyder_kernels.comms.frontendcomm import CommError
from spyder_kernels.utils.pythonenv import PythonEnvInfo, PythonEnvType

# Local imports
from spyder.api.shellconnect.status import ShellConnectStatusBarWidget
from spyder.api.translations import _
from spyder.api.widgets.menus import SpyderMenu
from spyder.config.base import running_in_ci
from spyder.utils.stylesheet import MAC, WIN
from spyder.utils.icon_manager import ima


logger = logging.getLogger(__name__)


class MatplotlibStatus(ShellConnectStatusBarWidget):
    """Status bar widget for current Matplotlib backend."""

    ID = "matplotlib_status"
    CONF_SECTION = 'ipython_console'
    INTERACT_ON_CLICK = True

    def __init__(self, parent):
        super().__init__(parent)

        self._gui = None
        self._interactive_gui = None

        # Signals
        self.sig_clicked.connect(self.toggle_matplotlib)

    # ---- StatusBarWidget API
    # -------------------------------------------------------------------------
    def get_tooltip(self):
        """Return localized tooltip for widget."""
        msg = _(
            "Click to toggle between inline and interactive Matplotlib "
            "plotting"
        )
        msg = '\n'.join(textwrap.wrap(msg, width=40))
        return msg

    def get_icon(self):
        return self.create_icon('plot')

    # ---- Public API
    # -------------------------------------------------------------------------
    def toggle_matplotlib(self):
        """Toggle matplotlib interactive backend."""
        if self.current_shellwidget is None or self._gui is None:
            return

        if self._gui != "inline":
            # Switch to inline for any backend that is not inline
            backend = "inline"
            self._interactive_gui = self._gui
        else:
            if self._interactive_gui is None:
                # Use the auto backend in case the interactive backend hasn't
                # been set yet
                backend = "auto"
            else:
                # Always use the interactive backend otherwise
                backend = self._interactive_gui

        sw = self.current_shellwidget
        sw.execute("%matplotlib " + backend)
        is_spyder_kernel = sw.is_spyder_kernel

        if not is_spyder_kernel:
            self.update_matplotlib_gui(backend)

    def update_matplotlib_gui(self, gui, shellwidget=None):
        """Update matplotlib interactive."""
        if shellwidget is None:
            shellwidget = self.current_shellwidget
            if shellwidget is None:
                return

        if shellwidget in self.shellwidget_to_status:
            self.shellwidget_to_status[shellwidget] = gui
            if shellwidget == self.current_shellwidget:
                self.update_status(gui)

    # ---- ShellConnectStatusBarWidget API
    # -------------------------------------------------------------------------
    def update_status(self, gui):
        """Update interactive state."""
        logger.debug(f"Setting Matplotlib backend to {gui}")

        if self._interactive_gui is None and gui != "inline":
            self._interactive_gui = gui
        self._gui = gui

        if gui == "inline":
            text = _("Inline")
        elif gui == "auto":
            text = _("Automatic")
        elif gui == "macosx":
            text = "macOS"
        else:
            text = gui.capitalize()

        self.set_value(text)

    def config_spyder_kernel(self, shellwidget):
        shellwidget.register_kernel_call_handler(
            "update_matplotlib_gui",
            functools.partial(
                self.update_matplotlib_gui, shellwidget=shellwidget
            )
        )
        shellwidget.set_kernel_configuration("update_gui", True)

    def on_kernel_start(self, shellwidget):
        """Actions to take when the kernel starts."""
        # Reset value of interactive backend
        self._interactive_gui = None

        # Avoid errors when running our test suite on Mac and Windows.
        # On Windows the following error appears:
        # `spyder_kernels.comms.commbase.CommError: The comm is not connected.`
        if running_in_ci() and not sys.platform.startswith("linux"):
            mpl_backend = "inline"
        else:
            # CommError: Needed when the comm is not connected.
            # Fixes spyder-ide/spyder#22194
            # TimeoutError: Prevent error that seems to happen sporadically.
            # Fixes spyder-ide/spyder#24865
            # RuntimeError: A remote console can be closed too quickly, which
            # raises a "Kernel is dead" error from comms.
            try:
                mpl_backend = shellwidget.get_matplotlib_backend()
            except (CommError, TimeoutError, RuntimeError):
                mpl_backend = None

        # Associate detected backend to shellwidget
        self.shellwidget_to_status[shellwidget] = mpl_backend

        # Hide widget if Matplotlib is not available or failed to import in the
        # kernel
        if mpl_backend is None:
            self.hide()
        else:
            self.set_shellwidget(shellwidget)
            # Backend Matplotlib (SmartOS, patch_spyder_statusbar_enable.py) : affiche seulement
            # si statusbar/matplotlib_status/enable est vrai (masque par defaut).
            from spyder.config.manager import CONF as _smartos_CONF
            if _smartos_CONF.get("statusbar", "matplotlib_status/enable", True):
                self.show()
            else:
                self.hide()

        # Ask the kernel to update the current backend, in case it has changed
        shellwidget.set_kernel_configuration("update_gui", True)

    def remove_shellwidget(self, shellwidget):
        """
        Overridden method to remove the call handler registered by this widget.
        """
        shellwidget.unregister_kernel_call_handler("update_matplotlib_gui")
        super().remove_shellwidget(shellwidget)


class PythonEnvironmentStatus(ShellConnectStatusBarWidget):
    """
    Status bar widget for displaying the Python environment used by the current
    console.
    """

    ID = 'pythonenv_status'
    CONF_SECTION = 'ipython_console'
    INTERACT_ON_CLICK = True

    sig_interpreter_changed = Signal(str)

    sig_open_preferences_requested = Signal()
    """
    Signal to open the main interpreter preferences.
    """

    def __init__(self, parent):
        self._current_env_info: PythonEnvInfo | None = None
        super().__init__(parent)
        self.menu = SpyderMenu(self)
        self.sig_clicked.connect(self.show_menu)

    # ---- StatusBarWidget API
    # -------------------------------------------------------------------------
    def get_tooltip(self):
        return self._current_env_info["path"] if self._current_env_info else ""

    # ---- ShellConnectStatusBarWidget API
    # -------------------------------------------------------------------------
    def update_status(self, env_info: dict):
        """Update env info."""
        if (
            # There's no need to emit this signal for remote consoles because
            # other plugins can only react to local interpreter changes.
            not self.current_shellwidget.is_remote()
            and env_info != self._current_env_info
        ):
            new_interpreter = env_info["path"]
            logger.debug(f"Console interpreter changed to {new_interpreter}")
            self.sig_interpreter_changed.emit(new_interpreter)

        self._current_env_info = env_info

        if env_info["env_type"] == PythonEnvType.Conda:
            env_type = "Conda"
        elif env_info["env_type"] == PythonEnvType.PyEnv:
            env_type = "Pyenv"
        elif env_info["env_type"] == PythonEnvType.Pixi:
            env_type = "Pixi"
        else:
            env_type = _("Custom")

        # The format to display is:
        # env_type: env_name (Python python_version)
        text = (
            # Prefixe "Custom:"/"Personnalise:" retire (SmartOS, cf.
            # Commun/scripts/patch_spyder_interpreter_prefix.py) - demande utilisateur.
            env_info["name"]
            + " (Python "
            + env_info["python_version"]
            + ")"
        )
        self.set_value(text)

    def on_kernel_start(self, shellwidget):
        """Actions to take when the kernel starts."""
        # Avoid errors when running our test suite on Mac and Windows.
        # On Windows the following error appears:
        # `spyder_kernels.comms.commbase.CommError: The comm is not connected.`
        if running_in_ci() and not sys.platform.startswith("linux"):
            env_info = PythonEnvInfo(
                path=sys.executable,
                env_type=PythonEnvType.Conda,
                name="foo",
                python_version=".".join(
                    [str(n) for n in sys.version_info[:3]]
                ),
                ipython_version=ipython_release.version,
                sys_version=sys.version,
            )
        else:
            # Handle any possible error.
            try:
                env_info = shellwidget.get_pythonenv_info()
            except Exception:
                env_info = None

        # Associate env info to shellwidget
        self.shellwidget_to_status[shellwidget] = env_info

        # Update status
        if env_info is None:
            self.hide()
        else:
            self.set_shellwidget(shellwidget)
            # Interpreteur de la console (SmartOS, patch_spyder_statusbar_enable.py) : affiche
            # seulement si statusbar/pythonenv_status/enable est vrai (masque par defaut, le
            # selecteur etant dans la barre d'outils "Interpreteur"). set_shellwidget ci-dessus
            # reste appele -> update_status et sig_interpreter_changed fonctionnent.
            from spyder.config.manager import CONF as _smartos_CONF
            self.setVisible(bool(
                _smartos_CONF.get("statusbar", "pythonenv_status/enable", True)))

    def show_menu(self):
        """Display a menu when clicking on the widget."""
        self.menu.clear_actions()

        # Liste des interpreteurs directement selectionnables ici, sans passer par les Preferences
        # (TODO du 18/07/2026, demande explicite de l'utilisateur). Reprend custom_interpreters_list,
        # deja peuplee/maintenue a jour par nos propres scripts d'installation (cf.
        # Commun/scripts/update_spyder_interpreters.py) plutot que par la detection pyenv de Spyder
        # (buggy avec l'architecture SmartPythons, cf. CachyOS - DONE.txt).
        current_path = (
            self._current_env_info["path"] if self._current_env_info else None
        )
        interpreters = self.get_conf(
            'custom_interpreters_list', default=[], section='main_interpreter'
        )

        def _interpreter_label(interpreter_path):
            # Affiche juste le nom de l'environnement pyenv (ex. "SmartKonsole") plutot que le
            # chemin complet ("/DATA/Python/SmartPython/CachyOS/versions/SmartKonsole/bin/python") -
            # convention pyenv-virtualenv : le chemin est toujours de la forme
            # ".../versions/<nom>/bin/python", donc le nom est le dossier 2 niveaux au-dessus de
            # l'executable. Si la structure ne correspond pas a cette convention (interpreteur
            # ajoute manuellement ailleurs), retourne le chemin complet tel quel plutot que de planter.
            try:
                label = osp.basename(osp.dirname(osp.dirname(interpreter_path)))
            except Exception:
                label = interpreter_path
            return label or interpreter_path

        # Tri alphabetique (insensible a la casse) sur le nom affiche, pas sur le chemin complet -
        # demande explicite de l'utilisateur.
        labeled_interpreters = sorted(
            ((_interpreter_label(p), p) for p in interpreters),
            key=lambda item: item[0].lower(),
        )
        for label, interpreter_path in labeled_interpreters:
            # icone coche verte (deja utilisee par Spyder pour indiquer un etat "valide/ok", cf.
            # icon_manager.py "dependency_ok") plutot qu'un prefixe texte - couleur issue de la
            # palette Spyder (SpyderPalette.COLOR_SUCCESS_2), donc coherente avec le theme
            # clair/sombre actif, contrairement a une couleur codee en dur.
            select_action = self.create_action(
                f"select_environment_{interpreter_path}",
                text=label,
                icon=ima.icon('dependency_ok') if interpreter_path == current_path else None,
                triggered=functools.partial(
                    self.select_interpreter, interpreter_path
                ),
                register_action=False,
            )
            self.add_item_to_menu(select_action, self.menu)

        text = _("Change default environment in Preferences...")
        change_action = self.create_action(
            "change_environment",
            text=text,
            triggered=self.open_interpreter_preferences,
            register_action=False,
        )
        self.add_item_to_menu(change_action, self.menu)

        x_offset = (
            # Margin of menu items to left and right
            2 * SpyderMenu.HORIZONTAL_MARGIN_FOR_ITEMS
            # Padding of menu items to left and right
            + 2 * SpyderMenu.HORIZONTAL_PADDING_FOR_ITEMS
        )
        y_offset = 4 if MAC else (3 if WIN else 2)

        metrics = QFontMetrics(self.font())
        rect = self.contentsRect()
        pos = self.mapToGlobal(
            rect.topLeft()
            + QPoint(
                -metrics.width(text) // 2 + x_offset,
                -2 * self.parent().height() + y_offset,
            )
        )

        self.menu.popup(pos)

    def select_interpreter(self, path):
        """Set the given interpreter as Spyder's default, without opening any dialog."""
        # Ne positionne pas 'executable' nous-memes : MainInterpreterContainer.on_interpreter_changed
        # (deja cablee, cf. spyder/plugins/maininterpreter/container.py) le calcule et l'ecrit
        # elle-meme a partir de ces 3 options - on reste sur le meme chemin que celui emprunte par
        # les Preferences, sans dupliquer sa logique.
        self.set_conf('custom_interpreter', path, section='main_interpreter')
        self.set_conf('default', False, section='main_interpreter')
        self.set_conf('custom', True, section='main_interpreter')

    def open_interpreter_preferences(self):
        """Request to open the main interpreter preferences."""
        self.sig_open_preferences_requested.emit()

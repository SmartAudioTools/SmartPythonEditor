# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
Layout Plugin.
"""
# Standard library imports
import configparser as cp
from functools import lru_cache
import logging
import os

# Third party imports
from qtpy.QtCore import Qt, QByteArray, QSize, QPoint, Slot
from qtpy.QtGui import QIcon, QKeySequence
from qtpy.QtWidgets import QApplication

# Local imports
from spyder.api.exceptions import SpyderAPIError
from spyder.api.plugins import (
    Plugins, DockablePlugins, SpyderDockablePlugin, SpyderPluginV2)
from spyder.api.plugin_registration.decorators import (
    on_plugin_available, on_plugin_teardown)
from spyder.api.plugin_registration.registry import PLUGIN_REGISTRY
from spyder.api.shortcuts import SpyderShortcutsMixin
from spyder.api.translations import _
from spyder.api.utils import get_class_values
from spyder.plugins.mainmenu.api import ApplicationMenus, WindowMenuSections
from spyder.plugins.layout.container import (
    LayoutContainer, LayoutContainerActions, LayoutPluginMenus)
from spyder.plugins.layout.layouts import (DefaultLayouts,
                                           HorizontalSplitLayout,
                                           MatlabLayout, RLayout,
                                           SpyderLayout, VerticalSplitLayout)
from spyder.plugins.preferences.api import PreferencesActions
from spyder.plugins.toolbar.api import (
    ApplicationToolbars, MainToolbarSections)
from spyder.utils.qthelpers import qbytearray_to_str


# For logging
logger = logging.getLogger(__name__)

# Number of default layouts available
DEFAULT_LAYOUTS = get_class_values(DefaultLayouts)

# ----------------------------------------------------------------------------
# ---- Window state version passed to saveState/restoreState.
# ----------------------------------------------------------------------------
# This defines the layout version used by different Spyder releases. In case
# there's a need to reset the layout when moving from one release to another,
# please increase the number below in integer steps, e.g. from 1 to 2, and
# leave a mention below explaining what prompted the change.
#
# The current versions are:
#
# * Spyder 4: Version 0 (it was the default).
# * Spyder 5.0.0: Version 1 (a bump was required due to the new API).
# * Spyder 5.1.0: Version 2 (a bump was required due to the migration of
#                            Projects to the new API).
# * Spyder 5.2.0: Version 3 (a bump was required due to the migration of the
#                            IPython Console to the new API)
# * Spyder 6.0.0: Version 4 (a bump was required due to the migration of
#                            Editor to the new API)

WINDOW_STATE_VERSION = 4


class Layout(SpyderPluginV2, SpyderShortcutsMixin):
    """
    Layout manager plugin.
    """
    NAME = "layout"
    CONF_SECTION = "quick_layouts"
    REQUIRES = [Plugins.All]  # Uses wildcard to require all plugins
    CONF_FILE = False
    CONTAINER_CLASS = LayoutContainer
    CAN_BE_DISABLED = False

    # ---- SpyderDockablePlugin API
    # -------------------------------------------------------------------------
    @staticmethod
    def get_name():
        return _("Layout")

    @staticmethod
    def get_description():
        return _("Layout manager")

    @classmethod
    def get_icon(cls):
        return QIcon()

    def on_initialize(self):
        self._last_plugin = None
        self._fullscreen_flag = None
        # The following flag remember the maximized state even when
        # the window is in fullscreen mode:
        self._maximized_flag = None
        # The following flag is used to restore window's geometry when
        # toggling out of fullscreen mode in Windows.
        self._saved_normal_geometry = None
        self._state_before_maximizing = None
        self._interface_locked = self.get_conf('panes_locked', section='main')
        # The following flag is used to apply the window settings only once
        # during the first run
        self._window_settings_applied_on_first_run = False

        # If Spyder has already been run once, this option needs to be False.
        # Note: _first_spyder_run needs to be accessed at least once in this
        # method to be computed at startup.
        if not self._first_spyder_run:
            self.set_conf("first_time", False)

        # Register default layouts
        self.register_layout(self, SpyderLayout)
        self.register_layout(self, RLayout)
        self.register_layout(self, MatlabLayout)
        self.register_layout(self, HorizontalSplitLayout)
        self.register_layout(self, VerticalSplitLayout)

        self._update_fullscreen_action()

    @on_plugin_available(plugin=Plugins.MainMenu)
    def on_main_menu_available(self):
        mainmenu = self.get_plugin(Plugins.MainMenu)
        container = self.get_container()
        # Add Panes related actions to Window application menu
        panes_items = [
            container._plugins_menu,
            container._lock_interface_action,
            container._maximize_dockwidget_action,
            container._close_dockwidget_action,
        ]
        for panes_item in panes_items:
            mainmenu.add_item_to_application_menu(
                panes_item,
                menu_id=ApplicationMenus.Window,
                section=WindowMenuSections.Pane,
                before_section=WindowMenuSections.Toolbar)
        # Add layouts menu to Window application menu
        layout_items = [
            container._layouts_menu,
            container._toggle_next_layout_action,
            container._toggle_previous_layout_action]
        for layout_item in layout_items:
            mainmenu.add_item_to_application_menu(
                layout_item,
                menu_id=ApplicationMenus.Window,
                section=WindowMenuSections.Layout,
                before_section=WindowMenuSections.Bottom)
        # Add fullscreen action to Window application menu
        mainmenu.add_item_to_application_menu(
            container._fullscreen_action,
            menu_id=ApplicationMenus.Window,
            section=WindowMenuSections.Bottom)

    @on_plugin_available(plugin=Plugins.Toolbar)
    def on_toolbar_available(self):
        container = self.get_container()
        toolbars = self.get_plugin(Plugins.Toolbar)
        # Add actions to Main application toolbar
        toolbars.add_item_to_application_toolbar(
            container._maximize_dockwidget_action,
            toolbar_id=ApplicationToolbars.Main,
            section=MainToolbarSections.ApplicationSection,
            before=PreferencesActions.Show
        )

    @on_plugin_teardown(plugin=Plugins.MainMenu)
    def on_main_menu_teardown(self):
        mainmenu = self.get_plugin(Plugins.MainMenu)
        # Remove Panes actions from the Window application menu
        panes_items = [
            LayoutPluginMenus.PluginsMenu,
            LayoutContainerActions.LockDockwidgetsAndToolbars,
            LayoutContainerActions.CloseCurrentDockwidget,
            LayoutContainerActions.MaximizeCurrentDockwidget]
        for panes_item in panes_items:
            mainmenu.remove_item_from_application_menu(
                panes_item,
                menu_id=ApplicationMenus.Window)
        # Remove layouts menu from the Window application menu
        layout_items = [
            LayoutPluginMenus.LayoutsMenu,
            LayoutContainerActions.NextLayout,
            LayoutContainerActions.PreviousLayout]
        for layout_item in layout_items:
            mainmenu.remove_item_from_application_menu(
                layout_item,
                menu_id=ApplicationMenus.Window)
        # Remove fullscreen action from the Window application menu
        mainmenu.remove_item_from_application_menu(
            LayoutContainerActions.Fullscreen,
            menu_id=ApplicationMenus.Window)

    @on_plugin_teardown(plugin=Plugins.Toolbar)
    def on_toolbar_teardown(self):
        toolbars = self.get_plugin(Plugins.Toolbar)

        # Remove actions from the Main application toolbar
        toolbars.remove_item_from_application_toolbar(
            LayoutContainerActions.MaximizeCurrentDockwidget,
            toolbar_id=ApplicationToolbars.Main
        )

    def before_mainwindow_visible(self):
        # Update layout menu
        self.update_layout_menu_actions()

        # The layout needs to be applied twice: before and after the main
        # window is visible (see below). This call avoids weird issues when the
        # window was not maximized in the last session. See:
        # https://github.com/spyder-ide/spyder/pull/22232#issuecomment-2224142496
        self.setup_layout(default=False)

    def on_mainwindow_visible(self):
        # Populate `Panes > Window` menu.
        # This **MUST** be done before restoring the last visible plugins, so
        # that works as expected.
        self.create_plugins_menu()

        # Setup layout when the window is visible.
        # This **MUST** be done after creating the plugins menu to correctly
        # restore the layout from the previous session.
        # Fixes spyder-ide/spyder#17945 and spyder-ide/spyder#21596
        self.setup_layout(default=False)

        # Correctly display dock tabbars.
        # This **MUST** be done after setting up the layout.
        self._apply_docktabbar_style()

        # Restore last visible plugins.
        # This **MUST** be done before running on_mainwindow_visible for the
        # other plugins so that the user doesn't experience sudden jumps in the
        # interface.
        self.restore_visible_plugins()

        # Update panes and toolbars lock status
        self.toggle_lock(self._interface_locked)

        # SmartOS (patch_spyder_deux_ecrans.py) : poser la barre « Écrans » et son bouton de
        # bascule, puis rendre le mode s'il etait actif a la fermeture precedente.
        from qtpy.QtCore import QTimer as _SmartosQTimerEcrans
        _SmartosQTimerEcrans.singleShot(0, self._smartos_installer_deux_ecrans)

        # SmartOS (patch_spyder_hide_docks.py) : masquer PAR DEFAUT les docks Line Profiler et
        # Debogueur, juges redondants / peu utilises (chapitre Dock2, demande utilisateur). On
        # les cache UNE seule fois - au premier lancement apres ce correctif - puis on respecte le
        # choix de l'utilisateur (s'il les reaffiche, ils restent affiches). Les greffons ne sont
        # PAS desactives (profilage F10 et timings dans l'editeur intacts).
        if not self.get_conf("smartos_docks_hidden_once", False):
            for _smartos_dock in ("spyder_line_profiler", "debugger"):
                _smartos_plugin = self.get_plugin(_smartos_dock, error=False)
                if _smartos_plugin is not None:
                    _smartos_plugin.get_widget().toggle_view(False)
            self.set_conf("smartos_docks_hidden_once", True)

        # SmartOS (2e passe, 26/07/2026, item 6 du TODO cosmetique) : meme mecanisme pour
        # VizTracer, Profileur, Historique et Terminal. DRAPEAU DISTINCT du precedent, qui est
        # deja a True : le reutiliser n'aurait rien masque, et le remettre a False aurait
        # re-masque Line Profiler et Debogueur que l'utilisateur a peut-etre reaffiches depuis.
        if not self.get_conf("smartos_docks_hidden_v2", False):
            for _smartos_dock in (
                "viztracer_profiler", "profiler", "historylog", "terminal"
            ):
                _smartos_plugin = self.get_plugin(_smartos_dock, error=False)
                if _smartos_plugin is not None:
                    _smartos_plugin.get_widget().toggle_view(False)
            self.set_conf("smartos_docks_hidden_v2", True)

        # SmartOS (patch_spyder_pane_maximize_button.py) : poser le bouton "Agrandir le volet" a
        # DROITE de l'en-tete des panneaux qui en ont l'usage, la barre d'outils globale etant
        # masquee. DIFFERE d'un tour de boucle : les panneaux doivent tous exister et avoir
        # construit leur en-tete.
        from qtpy.QtCore import QTimer as _SmartosQTimer
        _SmartosQTimer.singleShot(0, self._smartos_add_pane_maximize_buttons)

    #: SmartOS : panneaux dont le CONTENU gagne a occuper toute la fenetre (critere valide par
    #: l'utilisateur le 26/07/2026). La console, les fichiers, l'organisation du code et l'analyse
    #: de code en sont volontairement absents : ils sont etroits par nature.
    #: ⚠ "editor" N'Y EST PAS : l'Editeur n'a pas UNE barre d'onglets mais UNE PAR VOLET, et les
    #: volets naissent et meurent au fil des scissions - un bouton pose une fois pour toutes ne peut
    #: pas suivre (releve du 27/07/2026 : il ne servait que le volet du haut). C'est donc
    #: EditorStack qui cree le sien, cf. Commun/scripts/patch_spyder_editor_split_buttons.py.
    SMARTOS_PANNEAUX_AGRANDISSABLES = (
        "pyxel_game",
        "pyxel_studio",
        "python_tutor",
        "viztracer_profiler",
        "plots",
        # Ajoute le 27/07/2026, a la demande de l'utilisateur : « peux-tu ajouter le bouton
        # d'agrandissement pour le panneau Claude ? car la je ne peux pas verifier la mosaique ».
        # Il entre dans le critere sans discussion — c'est meme le seul panneau dont l'agrandissement
        # CHANGE le contenu : au-dela d'une session, il etale ses onglets en mosaique.
        "claude_pane",
        # Ajoute le 31/07/2026, meme demande pour le panneau Terminal : « comme pour le panneau
        # Claude, peux-tu ajouter un bouton d'agrandissement au panneau Terminal, qui permet de
        # l'agrandir et de passer en vue onglet ou mosaique ? »
        # ⚠ IL N'Y AVAIT RIEN A ECRIRE D'AUTRE QUE CETTE LIGNE, et c'est le seul point a
        # comprendre : la mosaique et sa bascule ont ete recopiees dans ce panneau le meme jour,
        # quand les deux greffons sont devenus independants. Elles etaient donc deja la, mais
        # INATTEIGNABLES — la mosaique ne s'affiche que dans un panneau agrandi, et ce panneau
        # n'avait aucun moyen de l'etre. Le bouton ne l'ajoute pas, il l'ouvre.
        "native_terminal",
    )

    def _smartos_add_pane_maximize_buttons(self):
        """
        Poser l'action d'agrandissement dans l'en-tete de chaque panneau retenu, calee a droite, et
        sur la ligne des titres d'onglets quand le panneau en a.
        Cf. Commun/scripts/patch_spyder_pane_maximize_button.py pour le detail et les pieges.
        """
        action = self.get_container()._maximize_dockwidget_action

        for _nom in self.SMARTOS_PANNEAUX_AGRANDISSABLES:
            try:
                plugin = self.get_plugin(_nom, error=False)
                if plugin is None:
                    continue
                widget = plugin.get_widget()
                if getattr(widget, "_smartos_bouton_agrandir", None) is not None:
                    continue  # deja pose (methode rejouee)

                bouton = self._smartos_poser_bouton_agrandir(widget, action)
                if bouton is None:
                    continue
                widget._smartos_bouton_agrandir = bouton

                # ⚠ FOND GRISE A L'ETAT COCHE (panneau agrandi) : meme demande de l'utilisateur,
                # meme jour, que pour le bouton "Mode deux ecrans" - garder un fond transparent.
                # Cf. patch_spyder_deux_ecrans.py pour la meme regle, ET pour le meme piege : un
                # type-selecteur nu (QToolButton:checked) perd face a la feuille de style plus
                # SPECIFIQUE que Spyder pose au niveau de l'application - releve de l'utilisateur,
                # le fond restait gris malgre ce style. Nom d'objet UNIQUE par panneau, cible par
                # selecteur d'ID (specificite maximale). Une SEULE action partagee
                # (`_maximize_dockwidget_action`) alimente les 7 boutons de ce patch : la regle
                # doit donc etre reposee sur CHACUN d'eux, pas une seule fois.
                bouton.setObjectName(f"smartos_bouton_agrandir_{_nom}")
                bouton.setStyleSheet(
                    f"QToolButton#smartos_bouton_agrandir_{_nom}"
                    " { background-color: transparent; border: none; } "
                    f"QToolButton#smartos_bouton_agrandir_{_nom}:checked"
                    " { background-color: transparent; border: none; } "
                    f"QToolButton#smartos_bouton_agrandir_{_nom}:hover"
                    " { background-color: transparent; border: none; } "
                    f"QToolButton#smartos_bouton_agrandir_{_nom}:pressed"
                    " { background-color: transparent; border: none; }"
                )

                # ⚠ maximize_dockwidget() choisit sa cible par QApplication.focusWidget(), qui n'a
                # pas le temps de refleter un changement survenu dans le MEME clic (synchrone) que
                # le `toggled` qui suit `pressed` et invoque maximize_dockwidget() - decouvert le
                # 01/08/2026 (TODO - Spyder - General.txt, bug rapporte sur le mode deux ecrans) :
                # sans repli, ce focus perime fait retomber l'agrandissement sur l'Editeur (mono
                # fenetre) ou sur la MAUVAISE FENETRE (mode deux ecrans). On retient donc
                # explicitement, ICI, QUEL panneau vient d'etre presse - cf.
                # _smartos_bouton_agrandir_presse plus bas.
                bouton.pressed.connect(
                    lambda _p=plugin, _a=action: self._smartos_bouton_agrandir_presse(_p, _a)
                )
            except Exception:
                # Un panneau recalcitrant ne doit pas priver les autres de leur bouton, ni casser le
                # demarrage pour un ajout cosmetique.
                logger.debug("Bouton d'agrandissement non pose sur %s", _nom, exc_info=True)

            # Hors du try qui precede : que le bouton ait pu etre pose ou non, un burger vide ne doit
            # pas rester affiche. C'est une demande a part entiere de l'utilisateur, elle ne doit pas
            # tomber avec l'echec de l'autre.
            try:
                self._smartos_masquer_burger_vide(widget)
            except Exception:
                logger.debug("Burger vide non masque sur %s", _nom, exc_info=True)

    def _smartos_bouton_agrandir_presse(self, plugin, action):
        """
        Reagir au clic sur UN bouton d'agrandissement precis (`pressed`, avant que l'action ne
        bascule). Retient explicitement CE panneau (`_smartos_bouton_agrandir_cible`), consomme en
        UNE FOIS par `maximize_dockwidget()` (patch_spyder_deux_ecrans_maximize.py) a la place d'un
        focus Qt pas encore a jour. None quand l'action est deja cochee (le clic veut dire "revenir
        en arriere") : ce cas ne doit pas influencer une future selection.

        `switch_to_plugin(force_focus=True)` RESTE NECESSAIRE ICI, et ce n'est PAS ce qui causait le
        bug "Hierarchie" releve par l'utilisateur le 01/08/2026 - hypothese emise a l'epoque (son
        appel a `maximize_dockwidget()` avant celui du bouton, dans le meme clic, semblait un
        candidat plausible), RETIREE puis VERIFIEE FAUSSE par un test A/B en direct
        (HGIGNORED/scenario_test_switch_to_plugin_hypothese.json) : avec l'appel, un panneau deja
        agrandi ailleurs est proprement desagrandi puis le bon panneau est agrandi ; SANS l'appel,
        cliquer ce bouton alors qu'autre chose est deja agrandi ne fait plus RIEN DU TOUT (le clic
        retombe sur la restauration de l'ancien agrandissement, jamais sur le nouveau) - une
        regression reelle, plus genante que le bug qu'on croyait corriger. La cause du bug Hierarchie
        etait ailleurs (disposition de fenetre corrompue par des essais anterieurs, cf. DONE - Spyder
        - cosmetique et disposition.txt).
        """
        self._smartos_bouton_agrandir_cible = None if action.isChecked() else plugin
        if not action.isChecked():
            plugin.switch_to_plugin(force_focus=True)

    @staticmethod
    def _smartos_ligne_des_onglets(widget):
        """
        Le coin haut-droit d'une barre d'onglets VISIBLE, ou None.

        L'utilisateur veut le bouton "a la meme hauteur que les titres d'onglets" quand il y en a.
        Le critere est donc la VISIBILITE DE LA BARRE D'ONGLETS, et non la seule existence d'un
        widget a onglets : Pyxel (panneau du jeu) en contient un dont la barre est masquee - y poser
        le bouton l'aurait mis dans une ligne invisible. Releve du 26/07/2026 : barre visible pour
        l'Editeur (44 px) et Pyxel Studio (29 px), masquee pour Pyxel.

        ⚠ NE PAS Y AJOUTER DE DRAPEAU « ce panneau refuse cet emplacement ». Essaye le 27/07/2026
        pour le panneau Claude, dont la barre d'onglets disparait en mode mosaique : le drapeau
        FONCTIONNAIT (cette methode rendait bien None) et ne changeait RIEN au resultat — le repli
        `add_corner_widget` mene au MEME MainCornerWidget, celui que Spyder place lui-meme dans la
        barre d'onglets des qu'un panneau contient un `Tabs`. Il n'y en a qu'un. Mesure : bouton a
        44x43 dans MainCornerWidget, avec le drapeau comme sans lui. C'etait donc du code mort,
        retire a la passe de simplification. Un panneau dont la barre d'onglets disparait doit
        porter son propre bouton la ou il reste visible (cf. spyder_claude/mosaique.py), pas
        esperer un autre emplacement de celui-ci.
        """
        from qtpy.QtCore import Qt as _SmartosQt
        from qtpy.QtWidgets import QHBoxLayout as _SmartosQHBoxLayout
        from qtpy.QtWidgets import QTabWidget as _SmartosQTabWidget
        from qtpy.QtWidgets import QWidget as _SmartosQWidget

        for _tw in widget.findChildren(_SmartosQTabWidget):
            if not _tw.isVisible() or not _tw.tabBar().isVisible():
                continue
            _coin = _tw.cornerWidget(_SmartosQt.TopRightCorner)
            if _coin is not None and _coin.layout() is not None:
                return _coin
            # Pas de coin exploitable : en poser un. C'est le cas de Pyxel Studio, dont le coin
            # existe mais mesure 0x0 et n'accueille rien.
            _neuf = _SmartosQWidget(_tw)
            _boite = _SmartosQHBoxLayout(_neuf)
            _boite.setContentsMargins(0, 0, 0, 0)
            _boite.setSpacing(0)
            _tw.setCornerWidget(_neuf, _SmartosQt.TopRightCorner)
            return _neuf
        return None

    def _smartos_poser_bouton_agrandir(self, widget, action):
        """
        Poser le bouton et renvoyer le QToolButton cree, ou None.

        Deux emplacements, cf. l'en-tete du patch : sur la ligne des titres d'onglets quand le
        panneau en a une, dans la barre de coin du panneau sinon. Dans les deux cas, a DROITE.
        """
        from qtpy.QtWidgets import QToolButton as _SmartosQToolButton

        _coin = self._smartos_ligne_des_onglets(widget)
        if _coin is not None:
            # ⚠ UN COIN PEUT ETRE UNE BARRE D'OUTILS, ET ON N'EMPILE PAS UN WIDGET DANS LE LAYOUT
            # D'UNE BARRE D'OUTILS. `MainCornerWidget`, le coin que Spyder pose sur la barre
            # d'onglets de certains panneaux, HERITE DE QToolBar : son layout est un QToolBarLayout,
            # qui n'a ni insertWidget ni un addWidget qui range quoi que ce soit. On lui donne donc
            # l'ACTION, et Qt fabrique le bouton, le place et le dimensionne.
            #
            # Mesure du 27/07/2026, panneau Claude, en deux temps — les deux tentatives ratees sont
            # gardees parce qu'elles ont chacune une signature reconnaissable :
            #   1. `layout().insertWidget(0, b)` -> AttributeError sur un QLayout generique,
            #      exception avalee par l'appelant, AUCUN bouton et aucun message ;
            #   2. `layout().addWidget(b)` -> le bouton existe, mais HORS LAYOUT : dessine en
            #      100x30 (taille par defaut d'un widget jamais mis en page) au milieu de voisins
            #      en 44x44. C'est exactement le piege des boutons orphelins deja paye trois fois
            #      sur ce depot.
            # Et une ACTION, contrairement a un bouton, ne laisse rien derriere elle si on la
            # deplace un jour.
            from qtpy.QtWidgets import QToolBar as _SmartosQToolBar
            if isinstance(_coin, _SmartosQToolBar):
                _coin.addAction(action)
                return _coin.widgetForAction(action)

            bouton = _SmartosQToolButton(_coin)
            bouton.setDefaultAction(action)
            bouton.setAutoRaise(True)
            # ⚠ NE PAS FIGER LA TAILLE SUR CELLE D'UN VOISIN : mesure trop tot, elle valait 44x0 et
            # le bouton etait invisible tout en etant "present" (releve du 26/07/2026). On ne fixe
            # que la taille d'icone et on laisse le layout dimensionner le reste.
            _voisins = [_b for _b in _coin.findChildren(_SmartosQToolButton) if _b is not bouton]
            if _voisins:
                bouton.setIconSize(_voisins[0].iconSize())
            # En TETE du layout : le burger preexistant reste le dernier element, donc le plus a
            # droite, et notre bouton se place juste a sa gauche - "a cote du burger", comme demande.
            #
            # ⚠ insertWidget N'EXISTE QUE SUR UN QBoxLayout, et le coin d'un panneau n'en a pas
            # forcement un. Mesure du 27/07/2026 sur le panneau Claude : son coin est le
            # MainCornerWidget de Spyder, dont layout() rend un QLayout generique — l'appel levait
            # « 'PySide6.QtWidgets.QLayout' object has no attribute 'insertWidget' », l'exception
            # etait avalee par le try de l'appelant et journalisee en debug, donc AUCUN bouton et
            # AUCUN message. Le cas ne s'etait jamais presente parce que cette branche n'avait plus
            # qu'un seul utilisateur, l'Editeur, sorti de la liste le matin meme : du code sans
            # appelant, qui se degrade en silence. Les autres panneaux passent par le `_neuf` de
            # _smartos_ligne_des_onglets, un QHBoxLayout que nous construisons — d'ou insertWidget
            # disponible chez eux et pas ici.
            _layout = _coin.layout()
            if hasattr(_layout, "insertWidget"):
                _layout.insertWidget(0, bouton)
            else:
                # Pas de QBoxLayout : on ajoute en fin, ce qui met le bouton le plus a DROITE.
                # C'est l'emplacement demande par l'utilisateur, et le burger de ces panneaux-la
                # est de toute facon masque quand son menu est vide.
                _layout.addWidget(bouton)
            return bouton

        # Cas general : la barre de coin du panneau.
        widget.add_corner_widget(action)
        bouton = widget.get_corner_widget(action.name)
        if bouton is not None:
            self._smartos_caler_le_coin_a_droite(widget, bouton)
        return bouton

    @staticmethod
    def _smartos_caler_le_coin_a_droite(widget, bouton):
        """
        Rendre le coin visible et y caler le contenu A DROITE.

        Deux defauts constates, chacun mesure :
          - le coin peut etre invisible : patch_spyder_dock_actions.py masque le burger des panneaux
            au menu vide, et un coin sans rien a montrer n'est pas affiche par Qt. Le bouton etait
            alors pose sans etre visible, et rien ne le signalait ;
          - le contenu se cale a GAUCHE dans les panneaux SANS barre d'outils principale (Pyxel,
            Python Tutor) : rien ne pousse le coin, qui prend toute la largeur. L'espaceur va DANS
            le widget de coin (MainCornerWidget), et non dans la barre de coin qui le contient :
            mesure du 26/07/2026, un espaceur place dans la barre laissait encore les boutons a
            gauche d'un widget de coin large de 440 px.
        """
        from qtpy.QtWidgets import QSizePolicy as _SmartosQSizePolicy
        from qtpy.QtWidgets import QWidget as _SmartosQWidget

        bouton.setVisible(True)
        for _attr in ("_corner_widget", "_corner_toolbar"):
            _zone = getattr(widget, _attr, None)
            if _zone is not None:
                _zone.setVisible(True)

        _coin = getattr(widget, "_corner_widget", None)
        if _coin is not None and not getattr(_coin, "_smartos_cale_a_droite", False):
            _coin._smartos_cale_a_droite = True
            _espaceur = _SmartosQWidget(_coin)
            _espaceur.setSizePolicy(
                _SmartosQSizePolicy.Expanding, _SmartosQSizePolicy.Preferred)
            _actions = _coin.actions()
            if _actions:
                _coin.insertWidget(_actions[0], _espaceur)
            else:
                _coin.addWidget(_espaceur)

    @staticmethod
    def _smartos_masquer_burger_vide(widget):
        """
        Masquer le bouton burger d'un panneau dont le menu d'options n'a aucune entree.

        ⚠ IL FAUT MASQUER L'ACTION, PAS LE WIDGET. Le burger est tenu par une barre d'outils Qt (le
        MainCornerWidget en est une), via une QWidgetAction. Or QToolBarLayout REAFFICHE le widget de
        chaque action dont l'ACTION est visible des que la barre redevient visible - ce que fait
        justement _smartos_caler_le_coin_a_droite juste avant. Un setVisible(False) pose sur le seul
        bouton etait donc annule aussitot : releve du 26/07/2026, trois panneaux a zero entree de menu
        et burger pourtant affiche, alors que les deux panneaux passes par la ligne d'onglets - ou la
        barre n'est pas re-affichee - respectaient la consigne. C'est ce contraste qui a mis le doigt
        dessus.
        Meme cause pour patch_spyder_dock_actions.py, qui porte la meme regle et paraissait inoperant :
        il masque bien le bouton au demarrage, mais nous ressuscitions le burger en rendant le coin
        visible. La regle est donc rejouee ici, sur l'action.
        """
        _burger = getattr(widget, "_options_button", None)
        _menu = getattr(widget, "_options_menu", None)
        if _burger is None or _menu is None:
            return
        _menu._dirty = True
        _menu.render()
        _garder = any(not _a.isSeparator() for _a in _menu.actions())

        _burger.setVisible(_garder)
        _coin = getattr(widget, "_corner_widget", None)
        if _coin is not None:
            for _act in _coin.actions():
                try:
                    if _coin.widgetForAction(_act) is _burger:
                        _act.setVisible(_garder)
                except Exception:
                    continue

        # SmartOS (3e passe, 08/08/2026) : Analyse de code (pylint). La disposition par
        # defaut de Spyder l'affiche ; ici le bouton Docteur et la marge (greffon
        # spyder_code_analysis) couvrent l'usage courant, le panneau se rouvre de
        # lui-meme au premier clic sur Docteur. Drapeau DISTINCT, comme pour la v2 :
        # les configurations dont v1/v2 sont deja consommes doivent quand meme le
        # masquer une fois.
        if not self.get_conf("smartos_docks_hidden_v3", False):
            _smartos_plugin = self.get_plugin("pylint", error=False)
            if _smartos_plugin is not None:
                _smartos_plugin.get_widget().toggle_view(False)
            self.set_conf("smartos_docks_hidden_v3", True)

    # ---- Private API
    # -------------------------------------------------------------------------
    @property
    @lru_cache
    def _first_spyder_run(self):
        """
        Check if Spyder is run for the first time.

        Notes
        -----
        * We declare this as a property to prevent reassignments in other
          places of this class.
        * It only needs to be computed once at startup (i.e. it needs to be
          accessed in on_initialize).
        """
        # We need to do this double check because we were not using the
        # "first_time" option in 6.0 and older versions.
        return (
            self.get_conf("first_time", True) and self.get_conf("names") == []
        )

    def _get_internal_dockable_plugins(self):
        """Get the list of internal dockable plugins"""
        return get_class_values(DockablePlugins)

    def _update_fullscreen_action(self):
        if self._fullscreen_flag:
            icon = self.create_icon('window_nofullscreen')
        else:
            icon = self.create_icon('window_fullscreen')
        self.get_container()._fullscreen_action.setIcon(icon)

    def _update_lock_interface_action(self):
        """
        Helper method to update the locking of panes/dockwidgets and toolbars.

        Returns
        -------
        None.
        """
        if self._interface_locked:
            icon = self.create_icon('drag_dock_widget')
            text = _('Unlock panes and toolbars')
        else:
            icon = self.create_icon('lock')
            text = _('Lock panes and toolbars')
        self.lock_interface_action.setIcon(icon)
        self.lock_interface_action.setText(text)

    def _apply_docktabbar_style(self):
        """Apply dock tabbar style."""
        # Apply style by installing the dockwidget tab event filter.
        plugins = self.get_dockable_plugins()
        for plugin in plugins:
            plugin.dockwidget.is_shown = True
            plugin.dockwidget.install_tab_event_filter()

    def _update_shortcuts_in_plugins_menu(self, show=True):
        """
        Show/hide shortcuts for actions in the plugins menu.

        Notes
        -----
        Shortcuts in that menu need to disabled when not visible to prevent
        plugins to be hidden with them.
        """
        for plugin in self.get_dockable_plugins():
            action = plugin.toggle_view_action

            if show:
                section = plugin.CONF_SECTION
                try:
                    context = '_'
                    name = 'switch to {}'.format(section)
                    shortcut = self.get_shortcut(
                        name, context, plugin_name=section
                    )
                except (cp.NoSectionError, cp.NoOptionError):
                    shortcut = QKeySequence()
            else:
                shortcut = QKeySequence()

            action.setShortcut(shortcut)

    # ---- Helper methods
    # -------------------------------------------------------------------------
    def get_last_plugin(self):
        """
        Return the last focused dockable plugin.

        Returns
        -------
        SpyderDockablePlugin
            The last focused dockable plugin.
        """
        return self._last_plugin

    def get_fullscreen_flag(self):
        """
        Give access to the fullscreen flag.

        The flag shows if the mainwindow is in fullscreen mode or not.

        Returns
        -------
        bool
            True is the mainwindow is in fullscreen. False otherwise.
        """
        return self._fullscreen_flag

    # ---- Layout handling
    # -------------------------------------------------------------------------
    def register_layout(self, parent_plugin, layout_type):
        """
        Register a new layout type.

        Parameters
        ----------
        parent_plugin: spyder.api.plugins.SpyderPluginV2
            Plugin registering the layout type.
        layout_type: spyder.plugins.layout.api.BaseGridLayoutType
            Layout to register.
        """
        self.get_container().register_layout(parent_plugin, layout_type)

    def register_custom_layouts(self):
        """Register custom layouts provided by external plugins."""
        for plugin_name in PLUGIN_REGISTRY.external_plugins:
            plugin_instance = self.get_plugin(plugin_name)
            if hasattr(plugin_instance, 'CUSTOM_LAYOUTS'):
                if isinstance(plugin_instance.CUSTOM_LAYOUTS, list):
                    for custom_layout in plugin_instance.CUSTOM_LAYOUTS:
                        self.register_layout(self, custom_layout)
                else:
                    logger.info(
                        f'Unable to load custom layouts for plugin '
                        f'{plugin_name}. Expecting a list of layout classes '
                        f'but got {plugin_instance.CUSTOM_LAYOUTS}.'
                    )

    def get_layout(self, layout_id):
        """
        Get a registered layout by his ID.

        Parameters
        ----------
        layout_id : string
            The ID of the layout.

        Returns
        -------
        Instance of a spyder.plugins.layout.api.BaseGridLayoutType subclass
            Layout.
        """
        return self.get_container().get_layout(layout_id)

    def update_layout_menu_actions(self):
        self.get_container().update_layout_menu_actions()

    def setup_layout(self, default=False):
        """Initialize mainwindow layout."""
        prefix = 'window' + '/'
        settings = self.load_window_settings(prefix, default)
        hexstate = settings[0]

        if hexstate is None:
            # First Spyder execution:
            self.main.setWindowState(Qt.WindowMaximized)
            self.setup_default_layouts(DefaultLayouts.SpyderLayout, settings)

            # Restore the original defaults. This is necessary, for instance,
            # when bumping WINDOW_STATE_VERSION because layouts saved with a
            # previous version can't be applied with the new one
            if default:
                order = list(self.get_container().spyder_layouts.keys())
                self.set_conf('order', order)
                self.set_conf('active', order)
                self.set_conf('names', order)

                ui_names = [
                    l.get_name()
                    for l in self.get_container().spyder_layouts.values()
                ]
                self.set_conf('ui_names', ui_names)

                # This will remove custom layouts from the UI
                self.get_container().update_layout_menu_actions()

            section = 'quick_layouts'
            order = self.get_conf('order')
            for index, _name, in enumerate(order):
                prefix = 'layout_{0}/'.format(index)
                self.save_current_window_settings(
                    prefix, section, none_state=True
                )

            # Store the initial layout as the default in spyder
            prefix = 'layout_default/'
            self.save_current_window_settings(prefix, section, none_state=True)
            self._current_quick_layout = DefaultLayouts.SpyderLayout

        self.set_window_settings(*settings)

    def setup_default_layouts(self, layout_id, settings):
        """Setup default layouts when run for the first time."""
        main = self.main
        main.setUpdatesEnabled(False)

        if self._first_spyder_run:
            self.set_window_settings(*settings)
        else:
            if self._last_plugin:
                if self._last_plugin._ismaximized:
                    self.maximize_dockwidget(restore=True)

            if not (main.isMaximized() or self._maximized_flag):
                main.showMaximized()

            min_width = main.minimumWidth()
            max_width = main.maximumWidth()
            base_width = main.width()
            main.setFixedWidth(base_width)

        # Layout selection
        layout = self.get_layout(layout_id)

        # Apply selected layout
        layout.set_main_window_layout(self.main, self.get_dockable_plugins())

        if self._first_spyder_run:
            self.set_conf("first_time", False)
        else:
            self.main.setMinimumWidth(min_width)
            self.main.setMaximumWidth(max_width)

            if not (self.main.isMaximized() or self._maximized_flag):
                self.main.showMaximized()

        self.main.setUpdatesEnabled(True)
        self.main.sig_layout_setup_ready.emit(layout)

        return layout

    def quick_layout_switch(self, index_or_layout_id):
        """
        Switch to quick layout.

        Using a number *index* or a registered layout id *layout_id*.

        Parameters
        ----------
        index_or_layout_id: int or str
        """
        # We need to do this first so the new layout is applied as expected.
        self.unmaximize_dockwidget()

        section = 'quick_layouts'
        container = self.get_container()
        try:
            settings = self.load_window_settings(
                'layout_{}/'.format(index_or_layout_id), section=section
            )
            hexstate, window_size, pos, is_maximized, is_fullscreen = settings

            # The defaults layouts will always be regenerated unless there was
            # an overwrite, either by rewriting with same name, or by deleting
            # and then creating a new one
            if hexstate is None:
                # The value for hexstate shouldn't be None for a custom saved
                # layout (ie, where the index is greater than the number of
                # defaults).  See spyder-ide/spyder#6202.
                if index_or_layout_id not in DEFAULT_LAYOUTS:
                    container.critical_message(
                        _("Warning"),
                        _("Error opening the custom layout.  Please close"
                          " Spyder and try again.  If the issue persists,"
                          " then you must use 'Reset to Spyder default' "
                          "from the layout menu."))
                    return
                self.setup_default_layouts(index_or_layout_id, settings)
            else:
                self.set_window_settings(*settings)
        except cp.NoOptionError:
            try:
                layout = self.get_layout(index_or_layout_id)
                layout.set_main_window_layout(
                    self.main, self.get_dockable_plugins())
                self.main.sig_layout_setup_ready.emit(layout)
            except SpyderAPIError:
                container.critical_message(
                    _("Warning"),
                    _("Quick switch layout #%s has not yet "
                      "been defined.") % str(index_or_layout_id))

        # Make sure the flags are correctly set for visible panes
        for plugin in self.get_dockable_plugins():
            action = plugin.toggle_view_action
            action.setChecked(plugin.dockwidget.isVisible())

        # This is necessary to restore the style for dock tabbars after the
        # switch
        self._apply_docktabbar_style()

        return index_or_layout_id

    def load_window_settings(self, prefix, default=False, section='main'):
        """
        Load window layout settings from userconfig-based configuration with
        *prefix*, under *section*.

        Parameters
        ----------
        default: bool
            if True, do not restore inner layout.
        """
        get_func = self.get_conf_default if default else self.get_conf
        window_size = get_func(prefix + 'size', section=section)

        if default:
            hexstate = None
        else:
            try:
                hexstate = get_func(prefix + 'state', section=section)
            except Exception:
                hexstate = None

        pos = get_func(prefix + 'position', section=section)

        # We use `virtualGeometry` instead of `geometry` below because it gives
        # the shape of all connected screens, which is what we need here (
        # `geometry` only works for the current one).
        screen_shape = self.main.screen().virtualGeometry()
        current_width = screen_shape.width()
        current_height = screen_shape.height()

        # It's necessary to verify if the window/position value is valid
        # with the current screen. See spyder-ide/spyder#3748.
        width = pos[0]
        height = pos[1]
        if current_width < width or current_height < height:
            pos = self.get_conf_default(prefix + 'position', section)

        is_maximized = get_func(prefix + 'is_maximized', section=section)
        is_fullscreen = get_func(prefix + 'is_fullscreen', section=section)
        return (hexstate, window_size, pos, is_maximized, is_fullscreen)

    def get_window_settings(self):
        """
        Return current window settings.

        Symetric to the 'set_window_settings' setter.
        """
        # FIXME: Window size in main window is update on resize
        window_size = (self.window_size.width(), self.window_size.height())

        is_fullscreen = self.main.isFullScreen()
        if is_fullscreen:
            is_maximized = self._maximized_flag
        else:
            is_maximized = self.main.isMaximized()

        pos = (self.window_position.x(), self.window_position.y())

        hexstate = qbytearray_to_str(
            self.main.saveState(version=WINDOW_STATE_VERSION)
        )
        return (hexstate, window_size, pos, is_maximized, is_fullscreen)

    def set_window_settings(self, hexstate, window_size, pos, is_maximized,
                            is_fullscreen):
        """
        Set window settings.

        Symetric to the 'get_window_settings' accessor.
        """
        # Prevent calling this method multiple times on first run because it
        # causes main window flickering on Windows and Mac.
        # Fixes spyder-ide/spyder#15074
        if (
            self._window_settings_applied_on_first_run
            and self._first_spyder_run
        ):
            return

        self.main.setUpdatesEnabled(False)

        # Restore window properties
        self.window_size = QSize(
            window_size[0], window_size[1] # width, height
        )
        self.window_position = QPoint(pos[0], pos[1]) # x, y
        self.main.resize(self.window_size)
        self.main.move(self.window_position)

        # Window layout
        if hexstate:
            hexstate_valid = self.main.restoreState(
                QByteArray().fromHex(str(hexstate).encode('utf-8')),
                version=WINDOW_STATE_VERSION
            )

            # Check layout validity. Spyder 4 and below use the version 0
            # state (default), whereas Spyder 5 will use version 1 state.
            # For more info see the version argument for
            # QMainWindow.restoreState:
            # https://doc.qt.io/qt-5/qmainwindow.html#restoreState
            if not hexstate_valid:
                self.main.setUpdatesEnabled(True)
                self.setup_layout(default=True)
                return

        # Is fullscreen?
        if is_fullscreen:
            self.main.setWindowState(Qt.WindowFullScreen)

        # Is maximized?
        if is_fullscreen:
            self._maximized_flag = is_maximized
        elif is_maximized:
            self.main.setWindowState(Qt.WindowMaximized)

        # Settings applied at startup
        self._window_settings_applied_on_first_run = True

        self.main.setUpdatesEnabled(True)

    def save_current_window_settings(self, prefix, section='main',
                                     none_state=False):
        """
        Save current window settings.

        It saves config with *prefix* in the userconfig-based,
        configuration under *section*.
        """
        # Use current size and position when saving window settings.
        # Fixes spyder-ide/spyder#13882
        win_size = self.main.size()
        pos = self.main.pos()

        self.set_conf(
            prefix + 'size',
            (win_size.width(), win_size.height()),
            section=section,
        )
        self.set_conf(
            prefix + 'is_maximized',
            self.main.isMaximized(),
            section=section,
        )
        self.set_conf(
            prefix + 'is_fullscreen',
            self.main.isFullScreen(),
            section=section,
        )
        self.set_conf(
            prefix + 'position',
            # We need to do these validations to avoid an error that breaks
            # doing mouse clicks in WSL.
            # Fixes spyder-ide/spyder#20851
            (pos.x() if pos.x() > 0 else 0, pos.y() if pos.y() > 0 else 0),
            section=section,
        )

        self.maximize_dockwidget(restore=True)  # Restore non-maximized layout

        if none_state:
            self.set_conf(
                prefix + 'state',
                None,
                section=section,
            )
        else:
            qba = self.main.saveState(version=WINDOW_STATE_VERSION)
            self.set_conf(
                prefix + 'state',
                qbytearray_to_str(qba),
                section=section,
            )

        self.set_conf(
            prefix + 'statusbar',
            not self.main.statusBar().isHidden(),
            section=section,
        )

    # ---- Maximize, close, switch to dockwidgets/plugins
    # -------------------------------------------------------------------------
    @Slot()
    def close_current_dockwidget(self):
        """Search for the currently focused plugin and close it."""
        widget = QApplication.focusWidget()
        for plugin in self.get_dockable_plugins():
            if plugin.get_widget().isAncestorOf(widget):
                plugin.toggle_view_action.setChecked(False)
                break

    @property
    def maximize_action(self):
        """Expose maximize current dockwidget action."""
        return self.get_container()._maximize_dockwidget_action

    # SmartOS (patch_spyder_deux_ecrans.py) : mode DEUX ECRANS. Cf. l'en-tete du patch pour
    # le releve des panneaux et ce qui n'est pas faisable sous Wayland.

    #: Panneaux qui partent sur le second ecran : ceux qui sont A DROITE de l'editeur, calcule EN
    #: DIRECT a chaque entree dans le mode (plus une liste figee dans le code, cf. plus bas) -
    #: demande de l'utilisateur du 01/08/2026, apres avoir du faire retirer "find_in_files" a la
    #: main quand il l'a deplace a gauche dans SA disposition : suivre sa disposition reelle evite
    #: cette friction pour tout futur reamenagement.
    #:
    #: Identifiant de la barre d'application qui porte le bouton de bascule.
    SMARTOS_BARRE_ECRANS = "smartos_toolbar_ecrans"

    #: Options de configuration ou sont memorisees les deux dispositions.
    #:
    #: ⚠ POURQUOI LA CLE EST VERSIONNEE, ET CE QUE CELA NE DIT PAS. `saveState()` est MONOLITHIQUE :
    #: il enregistre les panneaux ET les barres d'outils. Une disposition enregistree pendant que la
    #: rangee etait cassee la restitue donc fidelement a chaque bascule - et la sortie du mode la
    #: reenregistre telle quelle. Cela ressemble a une boucle qui s'aggrave ; ce n'en est pas une.
    #: MESURE DU 31/07/2026, cinq bascules d'affilee en partant d'une cle vierge : ecart du groupe au
    #: centre de 0 pixel, dix relevés. Aucune derive. Un etat SAIN se reenregistre sain ; c'est un
    #: etat HERITE qui se perpetue.
    #: (Un residu de -8 px subsistait avant que la barre « Écrans » ne recopie l'etat verrouille des
    #: autres : c'etait la largeur de sa poignee de deplacement, que j'avais mise sur le compte d'un
    #: arrondi. Une explication commode vaut moins qu'une mesure de plus.)
    #: Il n'y avait donc rien a corriger dans le mecanisme, et surtout rien a lui retirer - deux
    #: correctifs ont ete ecrits puis jetes avant de le comprendre (forcer la barre de centrage a se
    #: reajuster, rejouer le calcul apres restauration : mesures sans effet, l'espace libere etant
    #: aussitot repris par la zone de gauche). Il fallait REPARTIR PROPRE, et rien d'autre.
    #: v2 avait ete polluee de la meme facon quelques heures apres sa creation, par une version
    #: encore cassee essayee entre-temps : changer de nom ne protege que si l'on change de nom APRES
    #: que le defaut est corrige.
    SMARTOS_CONF_MODE = "smartos_mode_deux_ecrans"
    #: ⚠ CETTE CLE A ETE VERSIONNEE (v1 a v4) PENDANT LE DEVELOPPEMENT DE CE PATCH - PLUS
    #: MAINTENANT. Tant que la rangee de barres changeait, la cle changeait avec elle : l'etat
    #: memorise fige la POSITION des barres autant que celle des panneaux, et deplacer la barre
    #: « Écrans » sans renommer la cle la faisait revenir a son ancienne place a la premiere
    #: bascule (constate deux fois le 31/07/2026, v2 puis v3 polluees dans l'heure par une session
    #: lancee entre la creation de la cle et la retouche suivante). Renommee en anglais et
    #: DEVERSIONNEE le 01/08/2026 (demande explicite de l'utilisateur : les numeros de version
    #: etaient utiles pendant le developpement, plus dans la solution finale). Consequence a
    #: assumer pour toute retouche future de la rangee de barres : reinitialiser la cle a la main
    #: (`set_conf(SMARTOS_CONF_ETATS, {})`) plutot que de lui donner un nouveau nom.
    SMARTOS_CONF_ETATS = "window_state_screens"

    def _smartos_creer_fenetre_ecran2(self):
        """La seconde fenetre, prete a recevoir des panneaux."""
        from qtpy.QtWidgets import QMainWindow as _SmartosQMainWindow

        fenetre = _SmartosQMainWindow()
        fenetre.setObjectName("smartos_fenetre_ecran2")
        fenetre.setWindowTitle(_("Spyder — second écran"))

        # ⚠ LA TAILLE SE MEMORISE, LA POSITION NON, et il ne faut pas laisser croire le contraire.
        # `restoreGeometry` rend la taille ET l'etat maximise/plein ecran, ce qui est l'essentiel de
        # ce que l'utilisateur regle a la main - releve du 31/07/2026, « la deuxieme fenetre
        # n'enregistre pas son emplacement ». Sa POSITION, elle, reste decidee par le compositeur :
        # sous Wayland un client ne place pas sa propre fenetre (mesure du 27/07/2026 rappelee en
        # tete de ce fichier), et aucune option de Qt n'y changera rien. 1200x900 n'est que le repli
        # de la toute premiere ouverture.
        _geometrie = self.get_conf(self.SMARTOS_CONF_ETATS, default={}).get("double_geometry")
        if not (_geometrie and fenetre.restoreGeometry(
                QByteArray().fromHex(str(_geometrie).encode("utf-8")))):
            fenetre.resize(1200, 900)

        # ⚠ Une QMainWindow neuve n'herite PAS de la feuille de style de l'application : sans ceci
        # la fenetre s'affiche en theme clair au milieu d'un Spyder sombre (mesure du 27/07/2026).
        fenetre.setStyleSheet(self.main.styleSheet())

        # ⚠ NI DE SES OPTIONS DE DOCK, et c'est ce qui interdisait la MOSAIQUE. Mesure du
        # 31/07/2026 : la fenetre principale porte AnimatedDocks|AllowNestedDocks|AllowTabbedDocks,
        # une fenetre neuve seulement AnimatedDocks|AllowTabbedDocks. Sans AllowNestedDocks, deux
        # panneaux ne peuvent que s'empiler ou se mettre en onglets, jamais se poser cote a cote -
        # releve de l'utilisateur, « je voudrais pouvoir reorganiser les dock dans la 2nd fenetre,
        # avec des dock lateraux, pour pouvoir faire une mosaique ». On RECOPIE celles de la fenetre
        # principale plutot que d'en ecrire une liste : si Spyder en change un jour, la seconde
        # fenetre suit sans qu'on ait a le savoir.
        fenetre.setDockOptions(self.main.dockOptions())

        # ⚠ SANS CECI LA FENETRE N'EST NI DEPLACABLE, NI REDIMENSIONNABLE, NI FERMABLE : la regle
        # KWin qui supprime la barre de titre vise le TITRE ("^Spyder [-—] .*", cf. TODO cosmetique,
        # "Fenetres pop-up SANS CADRE") - cette fenetre s'appelle "Spyder — second ecran", donc
        # matche aussi et perd son cadre natif. C'est le greffon qui equipe - ses boutons, ses
        # glyphes, son calage - et non nous. Garde les boutons SmartOS plutot que de compter sur le
        # cadre KWin : la demande utilisateur du 01/08/2026 est de garder la possibilite d'ajouter
        # d'autres boutons a cette barre plus tard, ce qu'un cadre natif n'offre pas.
        _controles = self.get_plugin("window_controls", error=False)
        if _controles is not None:
            _controles.equip_window(fenetre)
        else:
            logger.debug("Greffon window_controls absent : seconde fenetre sans ses controles")

        # Fermer la fenetre revient a quitter le mode : sinon les panneaux resteraient dans une
        # fenetre detruite, donc invisibles et injoignables.
        # ⚠ PASSER PAR L'ACTION (`setChecked`), PAS PAR UN APPEL DIRECT A
        # `_smartos_quitter_deux_ecrans()` (fait dans une version precedente, demande de
        # l'utilisateur du 01/08/2026) : un appel direct quitte bien le mode mais laisse le bouton
        # de la barre « Écrans » coche comme si le mode etait toujours actif - `_smartos_basculer_
        # deux_ecrans()`, qui recale aussi l'icone et le texte du bouton
        # (`_smartos_rendre_le_bouton`), n'est alors jamais appele. `setChecked(False)` emet
        # `toggled`, deja connecte a `_smartos_basculer_deux_ecrans` : une seule source de verite,
        # comme le reste de ce mode. Sans effet de bord si la fenetre est fermee PAR ce meme
        # bouton (l'action est deja decochee a ce moment-la, `setChecked` ne re-emet rien).
        fenetre.closeEvent = lambda evenement: (
            self._smartos_action_ecrans.setChecked(False), evenement.accept()
        )

        self._smartos_fen2 = fenetre
        return fenetre

    def _smartos_panneaux_a_droite_de_editeur(self):
        """
        Quels panneaux ancrables sont ACTUELLEMENT a droite de l'editeur dans la fenetre
        principale, releve PAR LA GEOMETRIE REELLE plutot que par une liste figee dans le code -
        remplace SMARTOS_GROUPES_ECRAN2 (retiree le 01/08/2026, demande de l'utilisateur : il avait
        du me faire retirer "find_in_files" a la main apres l'avoir deplace a gauche, ce calcul
        suit desormais sa disposition sans intervention).

        ⚠ DEUX CRITERES PLUS SIMPLES SE SONT REVELES FAUX (releve du 27/07/2026, cf. l'en-tete du
        patch) : `dockWidgetArea()` repond "gauche" pour TOUS les panneaux, meme ceux a droite ; et
        la position d'un panneau en onglet NON actif est mappee hors ecran (abscisse negative)
        alors qu'il se dit visible. La bonne methode, deja eprouvee a l'epoque : regrouper par
        `tabifiedDockWidgets()` (des onglets d'un meme groupe partagent une position), puis juger
        le groupe sur la geometrie de son onglet ACTIF (celui dont `visibleRegion()` n'est pas
        vide).
        """
        editeur = self.get_plugin(Plugins.Editor, error=False)
        if editeur is None or getattr(editeur, "dockwidget", None) is None:
            return ()
        _editeur_x = editeur.dockwidget.mapToGlobal(editeur.dockwidget.rect().topLeft()).x()

        _plugin_par_dock = {}
        for _p in self.get_dockable_plugins():
            _d = getattr(_p, "dockwidget", None)
            if _d is not None and _p is not editeur:
                _plugin_par_dock[_d] = _p

        _deja_vus = set()
        _groupes = []
        for _dock, _plugin in _plugin_par_dock.items():
            if _dock in _deja_vus:
                continue
            _compagnons = [_dock] + [
                _d for _d in self.main.tabifiedDockWidgets(_dock) if _d in _plugin_par_dock
            ]
            _deja_vus.update(_compagnons)
            _actif = next(
                (_d for _d in _compagnons if not _d.visibleRegion().isEmpty()), _compagnons[0]
            )
            _actif_x = _actif.mapToGlobal(_actif.rect().topLeft()).x()
            if _actif_x > _editeur_x:
                _groupes.append(tuple(_plugin_par_dock[_d].NAME for _d in _compagnons))
        return tuple(_groupes)

    def _smartos_groupes_dans_fenetre(self, fenetre):
        """
        Les panneaux ACTUELLEMENT dans `fenetre`, regroupes par `tabifiedDockWidgets()` - pour le
        retour du second ecran vers la fenetre principale, ou TOUT ce qui s'y trouve doit revenir
        (pas de tri par geometrie a faire, contrairement a l'aller).
        """
        from qtpy.QtWidgets import QDockWidget as _SmartosQDockWidget

        _plugin_par_dock = {
            _p.dockwidget: _p
            for _p in self.get_dockable_plugins()
            if getattr(_p, "dockwidget", None) is not None
        }
        _docks_ici = [
            _d for _d in fenetre.findChildren(_SmartosQDockWidget) if _d in _plugin_par_dock
        ]

        _deja_vus = set()
        _groupes = []
        for _dock in _docks_ici:
            if _dock in _deja_vus:
                continue
            _compagnons = [_dock] + [
                _d for _d in fenetre.tabifiedDockWidgets(_dock) if _d in _plugin_par_dock
            ]
            _deja_vus.update(_compagnons)
            _groupes.append(tuple(_plugin_par_dock[_d].NAME for _d in _compagnons))
        return tuple(_groupes)

    def _smartos_poser_groupes(self, fenetre, zone, groupes):
        """
        Poser `groupes` (tuple de tuples de noms de panneaux) dans `fenetre`, EN ONGLETS.

        ⚠ LE REGROUPEMENT VAUT DANS LES DEUX SENS, et c'est la meme mesure qui l'impose. Poses un
        par un, dix panneaux s'empilent en colonne : a l'aller ils faisaient une centaine de pixels
        chacun, illisibles (capture HGIGNORED/bascule_ecran2.png) ; au retour, leur hauteur minimale
        cumulee force Qt a AGRANDIR la fenetre principale, et le `restoreState` qui suit ne rend que
        la disposition, jamais la geometrie - d'ou la fenetre « trop grande verticalement » relevee
        par l'utilisateur le 31/07/2026.

        ⚠ UN PANNEAU DEJA VISIBLE AVANT LA BASCULE PEUT DEVENIR INVISIBLE APRES (releve de
        l'utilisateur le 01/08/2026, PAS l'inverse - une premiere hypothese le disait absent plutot
        qu'invisible, ecartee par sa correction). `addDockWidget()` + `.show()` change l'affichage
        Qt du dock mais PAS le suivi interne de Spyder (celui qui repond a
        Fenetre > Panneaux) - les deux se desynchronisent des que le dock change de fenetre
        parente, et le panneau reste marque "visible" en interne sans l'etre reellement a l'ecran.
        Seul un aller-retour MANUEL par le menu Fenetre le corrigeait, en repassant par le vrai
        chemin de code de Spyder. On rejoue ici ce meme aller-retour programmatiquement
        (`toggle_view`, la methode que ce menu appelle lui-meme) plutot que d'inventer une
        resynchronisation maison.
        """
        for _groupe in groupes:
            _premier = None
            for _nom in _groupe:
                _greffon = self.get_plugin(_nom, error=False)
                _dock = None if _greffon is None else getattr(_greffon, "dockwidget", None)
                if _dock is None:
                    continue
                fenetre.addDockWidget(zone, _dock)
                if _premier is None:
                    _premier = _dock
                else:
                    fenetre.tabifyDockWidget(_premier, _dock)
                # ⚠ addDockWidget NE MONTRE PAS : le dock garde son etat d'affichage anterieur.
                _dock.show()
                # Resynchronise le suivi interne de Spyder (cf. docstring) - meme aller-retour que
                # le menu Fenetre > Panneaux, qui appelle cette meme methode.
                try:
                    _greffon.toggle_view(False)
                    _greffon.toggle_view(True)
                except Exception:
                    logger.debug(
                        "Resynchronisation de visibilite impossible pour %s", _nom, exc_info=True)
            if _premier is not None:
                # Le premier de chaque groupe devient l'onglet actif, comme dans la disposition
                # d'origine.
                _premier.raise_()

    def _smartos_dock_dans_ecran2(self, dock):
        """Vrai si ce dock vit dans la seconde fenetre."""
        fenetre = getattr(self, "_smartos_fen2", None)
        return fenetre is not None and dock is not None and fenetre.isAncestorOf(dock)

    # SmartOS (patch_spyder_deux_ecrans_maximize.py) : quelle fenetre agrandir dans, et le repli
    # du second ecran quand rien n'a le focus dedans. Cf. l'en-tete du patch pour le contexte.

    def _smartos_fenetre_du_focus(self, focus_widget):
        """La fenetre (principale ou second ecran) qui contient le widget focalise."""
        fenetre2 = getattr(self, "_smartos_fen2", None)
        if (fenetre2 is not None and focus_widget is not None
                and fenetre2.isAncestorOf(focus_widget)):
            return fenetre2
        return self.main

    def _smartos_premier_panneau_ecran2(self):
        """Premier panneau encore present dans le second ecran, ou None."""
        _noms = self._smartos_noms_panneaux_ecran2()
        return self.get_plugin(_noms[0], error=False) if _noms else None

    def _smartos_noms_panneaux_ecran2(self):
        """
        Noms des panneaux ACTUELLEMENT dans le second ecran, releves par ancrage plutot que par
        une liste figee - remplace SMARTOS_PANNEAUX_ECRAN2 (retiree le 01/08/2026 avec
        SMARTOS_GROUPES_ECRAN2, meme raison : suivre la disposition reelle plutot qu'une liste a
        maintenir a la main). Vide si le mode n'est pas actif.
        """
        fenetre = getattr(self, "_smartos_fen2", None)
        if fenetre is None:
            return ()
        return tuple(
            _p.NAME for _p in self.get_dockable_plugins()
            if self._smartos_dock_dans_ecran2(getattr(_p, "dockwidget", None))
        )

    def _smartos_basculer_deux_ecrans(self, actif):
        """Entrer dans le mode deux ecrans, ou en sortir."""
        try:
            if actif:
                self._smartos_entrer_deux_ecrans()
            else:
                self._smartos_quitter_deux_ecrans()
            self._smartos_rendre_le_bouton(actif)
        except Exception:
            # Un mode d'affichage ne doit jamais emporter Spyder avec lui.
            logger.exception("Bascule du mode deux ecrans impossible")
        self._smartos_recentrer_rangee()

    def _smartos_recentrer_rangee(self):
        """
        Rejouer le centrage du groupe du milieu (publie sur main par patch_spyder_burger_menu.py).

        La bascule passe par restoreState() SANS redimensionner la fenetre principale (le second
        ecran a sa PROPRE fenetre) : sig_resized ne tire donc pas, et la DragArea gauche garderait
        la largeur figee calculee dans l'autre mode - les boutons du milieu ne revenaient pas au
        milieu en sortant du mode deux ecrans (releve utilisateur du 08/08/2026). Deux passes
        differees, comme au demarrage : les largeurs des barres ne sont definitives qu'une fois la
        rangee reposee par la boucle d'evenements.
        """
        recentrer = getattr(self.main, "_smartos_recentrer_barres", None)
        if recentrer is None:
            return
        from qtpy.QtCore import QTimer as _SmartosQTimerEcrans
        _SmartosQTimerEcrans.singleShot(0, recentrer)
        _SmartosQTimerEcrans.singleShot(400, recentrer)

    def _smartos_entrer_deux_ecrans(self):
        from qtpy.QtCore import Qt as _SmartosQt
        from qtpy.QtGui import QGuiApplication as _SmartosQGuiApplication

        if getattr(self, "_smartos_fen2", None) is not None:
            return

        # Memoriser la disposition mono-ecran AVANT de la defaire : c'est elle qu'on rendra en
        # sortant, et c'est la moitie « mono » des deux configurations demandees.
        etats = dict(self.get_conf(self.SMARTOS_CONF_ETATS, default={}))
        etats["mono"] = qbytearray_to_str(self.main.saveState(version=WINDOW_STATE_VERSION))

        fenetre = self._smartos_creer_fenetre_ecran2()
        self._smartos_poser_groupes(
            fenetre, _SmartosQt.LeftDockWidgetArea, self._smartos_panneaux_a_droite_de_editeur()
        )

        # ⚠ _last_plugin SURVIT d'un agrandissement a l'autre : s'il designe un panneau qui vient de
        # partir, le prochain agrandissement le tirerait dans la fenetre principale.
        if self._last_plugin is not None and self._smartos_dock_dans_ecran2(
                getattr(self._last_plugin, "dockwidget", None)):
            self._last_plugin = None

        # ⚠ DESIGNER L'ECRAN AVANT LE PREMIER AFFICHAGE, ET NON APRES. On ne PLACE pas la fenetre -
        # sous Wayland move() est ignore, c'est mesure - mais on peut dire sur QUEL ECRAN elle doit
        # naitre. Encore faut-il le dire au bon moment : le compositeur en tient compte a la
        # CREATION de la surface, et `setScreen` sur une fenetre deja mappee est au mieux une
        # suggestion qu'il ignore. La premiere version appelait show() puis setScreen() ; l'ordre
        # est desormais inverse, et c'est QWidget.setScreen qui s'en charge - il recree la fenetre
        # si besoin, ce que la poignee brute ne fait pas.
        #
        # ⚠ ET PAS DE showFullScreen. Il etait pose ici « puisqu'on a un ecran dedie » ; il rendrait
        # inutile la taille memorisee ci-dessus, que l'utilisateur a demandee le meme jour. Le
        # plein ecran reste a un clic, et restoreGeometry le memorise comme le reste.
        _ecrans = _SmartosQGuiApplication.screens()
        if len(_ecrans) > 1:
            _autres = [e for e in _ecrans if e is not self.main.screen()]
            if _autres:
                fenetre.setScreen(_autres[0])
        fenetre.show()

        # Restaurer la disposition double-ecran si on en a deja une.
        if etats.get("double_screen_1"):
            self.main.restoreState(
                QByteArray().fromHex(str(etats["double_screen_1"]).encode("utf-8")),
                version=WINDOW_STATE_VERSION,
            )
        if etats.get("double_screen_2"):
            fenetre.restoreState(
                QByteArray().fromHex(str(etats["double_screen_2"]).encode("utf-8"))
            )

        # ⚠ REMONTER LES POIGNEES DE REDIMENSIONNEMENT, ICI, SANS ATTENDRE D'EVENEMENT. Le greffon
        # window_controls les remonte au-dessus du contenu par un filtre d'evenements de la
        # fenetre ; poser dix panneaux d'un coup les recouvre, et le filtre ne rattrape qu'au tour
        # de boucle suivant. Mesure du 31/07/2026, la seule qui tranche : SANS cette relance, six
        # poignees sur huit restent sous les onglets ; avec, huit sur huit, sur trois essais.
        _poignees = getattr(fenetre, "_window_controls_grips", None)
        if _poignees is not None:
            _poignees.reposition()


        self.set_conf(self.SMARTOS_CONF_ETATS, etats)
        self.set_conf(self.SMARTOS_CONF_MODE, True)

    def _smartos_quitter_deux_ecrans(self):
        from qtpy.QtCore import Qt as _SmartosQt

        fenetre = getattr(self, "_smartos_fen2", None)
        if fenetre is None:
            return

        # Memoriser la disposition double-ecran AVANT de la defaire : c'est l'autre moitie.
        etats = dict(self.get_conf(self.SMARTOS_CONF_ETATS, default={}))
        etats["double_screen_1"] = qbytearray_to_str(
            self.main.saveState(version=WINDOW_STATE_VERSION)
        )
        etats["double_screen_2"] = qbytearray_to_str(fenetre.saveState())
        etats["double_geometry"] = qbytearray_to_str(fenetre.saveGeometry())

        # Relever AVANT de couper la reference : _smartos_groupes_dans_fenetre lit `fenetre`
        # directement (variable locale), mais autant le faire pendant que tout est encore en place.
        _groupes = self._smartos_groupes_dans_fenetre(fenetre)

        # closeEvent rappelle cette methode : couper la reference AVANT de reposer les panneaux,
        # sinon _smartos_dock_dans_ecran2 les croirait encore de l'autre cote.
        self._smartos_fen2 = None

        self._smartos_poser_groupes(self.main, _SmartosQt.RightDockWidgetArea, _groupes)

        if etats.get("mono"):
            self.main.restoreState(
                QByteArray().fromHex(str(etats["mono"]).encode("utf-8")),
                version=WINDOW_STATE_VERSION,
            )

        fenetre.closeEvent = lambda evenement: evenement.accept()
        fenetre.close()
        fenetre.deleteLater()

        self.toggle_lock(self._interface_locked)

        self.set_conf(self.SMARTOS_CONF_ETATS, etats)
        self.set_conf(self.SMARTOS_CONF_MODE, False)

    def _smartos_poser_barre_deux_ecrans(self):
        """
        La « nouvelle barre d'outils » demandee, avec son bouton de bascule.

        ⚠ UNE BARRE D'APPLICATION DECLAREE AU GREFFON Toolbar, ET SURTOUT PAS UNE QToolBar NUE.
        Les deux premieres versions en posaient une a la main, et les deux ont casse la rangee du
        haut - c'est le releve de l'utilisateur du 31/07/2026, « les outils se retrouvent a droite
        au lieu d'etre centres et la fenetre est trop grande verticalement ». La raison tient en
        une ligne : le centrage de cette rangee est CALCULE par patch_spyder_burger_menu.py
        (largeur de la DragArea gauche = W/2 - epingle a gauche - groupe/2), et ce calcul ne
        parcourt que `toolbarslist`, la liste des barres DECLAREES. Une barre nue occupe donc de la
        place sans etre comptee, et pousse tout le groupe.
        La parade evidente - addToolBarBreak, une rangee a soi - a ete essayee et MESUREE, elle est
        pire : _place_left reordonne la rangee par insertToolBar, et les controles de fenetre
        descendent avec la nouvelle rangee (releve du 31/07/2026, barre window_controls_toolbar a
        y=50 au lieu de y=0). Se declarer est la seule voie qui ne demande a corriger personne.

        Deux consequences de faire cela TARD (ce travail est differe d'un tour de boucle pour
        passer apres tous les on_mainwindow_visible) :
          - le container a deja rendu les barres : il faut appeler render() nous-memes ;
          - la barre s'ajoute en bout de rangee, donc apres les controles de fenetre : on l'insere
            juste avant eux, pour qu'ils restent l'element le plus a droite.
        """
        if getattr(self, "_smartos_barre_ecrans", None) is not None:
            return

        greffon_barres = self.get_plugin(Plugins.Toolbar, error=False)
        if greffon_barres is None:
            logger.debug("Greffon Toolbar absent : barre « Écrans » non posee")
            return

        action = self.create_action(
            "smartos_toggle_two_screens",
            text=_("Mode deux écrans"),
            tip=_("Déplacer les panneaux de droite dans une fenêtre pour le second écran"),
            toggled=self._smartos_basculer_deux_ecrans,
            register_action=False,
        )

        barre = greffon_barres.create_application_toolbar(
            self.SMARTOS_BARRE_ECRANS, _("Écrans")
        )
        greffon_barres.add_item_to_application_toolbar(
            action, toolbar_id=self.SMARTOS_BARRE_ECRANS, omit_id=True
        )
        barre.render()

        # ⚠ FOND GRISE QUAND LE BOUTON EST COCHE : cf. `_smartos_rendre_transparent` plus bas pour
        # le detail et les deux pieges deja payes. Applique ICI une premiere fois, mais ce n'est
        # PAS SUFFISANT A SOI SEUL (releve de l'utilisateur le 01/08/2026, toujours gris malgre ce
        # premier appel) - `_smartos_rendre_le_bouton`, rejouee a chaque bascule, la reapplique.
        self._smartos_rendre_transparent(barre.widgetForAction(action))

        # ⚠ ET RECOPIER L'ETAT VERROUILLE D'UNE BARRE EXISTANTE. Le verrouillage de l'interface
        # n'est pas une propriete que Qt propage : le greffon Toolbar le REJOUE sur les barres qu'il
        # connait, et il l'a fait avant que la notre existe. Elle nait donc MOBILE et affiche sa
        # poignee de deplacement, seule de la rangee - releve de l'utilisateur le 31/07/2026.
        # C'est le troisieme etat, apres la visibilite et le centrage, qu'une barre declaree tard
        # doit aller chercher elle-meme. patch_spyder_burger_menu.py fait deja exactement cela pour
        # sa propre barre nue.
        for _autre in greffon_barres.toolbarslist:
            if _autre is barre:
                continue
            barre.setMovable(_autre.isMovable())
            break

        # ⚠ A GAUCHE, COLLEE A « Fichiers », et pas seulement deplacee la : demande de l'utilisateur
        # du 31/07/2026. Une barre posee a gauche sans le DIRE serait comptee dans le groupe centre
        # par patch_spyder_burger_menu.py, qui la ferait alors decaler de sa propre largeur. On
        # s'annonce donc comme EPINGLEE A GAUCHE - la liste est ouverte, l'autre patch la relit a
        # chaque calcul - et c'est lui qui place la barre au bon endroit de la rangee.
        _epingles = tuple(getattr(self.main, "_smartos_barres_epinglees_gauche", ()))
        if self.SMARTOS_BARRE_ECRANS not in _epingles:
            self.main._smartos_barres_epinglees_gauche = _epingles + (self.SMARTOS_BARRE_ECRANS,)

        # ⚠ TROISIEME CONSEQUENCE, ET LA PLUS SOURNOISE : la barre nait CACHEE. Mesure du
        # 31/07/2026 - elle existe, elle est rendue, elle est placee, sa geometrie vaut 72x50 en
        # x=876, et `isVisible()` rend False. Un bouton parfaitement construit et invisible, soit
        # exactement ce dont l'utilisateur s'est plaint la veille : « je ne peux rien confirmer, si
        # je n'ai aucun bouton ». C'est load_last_visible_toolbars() qui cache tout ce qui n'est pas
        # dans `last_visible_toolbars`, et elle passe APRES nous : notre travail est differe d'un
        # tour de boucle, mais le demarrage de Spyder appelle processEvents(), si bien que ce tour
        # de boucle tombe AVANT le on_mainwindow_visible du greffon Toolbar.
        #
        # D'ou les deux moities, une par ordre d'execution possible - et il en faut bien deux,
        # aucune ne couvrant les deux cas : la conf si le greffon Toolbar passe apres nous (c'est
        # LUI qui montrera la barre, par son propre mecanisme), le setVisible s'il est deja passe.
        _noms = list(self.get_conf("last_visible_toolbars", default=[], section="toolbar"))
        if self.SMARTOS_BARRE_ECRANS not in _noms:
            _noms.append(self.SMARTOS_BARRE_ECRANS)
            self.set_conf("last_visible_toolbars", _noms, section="toolbar")
        barre.setVisible(True)

        self._smartos_barre_ecrans = barre
        self._smartos_action_ecrans = action
        self._smartos_rendre_le_bouton(False)

    def _smartos_rendre_transparent(self, bouton):
        """
        Forcer un fond transparent, y compris a l'etat coche, sur CE bouton precis.

        ⚠ DEUX PIEGES, chacun paye avant de comprendre. 1) Un selecteur de type nu
        (`QToolButton:checked`) ne suffit pas : Spyder pose sa propre feuille de style au niveau
        de l'application, avec un selecteur plus SPECIFIQUE qui l'emporte quel que soit l'ordre
        d'application - releve de l'utilisateur le 01/08/2026, fond gris persistant malgre ce
        style. Nom d'objet UNIQUE + selecteur d'ID (`#nom:checked`), qui l'emporte en specificite
        CSS. 2) POSER LE STYLE UNE SEULE FOIS, A LA CREATION DU BOUTON, N'A PAS SUFFI NON PLUS -
        toujours gris malgre le selecteur d'ID. Diagnostic en direct : le bouton qu'on style au
        moment de creer la barre (`_smartos_poser_barre_deux_ecrans`) n'a NI objectName NI
        styleSheet quand on l'inspecte ensuite - `render()` est rejoue plus tard par le greffon
        Toolbar lui-meme (deja documente juste au-dessus : « la conf si le greffon Toolbar passe
        apres nous »), qui RECONSTRUIT le QToolButton depuis l'action, effacant tout ce qu'on avait
        pose sur l'ancien. D'ou cette methode SEPAREE, REJOUEE a chaque bascule par
        `_smartos_rendre_le_bouton` (qui tourne de toute facon a chaque clic) plutot qu'une seule
        fois a la creation - le bouton, quel qu'il soit a cet instant, est alors forcement le bon.
        """
        if bouton is None:
            return
        bouton.setObjectName("smartos_bouton_deux_ecrans")
        bouton.setStyleSheet(
            "QToolButton#smartos_bouton_deux_ecrans"
            " { background-color: transparent; border: none; } "
            "QToolButton#smartos_bouton_deux_ecrans:checked"
            " { background-color: transparent; border: none; } "
            "QToolButton#smartos_bouton_deux_ecrans:hover"
            " { background-color: transparent; border: none; } "
            "QToolButton#smartos_bouton_deux_ecrans:pressed"
            " { background-color: transparent; border: none; }"
        )

    def _smartos_rendre_le_bouton(self, actif):
        """
        Donner au bouton l'aspect de CE QU'IL FERA au prochain clic, et non de l'etat courant.

        Demande de l'utilisateur du 31/07/2026 : « je ne trouve pas l'icone pour lancer la 2eme
        fenetre tres adaptee, et il faudrait qu'elle change pour permettre un retour plus explicite
        sur une fenetre ». Deux moniteurs quand on est sur un ecran, un seul moniteur quand on est
        sur deux : c'est la convention du bouton maximiser/restaurer, que le greffon
        window_controls applique deja a ses propres boutons.

        Les icones viennent de qtawesome et non de create_icon() : le jeu interne de Spyder n'a
        rien qui parle d'ECRANS, et « dock » - ce qui etait pose ici - decrit un panneau ancre,
        c'est-a-dire tout autre chose.
        """
        _action = getattr(self, "_smartos_action_ecrans", None)
        if _action is None:
            return
        import qtawesome as _qta
        from spyder.utils.icon_manager import ima as _ima
        _action.setIcon(_qta.icon("mdi.monitor" if actif else "mdi.monitor-multiple",
                                  color=_ima.MAIN_FG_COLOR))
        _action.setText(_("Revenir à une fenêtre") if actif else _("Mode deux écrans"))
        _action.setToolTip(
            _("Rapatrier les panneaux du second écran dans la fenêtre principale") if actif
            else _("Déplacer les panneaux de droite dans une fenêtre pour le second écran"))

        # ⚠ REJOUE A CHAQUE BASCULE, PAS UNE SEULE FOIS A LA CREATION : cf.
        # `_smartos_rendre_transparent` pour le pourquoi (le bouton peut avoir ete reconstruit par
        # le greffon Toolbar entre-temps).
        _barre = getattr(self, "_smartos_barre_ecrans", None)
        if _barre is not None:
            self._smartos_rendre_transparent(_barre.widgetForAction(_action))

    def _smartos_installer_deux_ecrans(self):
        """
        Point d'entree du mode, appele une fois l'interface visible.

        Pose la barre, puis REND LE MODE s'il etait actif a la fermeture precedente - sans quoi
        l'utilisateur retrouverait ses panneaux rapatries a chaque demarrage, et le « souvenir »
        des deux dispositions ne servirait a rien.
        """
        try:
            self._smartos_poser_barre_deux_ecrans()
            if self.get_conf(self.SMARTOS_CONF_MODE, default=False):
                # setChecked declenche `toggled`, donc la bascule : une seule source de verite.
                self._smartos_action_ecrans.setChecked(True)
        except Exception:
            logger.exception("Mode deux ecrans non installe")

    def maximize_dockwidget(self, restore=False):
        """
        Maximize current dockwidget.

        Shortcut: Ctrl+Alt+Shift+M
        First call: maximize current dockwidget
        Second call (or restore=True): restore original window layout
        """
        editor = self.get_plugin(Plugins.Editor, error=False)
        outline_explorer = self.get_plugin(
            Plugins.OutlineExplorer,
            error=False
        )

        if self._state_before_maximizing is None:
            if restore:
                return

            # Select plugin to maximize
            focus_widget = QApplication.focusWidget()
            # SmartOS (patch_spyder_deux_ecrans_maximize.py) : un clic sur le bouton "Agrandir"
            # d'un panneau (patch_spyder_pane_maximize_button.py) fixe le focus PUIS bascule
            # l'action dans le MEME clic, synchrone - QApplication.focusWidget() n'a pas encore
            # eu le temps de refleter ce changement (visible seulement apres un tour de boucle
            # Qt). Le bouton a donc deja laisse ICI, explicitement, le panneau qu'il vise -
            # on lui fait confiance a la place d'un focus pas encore a jour, consomme en une fois.
            _smartos_bouton_cible = getattr(self, "_smartos_bouton_agrandir_cible", None)
            self._smartos_bouton_agrandir_cible = None
            if _smartos_bouton_cible is not None:
                focus_widget = _smartos_bouton_cible.get_widget()
            # SmartOS (patch_spyder_deux_ecrans_maximize.py) : agrandir DANS LA FENETRE QUI A LE
            # FOCUS (principale ou second ecran), et non plus toujours dans la principale - cf.
            # l'en-tete de ce patch pour le bug que corrige ce remplacement.
            self._smartos_fenetre_agrandie = self._smartos_fenetre_du_focus(focus_widget)
            _smartos_cible_ecran2 = (
                self._smartos_fenetre_agrandie is getattr(self, "_smartos_fen2", None)
            )
            self._state_before_maximizing = self._smartos_fenetre_agrandie.saveState(
                version=WINDOW_STATE_VERSION
            )

            for plugin in self.get_dockable_plugins():
                # SmartOS (patch_spyder_deux_ecrans.py / _maximize.py) : ne cacher et ne proposer
                # comme candidat que les panneaux de la fenetre VISEE - jamais ceux de l'autre.
                if self._smartos_dock_dans_ecran2(plugin.dockwidget) != _smartos_cible_ecran2:
                    continue
                plugin.dockwidget.hide()
                # ⚠ isAncestorOf(x) rend FAUX quand x EST l'objet lui-meme (Qt : un widget n'est
                # pas son propre ancetre) - sans le "is", le cas du bouton (focus_widget vaut le
                # widget du PANNEAU lui-meme, cf. plus haut) ne matchait jamais rien.
                if plugin.get_widget() is focus_widget or plugin.get_widget().isAncestorOf(focus_widget):
                    self._last_plugin = plugin

            # This prevents a possible error when the value of _last_plugin
            # turns out to be None.
            if self._last_plugin is None:
                # SmartOS (patch_spyder_deux_ecrans_maximize.py) : le repli sur l'Editeur n'a de
                # sens que si la fenetre visee est la PRINCIPALE - l'Editeur n'est jamais envoye
                # vers le second ecran. Y retomber quand meme
                # agrandirait un panneau d'une autre fenetre que celle qu'on regarde : exactement
                # le bug rapporte.
                if not _smartos_cible_ecran2 and editor is not None:
                    self._last_plugin = editor
                else:
                    self._last_plugin = self._smartos_premier_panneau_ecran2()
                if self._last_plugin is None:
                    self._state_before_maximizing = None
                    return

            # Maximize last_plugin
            self._last_plugin.dockwidget.toggleViewAction().setDisabled(True)
            self._smartos_fenetre_agrandie.setCentralWidget(self._last_plugin.get_widget())
            self._last_plugin.get_widget().set_maximized_state(True)

            # Workaround to solve an issue with editor's outline explorer:
            # (otherwise the whole plugin is hidden and so is the outline
            # explorer and the latter won't be refreshed if not visible)
            self._last_plugin.get_widget().show()
            self._last_plugin.change_visibility(True)

            if self._last_plugin is editor:
                # Automatically show the outline if the editor was maximized
                if outline_explorer is not None:
                    outline_explorer.dock_with_maximized_editor()
        else:
            # Restore original layout (before maximizing current dockwidget)
            self._last_plugin.dockwidget.setWidget(
                self._last_plugin.get_widget()
            )
            self._last_plugin.dockwidget.toggleViewAction().setEnabled(True)
            self._smartos_fenetre_agrandie.setCentralWidget(None)
            self._last_plugin.get_widget().set_maximized_state(False)
            self._smartos_fenetre_agrandie.restoreState(
                self._state_before_maximizing, version=WINDOW_STATE_VERSION
            )
            self._smartos_fenetre_agrandie = None
            self._state_before_maximizing = None

            if self._last_plugin is editor:
                if outline_explorer is not None:
                    outline_explorer.hide_from_maximized_editor()

            self._last_plugin.get_widget().get_focus_widget().setFocus()

    def unmaximize_dockwidget(self):
        """Unmaximize any dockable plugin."""
        if self.maximize_action.isChecked():
            self.maximize_action.setChecked(False)

    def unmaximize_other_dockwidget(self, plugin_instance):
        """
        Unmaximize the currently maximized plugin, if not `plugin_instance`.
        """
        last_plugin = self.get_last_plugin()
        is_maximized = False

        if last_plugin is not None:
            is_maximized = (
                last_plugin.get_widget().get_maximized_state()
            )

        if (
            last_plugin is not None
            and is_maximized
            and last_plugin is not plugin_instance
        ):
            self.unmaximize_dockwidget()

    def switch_to_plugin(self, plugin, force_focus=None):
        """
        Switch to `plugin`.

        Notes
        -----
        This operation unmaximizes the current plugin (if any), raises
        this plugin to view (if it's hidden) and gives it focus (if
        possible).
        """
        last_plugin = self.get_last_plugin()

        if (
            last_plugin is not None
            and last_plugin.get_widget().get_maximized_state()
            and last_plugin is not plugin
        ):
            if self.maximize_action.isChecked():
                self.maximize_action.setChecked(False)
            else:
                self.maximize_action.setChecked(True)

        if not plugin.toggle_view_action.isChecked():
            plugin.toggle_view_action.setChecked(True)
            plugin.get_widget().is_visible = False

        if plugin.get_widget().windowwidget:
            # This is necessary to give focus to undocked plugin windows
            # from plugins in the main one when using the "switch to
            # plugin" shortcuts. It also allows to switch between different
            # undocked windows with those shortcuts.
            # Fixes spyder-ide/spyder#1351
            plugin.get_widget().windowwidget.activateWindow()
        else:
            plugin.change_visibility(True, force_focus=force_focus)
            self.main.activateWindow()

    # ---- Menus and actions
    # -------------------------------------------------------------------------
    @Slot()
    def toggle_fullscreen(self):
        """
        Toggle option to show the mainwindow in fullscreen or windowed.
        """
        main = self.main
        if self._fullscreen_flag:
            self._fullscreen_flag = False
            if os.name == 'nt':
                main.setWindowFlags(
                    main.windowFlags()
                    ^ (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint))
                main.setGeometry(self._saved_normal_geometry)
            main.showNormal()
            if self._maximized_flag:
                main.showMaximized()
        else:
            self._maximized_flag = main.isMaximized()
            self._fullscreen_flag = True
            self._saved_normal_geometry = main.normalGeometry()
            if os.name == 'nt':
                # Due to limitations of the Windows DWM, compositing is not
                # handled correctly for OpenGL based windows when going into
                # full screen mode, so we need to use this workaround.
                # See spyder-ide/spyder#4291.
                main.setWindowFlags(main.windowFlags()
                                    | Qt.FramelessWindowHint
                                    | Qt.WindowStaysOnTopHint)

                r = main.screen().geometry()
                main.setGeometry(
                    r.left() - 1, r.top() - 1, r.width() + 2, r.height() + 2)
                main.showNormal()
            else:
                main.showFullScreen()
        self._update_fullscreen_action()

    @property
    def plugins_menu(self):
        """Expose plugins toggle actions menu."""
        return self.get_container()._plugins_menu

    def create_plugins_menu(self):
        """
        Populate panes menu with the toggle view action of each base plugin.
        """
        order = [
            "editor",
            "ipython_console",
            "variable_explorer",
            "debugger",
            "help",
            "plots",
            None,
            "explorer",
            "outline_explorer",
            "project_explorer",
            "find_in_files",
            None,
            "historylog",
            "profiler",
            "pylint",
            None,
            "onlinehelp",
            "internal_console",
            None,
        ]

        for plugin in self.get_dockable_plugins():
            action = plugin.toggle_view_action
            if action:
                # Plugins that fail their compatibility checks don't have a
                # dockwidget. So, we need to skip them from the plugins menu.
                # Fixes spyder-ide/spyder#21074
                if plugin.dockwidget is None:
                    continue
                else:
                    action.setChecked(plugin.dockwidget.isVisible())

            try:
                name = plugin.CONF_SECTION
                pos = order.index(name)
            except ValueError:
                pos = None

            if pos is not None:
                order[pos] = action
            else:
                order.append(action)

        actions = order[:]
        for action in actions:
            if type(action) is not str:
                self.plugins_menu.add_action(action)

        # Enable shortcuts when the menu is visible so users can see they are
        # available. And disable those shortcuts when the menu is hidden
        # because they allow to hide plugins when pressed twice. See:
        # https://github.com/spyder-ide/spyder/issues/22189#issuecomment-2248644546
        self.plugins_menu.aboutToShow.connect(
            lambda: self._update_shortcuts_in_plugins_menu(show=True)
        )
        self.plugins_menu.aboutToHide.connect(
            lambda: self._update_shortcuts_in_plugins_menu(show=False)
        )

    @property
    def lock_interface_action(self):
        return self.get_container()._lock_interface_action

    def toggle_lock(self, value=None):
        """Lock/Unlock dockwidgets and toolbars."""
        self._interface_locked = (
            not self._interface_locked if value is None else value)
        self.set_conf('panes_locked', self._interface_locked, 'main')
        self._update_lock_interface_action()
        # Apply lock to panes
        for plugin in self.get_dockable_plugins():
            # Plugins that fail their compatibility checks don't have a
            # dockwidget. So, we need to skip them from the code below.
            # Fixes spyder-ide/spyder#21074
            if plugin.dockwidget is None:
                continue

            if self._interface_locked:
                if plugin.dockwidget.isFloating():
                    plugin.dockwidget.setFloating(False)

                plugin.dockwidget.remove_title_bar()
            else:
                plugin.dockwidget.set_title_bar()

        # Apply lock to toolbars
        toolbar = self.get_plugin(Plugins.Toolbar)
        if toolbar:
            toolbar.toggle_lock(value=self._interface_locked)

    # ---- Visible dockable plugins
    # -------------------------------------------------------------------------
    def restore_visible_plugins(self):
        """
        Restore dockable plugins that were visible during the previous session.
        """
        logger.info("Restoring visible plugins from the previous session")
        visible_plugins = self.get_conf('last_visible_plugins', default=[])

        # This should only be necessary the first time this method is run
        if not visible_plugins:
            visible_plugins = [
                Plugins.IPythonConsole,
                Plugins.Help,
                Plugins.VariableExplorer,  # In case Help is not available
                Plugins.Editor,
            ]

        # This is necessary because the visible state of dockable plugins is
        # not set correctly for all of them at startup. So, we make it False
        # first for all of them to then set it to True only for those that were
        # visible during the last session.
        for plugin in self.get_dockable_plugins():
            plugin.change_visibility(False)

        # Restore visible plugins
        for plugin in visible_plugins:
            plugin_class = self.get_plugin(plugin, error=False)
            if (
                plugin_class
                # This check is necessary for spyder-ide/spyder#21074
                and plugin_class.dockwidget is not None
                and plugin_class.dockwidget.isVisible()
            ):
                plugin_class.change_visibility(True, force_focus=False)

    def save_visible_plugins(self):
        """Save visible plugins."""
        logger.debug("Saving visible plugins to config system")

        visible_plugins = []
        for plugin in self.get_dockable_plugins():
            if plugin.get_widget().is_visible:
                visible_plugins.append(plugin.NAME)

        self.set_conf('last_visible_plugins', visible_plugins)

    # ---- Tabify plugins
    # -------------------------------------------------------------------------
    def tabify_plugins(self, first, second):
        """Tabify plugin dockwigdets."""
        self.main.tabifyDockWidget(first.dockwidget, second.dockwidget)

    def tabify_plugin(self, plugin, default=None):
        """
        Tabify `plugin` using the list of possible TABIFY options.

        Only do this if the dockwidget does not have more dockwidgets
        in the same position and if the plugin is using the New API.
        """
        def tabify_helper(plugin, next_to_plugins):
            for next_to_plugin in next_to_plugins:
                try:
                    self.tabify_plugins(next_to_plugin, plugin)
                    break
                except SpyderAPIError as err:
                    logger.error(err)

        # If TABIFY not defined use the [default]
        tabify = getattr(plugin, 'TABIFY', [default])
        if not isinstance(tabify, list):
            next_to_plugins = [tabify]
        else:
            next_to_plugins = tabify

        # Check if TABIFY is not a list with None as unique value or a default
        # list
        if tabify in [[None], []]:
            return False

        # Get the actual plugins from their names
        next_to_plugins = [
            self.get_plugin(p, error=False) for p in next_to_plugins
        ]

        # Remove not available plugins from next_to_plugins
        next_to_plugins = [
            p for p in next_to_plugins if p is not None
        ]

        if plugin.get_conf('first_time', True):
            # This tabifies external and internal plugins that are loaded for
            # the first time, and internal ones that are not part of the
            # default layout.
            if (
                isinstance(plugin, SpyderDockablePlugin)
                and plugin.NAME != Plugins.Console
            ):
                logger.info(
                    f"Tabifying {plugin.NAME} plugin for the first time next "
                    f"to {next_to_plugins}"
                )
                tabify_helper(plugin, next_to_plugins)

                # Show external plugins
                if plugin.NAME in PLUGIN_REGISTRY.external_plugins:
                    plugin.get_widget().toggle_view(True)

            plugin.set_conf('enable', True)
            plugin.set_conf('first_time', False)
        else:
            # This is needed to ensure that, when switching to a different
            # layout, any plugin (external or internal) not part of its
            # declared areas is tabified as expected.
            # Note: Check if `plugin` has no other dockwidgets in the same
            # position before proceeding.
            if not bool(self.main.tabifiedDockWidgets(plugin.dockwidget)):
                logger.info(f"Tabifying {plugin.NAME} plugin")
                tabify_helper(plugin, next_to_plugins)

        return True

    def tabify_new_plugins(self):
        """
        Tabify new dockable plugins, i.e. plugins that were not part of the
        interface in the last session.

        Notes
        -----
        This is only necessary the first time a plugin is loaded. Afterwards,
        the plugin's placement is recorded in the window hexstate, which is
        loaded in the next session.
        """
        # Detect if a new dockable internal plugin hasn't been added to the
        # DockablePlugins enum and raise an error if that's the case.
        for plugin in self.get_dockable_plugins():
            if (
                plugin.NAME in PLUGIN_REGISTRY.internal_plugins
                and plugin.NAME not in self._get_internal_dockable_plugins()
            ):
                raise SpyderAPIError(
                    f"Plugin {plugin.NAME} is a new dockable plugin but it "
                    f"hasn't been added to the DockablePlugins enum. Please "
                    f"do that to avoid this error."
                )

        # If this is the first time Spyder runs, then we don't need to go
        # beyond this point because all plugins are tabified in the
        # set_main_window_layout method of any layout.
        if self._first_spyder_run:
            # Save the list of internal dockable plugins to compare it with
            # the current ones during the next session.
            self.set_conf(
                'internal_dockable_plugins',
                self._get_internal_dockable_plugins()
            )
            return

        logger.debug("Tabifying new plugins")

        # Get the list of internal dockable plugins that were present in the
        # last session to decide which ones need to be tabified.
        last_internal_dockable_plugins = self.get_conf(
            'internal_dockable_plugins',
            default=self._get_internal_dockable_plugins()
        )

        # Tabify new internal plugins
        for plugin_name in self._get_internal_dockable_plugins():
            if plugin_name not in last_internal_dockable_plugins:
                plugin = self.get_plugin(plugin_name, error=False)
                if plugin:
                    self.tabify_plugin(plugin, Plugins.Console)

        # Tabify any new plugin that was not available in the previous session.
        # This can include internal plugins that were automatically disabled
        # in the first session (e.g. due to the lack of WebEngine).
        for plugin in self.get_dockable_plugins():
            if plugin.get_conf('first_time', True):
                self.tabify_plugin(plugin, Plugins.Console)

                # This is necessary in case the plugin doesn't set its TABIFY
                # constant
                plugin.set_conf("enable", True)
                plugin.set_conf("first_time", False)

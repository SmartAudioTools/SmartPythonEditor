# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
Main menu Plugin.
"""

# Standard library imports
from collections import OrderedDict
import os
import sys
from qtpy.QtCore import Qt, QEvent, QObject, QPoint, QSize, QTimer
from qtpy.QtGui import QKeySequence
from qtpy.QtWidgets import QSizePolicy, QToolBar, QToolButton, QWidget
from typing import Dict, List, Tuple, Optional, Union

# Local imports
from spyder.api.exceptions import SpyderAPIError
from spyder.api.fonts import SpyderFontType
from spyder.api.plugin_registration.registry import PLUGIN_REGISTRY
from spyder.api.plugins import SpyderPluginV2, SpyderDockablePlugin, Plugins
from spyder.api.translations import _
from spyder.api.widgets.menus import SpyderMenu
from spyder.api.widgets.mixins import SpyderMenuMixin
from spyder.plugins.mainmenu.api import (
    ApplicationMenu,
    ApplicationMenus,
    MENUBAR_STYLESHEET,
)
from spyder.utils.qthelpers import SpyderAction
from spyder.plugins.toolbar.api import ApplicationToolbars
from spyder.utils.icon_manager import ima


# Extended typing definitions
ItemType = Union[SpyderAction, SpyderMenu]
ItemSectionBefore = Tuple[
    ItemType, Optional[str], Optional[str], Optional[str]]
ItemQueue = Dict[str, List[ItemSectionBefore]]


class MainMenu(SpyderPluginV2, SpyderMenuMixin):
    NAME = 'mainmenu'
    CONF_SECTION = NAME
    CONF_FILE = False
    CAN_BE_DISABLED = False

    @staticmethod
    def get_name():
        return _('Main menus')

    @classmethod
    def get_icon(cls):
        return cls.create_icon('genprefs')

    @staticmethod
    def get_description():
        return _('Provide main application menu management.')

    def on_initialize(self):
        # Reference holder dict for the menus
        self._APPLICATION_MENUS = OrderedDict()

        # Queue that contain items that are pending to add to a non-existing
        # menu
        self._ITEM_QUEUE = {}  # type: ItemQueue

        # Set style. This is only necessary on Windows and Linux
        if not sys.platform == 'darwin':
            app_font = self.get_font(font_type=SpyderFontType.Interface)
            self.main.menuBar().setFont(app_font)
            self.main.menuBar().setStyleSheet(str(MENUBAR_STYLESHEET))

        # Create Application menus using plugin public API
        create_app_menu = self.create_application_menu
        create_app_menu(ApplicationMenus.File, _("&File"))
        create_app_menu(ApplicationMenus.Edit, _("&Edit"))
        create_app_menu(ApplicationMenus.Search, _("&Search"))
        create_app_menu(ApplicationMenus.Source, _("Sour&ce"))
        create_app_menu(ApplicationMenus.Run, _("&Run"))
        create_app_menu(ApplicationMenus.Debug, _("&Debug"))
        if self.is_plugin_enabled(Plugins.IPythonConsole):
            create_app_menu(ApplicationMenus.Consoles, _("C&onsoles"))
        if self.is_plugin_enabled(Plugins.Projects):
            create_app_menu(
                ApplicationMenus.Projects,
                _("&Projects"),
                min_width=150 if os.name == "nt" else 170
            )
        create_app_menu(ApplicationMenus.Tools, _("&Tools"))
        create_app_menu(ApplicationMenus.Window, _("&Window"))
        create_app_menu(ApplicationMenus.Help, _("&Help"))

        # Regroupe tous les menus ci-dessus dans un unique menu burger (TODO CachyOS du
        # 19/07/2026, "Burger menu pour Spyder ?"). Cf. docstring de
        # patch_spyder_burger_menu.py pour le detail complet.
        self._build_burger_menu()

    def _build_burger_menu(self):
        """
        Retire tous les menus d'application de la barre de menus et les regroupe comme
        sous-menus d'un unique menu burger, garde en reference sur self._burger_menu (TODO
        CachyOS du 19/07/2026, "Burger menu pour Spyder ?" - demande explicite de l'utilisateur).
        Cf. docstring de patch_spyder_burger_menu.py pour le detail complet.
        """
        menu_bar = self.main.menuBar()
        self._burger_menu = self._create_menu(
            menu_id='burger_menu', parent=self.main, title='☰'
        )
        for menu in list(self._APPLICATION_MENUS.values()):
            menu_bar.removeAction(menu.menuAction())
            self._burger_menu.addMenu(menu)

    def _install_burger_menu_button(self):
        """
        Dispose la rangee du haut : bouton burger dans SA PROPRE barre tout a gauche, barre
        Fichiers collee a sa suite, puis une DragArea qui - combinee a celle de window_controls a
        droite - CENTRE au milieu le groupe des autres barres (les boutons de fenetre restent a
        droite). Le burger est cache par defaut (affiche par Ctrl+M) ; la barre de menus classique,
        desormais vide, est masquee (TODO CachyOS du 19/07/2026 puis 24/07/2026, demande explicite
        de l'utilisateur). Cf. docstring de patch_spyder_burger_menu.py pour le detail complet
        (dont pourquoi cette methode est appelee depuis on_mainwindow_visible et non on_initialize,
        et le conflit Ctrl+M).
        """
        main = self.main
        toolbar_plugin = main.get_plugin(Plugins.Toolbar, error=False)

        try:
            from spyder_window_controls.spyder.widgets import (
                DragArea as _DragArea)
        except Exception:
            _DragArea = None

        burger_button = QToolButton(main)
        burger_button.setPopupMode(QToolButton.InstantPopup)
        burger_button.setIcon(ima.icon('tooloptions'))
        burger_button.setToolTip(_("Menu"))
        burger_button.setMenu(self._burger_menu)

        # Barre d'outils dediee au seul bouton burger, cachee par defaut (affichee par Ctrl+M).
        burger_toolbar = QToolBar(_("Menu"), main)
        burger_toolbar.setObjectName('spyder_burger_toolbar')
        burger_toolbar.setIconSize(QSize(24, 24))
        burger_toolbar.addWidget(burger_button)
        main.addToolBar(burger_toolbar)
        if toolbar_plugin is not None:
            try:  # meme etat verrouille/deverrouille (poignee) que les autres barres
                burger_toolbar.setMovable(
                    toolbar_plugin.get_application_toolbar(
                        ApplicationToolbars.File).isMovable())
            except Exception:
                pass

        # DragArea GAUCHE, entre le burger/Fichiers (epingles a gauche) et le groupe centre.
        # Combinee a la DragArea de window_controls (a droite, avant les boutons de fenetre), elle
        # CENTRE le groupe ; les DEUX vides restent des poignees de deplacement de la fenetre.
        # Repli en QWidget extensible si window_controls (donc DragArea) est absent.
        dragbar = QToolBar(main)
        dragbar.setObjectName('spyder_burger_dragarea')
        dragbar.setMovable(False)
        dragbar.setFloatable(False)
        drag = _DragArea(main) if _DragArea is not None else QWidget(main)
        if _DragArea is None:
            drag.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        dragbar.addWidget(drag)
        main.addToolBar(dragbar)

        # Centrage : QMainWindow donne TOUT l'espace libre a la barre extensible la plus a droite
        # (la DragArea de window_controls), jamais un partage entre deux. On CALCULE donc la largeur
        # de la DragArea gauche pour que le groupe soit centre au milieu ; celle de droite remplit
        # le reste. Le groupe = les barres GEREES du haut sauf window_controls (a droite) et
        # Fichiers (epinglee a gauche). burger et dragbar sont des QToolBar nues, hors toolbarslist.
        # Barres EPINGLEES A GAUCHE, aux cotes de Fichiers : elles ne font pas partie du groupe
        # centre, elles le decalent. La liste est ouverte - un autre patch peut y ajouter la sienne
        # en posant son objectName dans main._smartos_barres_epinglees_gauche (c'est ce que fait
        # patch_spyder_deux_ecrans.py pour la barre « Écrans », a la demande de l'utilisateur du
        # 31/07/2026 : « je veux la barre d'outils pour les deux écrans plutôt à gauche collé à
        # celle des fichiers »). Elle est LUE A CHAQUE APPEL, et non capturee ici : les barres qui
        # se declarent tard - apres tous les on_mainwindow_visible - arrivent apres ce code.
        def _epingles_a_gauche():
            return ('file_toolbar',) + tuple(
                getattr(main, '_smartos_barres_epinglees_gauche', ()))

        def _recenter():
            if toolbar_plugin is None:
                return
            epingles = _epingles_a_gauche()
            group_w = pinned = 0
            for tb in toolbar_plugin.toolbarslist:
                if not (tb.isVisible()
                        and main.toolBarArea(tb) == Qt.TopToolBarArea):
                    continue
                on = tb.objectName()
                if on == 'window_controls_toolbar':
                    continue
                if on in epingles:
                    pinned += tb.width()
                else:
                    group_w += tb.width()
            if burger_toolbar.isVisible():
                pinned += burger_toolbar.width()
            drag.setFixedWidth(
                max(0, main.width() // 2 - pinned - group_w // 2))

        # PUBLIE sur main : la bascule deux ecrans (patch_spyder_deux_ecrans.py) passe par
        # restoreState() SANS redimensionner la fenetre principale (le second ecran a sa propre
        # fenetre), donc rien ne garantit qu'un evenement de taille rejoue le recentrage - la
        # DragArea gauche garde la largeur figee calculee dans l'autre mode (releve utilisateur
        # du 08/08/2026 : « les boutons du milieu ne retournent pas au milieu »).
        main._smartos_recentrer_barres = _recenter

        # Placement DIFFERE en fin de boucle d'evenements (QTimer.singleShot), apres que le plugin
        # Toolbar a dispose ses barres - meme technique que spyder_window_controls. Ordre vise :
        # burger, Fichiers, DragArea gauche, puis le groupe. L'ancre est la barre du groupe la plus
        # a GAUCHE reperee par sa GEOMETRIE (x) : l'ordre de toolbarslist ne suit pas l'ordre visuel.
        def _place_left():
            if toolbar_plugin is None:
                _recenter()
                return
            file_tb = None
            try:
                file_tb = toolbar_plugin.get_application_toolbar(
                    ApplicationToolbars.File)
            except Exception:
                pass
            epingles = _epingles_a_gauche()
            cands = [tb for tb in toolbar_plugin.toolbarslist
                     if tb.isVisible()
                     and main.toolBarArea(tb) == Qt.TopToolBarArea
                     and tb.objectName() != 'window_controls_toolbar'
                     and tb.objectName() not in epingles]
            if cands:
                anchor = min(
                    cands, key=lambda tb: tb.mapTo(main, QPoint(0, 0)).x())
                main.insertToolBar(anchor, dragbar)
                # Les epinglees additionnelles viennent JUSTE AVANT la DragArea gauche, donc
                # collees a Fichiers ; puis Fichiers devant elles, puis le burger tout a gauche.
                suivante = dragbar
                for nom in reversed(epingles[1:]):
                    tb = next((t for t in toolbar_plugin.toolbarslist
                               if t.objectName() == nom), None)
                    if tb is not None:
                        main.insertToolBar(suivante, tb)
                        suivante = tb
                if file_tb is not None:
                    main.insertToolBar(suivante, file_tb)
                    main.insertToolBar(file_tb, burger_toolbar)
                else:
                    main.insertToolBar(suivante, burger_toolbar)
            _recenter()

        # Deux passes de placement : l'ancre par geometrie n'est fiable qu'une fois la rangee
        # disposee.
        QTimer.singleShot(0, _place_left)
        QTimer.singleShot(400, _place_left)

        # RECENTRAGE PILOTE PAR L'ETAT REEL, PLUS PAR UNE MINUTERIE. Le recentrage se contentait
        # d'une troisieme passe a 700 ms "quand les combos ont leur taille finale", puis ne
        # rejouait plus que sur redimensionnement de la fenetre. Or ce delai est un pari sur la
        # charge de la machine : une barre peut prendre sa largeur DEFINITIVE apres, le combo
        # d'interpreteurs en tete (sa liste est reecrite a CHAQUE lancement par
        # SmartPythonEditor.sh, donc sa largeur n'est pas connue au demarrage), et une barre de
        # greffon peut se declarer plus tard encore. Le groupe restait alors decale jusqu'au
        # demarrage suivant : un defaut INTERMITTENT, qui disparait au simple relancement et
        # resiste donc a tout diagnostic par la configuration. Releve utilisateur du 31/08/2026
        # apres reinstallation ("les boutons ne sont pas la ou on les avait places"), alors que la
        # configuration enregistree etait exacte, ordre ET centrage compris - mesure faite sur une
        # copie de sa configuration vivante, rejouee hors ecran.
        # On surveille donc les evenements de TAILLE des barres de la rangee, et l'ARRIVEE d'une
        # barre nouvelle, au lieu d'attendre un delai.
        _minuterie = QTimer(main)
        _minuterie.setSingleShot(True)
        _minuterie.setInterval(0)
        _minuterie.timeout.connect(_recenter)

        def _recentrer_bientot():
            # start() sur une minuterie deja armee la REDEMARRE : une rafale d'evenements (un
            # relayout en produit plusieurs d'affilee) se resout donc en UN seul recalcul, en fin
            # de boucle d'evenements.
            _minuterie.start()

        class _SurveillanceRangee(QObject):
            def eventFilter(self, obj, event):
                type_evenement = event.type()
                if type_evenement == QEvent.Resize:
                    # Resize couvre aussi celui de la fenetre principale, surveillee ci-dessous :
                    # c'est ce qui remplace la connexion a main.sig_resized.
                    _recentrer_bientot()
                elif type_evenement == QEvent.ChildAdded:
                    _surveiller_barres()
                return False

        _surveillance = _SurveillanceRangee(main)

        def _surveiller_barres():
            # dragbar est EXCLUE : c'est la barre que _recenter redimensionne lui-meme, la
            # surveiller la ferait se rappeler indefiniment. Les autres convergent en une passe,
            # leur largeur ne dependant pas de la sienne.
            # Pas de liste des barres deja vues : Qt ignore la reinstallation d'un filtre deja
            # pose (il le remonte en tete de liste), il n'y a donc rien a dedupliquer.
            barres = [burger_toolbar]
            if toolbar_plugin is not None:
                barres += list(toolbar_plugin.toolbarslist)
            for tb in barres:
                if tb is not dragbar:
                    tb.installEventFilter(_surveillance)
            _recentrer_bientot()

        # ChildAdded sur la fenetre principale : une barre ajoutee APRES ce code (greffon installe
        # plus tard, barre recochee dans Affichage > Barres d'outils) entre ainsi d'elle-meme sous
        # surveillance, sans qu'aucun appelant ait a le savoir.
        main.installEventFilter(_surveillance)
        _surveiller_barres()

        burger_toolbar.setVisible(False)  # cache par defaut ; Ctrl+M l'affiche

        toggle_action = burger_toolbar.toggleViewAction()
        toggle_action.setShortcut(QKeySequence("Ctrl+M"))
        toggle_action.setShortcutContext(Qt.ApplicationShortcut)
        toggle_action.toggled.connect(lambda *a: _recenter())
        main.addAction(toggle_action)

        main.menuBar().setVisible(False)

    def on_mainwindow_visible(self):
        # Pre-render menus so actions with menu roles (like "About Spyder" and
        # "Preferences") are located in the right place in Mac's menu bar.
        # Fixes spyder-ide/spyder#14917
        # This also registers shortcuts for actions that are only in menus.
        # Fixes spyder-ide/spyder#16061
        for menu in self._APPLICATION_MENUS.values():
            menu.render()

        # Installe le bouton burger dans la toolbar File une fois que TOUS les plugins
        # (dont Toolbar) ont fini leur on_initialize() - cf. docstring de
        # patch_spyder_burger_menu.py pour le detail complet.
        self._install_burger_menu_button()

    # ---- Private methods
    # ------------------------------------------------------------------------
    def _hide_options_menus(self):
        """Hide options menu when menubar is pressed in macOS."""
        for plugin_name in PLUGIN_REGISTRY:
            plugin_instance = PLUGIN_REGISTRY.get_plugin(plugin_name)
            if isinstance(plugin_instance, SpyderDockablePlugin):
                if plugin_instance.CONF_SECTION == 'editor':
                    editorstack = self._main.editor.get_current_editorstack()
                    editorstack.menu.hide()
                else:
                    plugin_instance.options_menu.hide()

    # ---- Public API
    # ------------------------------------------------------------------------
    def create_application_menu(
        self,
        menu_id: str,
        title: str,
        min_width: Optional[int] = None
    ):
        """
        Create a Spyder application menu.

        Parameters
        ----------
        menu_id: str
            The menu unique identifier string.
        title: str
            The localized menu title to be displayed.
        min_width: int
            Minimum width for the menu in pixels.
        """
        if menu_id in self._APPLICATION_MENUS:
            raise SpyderAPIError(
                'Menu with id "{}" already added!'.format(menu_id)
            )

        menu = self._create_menu(
            menu_id=menu_id,
            parent=self.main,
            title=title,
            min_width=min_width,
            MenuClass=ApplicationMenu
        )
        self._APPLICATION_MENUS[menu_id] = menu
        self.main.menuBar().addMenu(menu)

        if sys.platform == 'darwin':
            menu.aboutToShow.connect(self._hide_options_menus)

            # This is necessary because for some strange reason the
            # "Configuration per file" entry disappears after showing other
            # dialogs and the only way to make it visible again is by
            # re-rendering the menu.
            if menu_id == ApplicationMenus.Run:
                menu.aboutToShow.connect(lambda: menu.render(force=True))

        if menu_id in self._ITEM_QUEUE:
            pending_items = self._ITEM_QUEUE.pop(menu_id)
            for pending in pending_items:
                (item, section,
                 before_item, before_section) = pending
                self.add_item_to_application_menu(
                    item, menu_id=menu_id, section=section,
                    before=before_item, before_section=before_section)

        return menu

    def add_item_to_application_menu(self, item: ItemType,
                                     menu_id: Optional[str] = None,
                                     section: Optional[str] = None,
                                     before: Optional[str] = None,
                                     before_section: Optional[str] = None,
                                     omit_id: bool = False):
        """
        Add action or widget `item` to given application menu `section`.

        Parameters
        ----------
        item: SpyderAction or SpyderMenu
            The item to add to the `menu`.
        menu_id: str or None
            The application menu unique string identifier.
        section: str or None
            The section id in which to insert the `item` on the `menu`.
        before: str
            Make the item appear before the given object identifier.
        before_section: Section or None
            Make the item section (if provided) appear before another
            given section.
        omit_id: bool
            If True, then the menu will check if the item to add declares an
            id, False otherwise. This flag exists only for items added on
            Spyder 4 plugins. Default: False

        Notes
        -----
        Must provide a `menu` or a `menu_id`.
        """
        if not isinstance(item, (SpyderAction, SpyderMenu)) and not omit_id:
            raise SpyderAPIError('A menu only accepts items objects of type '
                                 'SpyderAction or SpyderMenu')

        if menu_id not in self._APPLICATION_MENUS:
            pending_menu_items = self._ITEM_QUEUE.get(menu_id, [])
            pending_menu_items.append((item, section, before,
                                       before_section))
            self._ITEM_QUEUE[menu_id] = pending_menu_items
        else:
            menu = self.get_application_menu(menu_id)
            menu.add_action(item, section=section, before=before,
                            before_section=before_section, omit_id=omit_id)

    def remove_application_menu(self, menu_id: str):
        """
        Remove a Spyder application menu.

        Parameters
        ----------
        menu_id: str
            The menu unique identifier string.
        """
        if menu_id in self._APPLICATION_MENUS:
            menu = self._APPLICATION_MENUS.pop(menu_id)
            self.main.menuBar().removeAction(menu.menuAction())

    def remove_item_from_application_menu(self, item_id: str,
                                          menu_id: Optional[str] = None):
        """
        Remove action or widget from given application menu by id.

        Parameters
        ----------
        item_id: str
            The item identifier to remove from the given menu.
        menu_id: str or None
            The application menu unique string identifier.
        """
        if menu_id not in self._APPLICATION_MENUS:
            raise SpyderAPIError('{} is not a valid menu_id'.format(menu_id))

        menu = self.get_application_menu(menu_id)
        menu.remove_action(item_id)

    def get_application_menu(self, menu_id: str) -> SpyderMenu:
        """
        Return an application menu by menu unique id.

        Parameters
        ----------
        menu_id: ApplicationMenu
            The menu unique identifier string.
        """
        if menu_id not in self._APPLICATION_MENUS:
            raise SpyderAPIError(
                'Application menu "{0}" not found! Available '
                'menus are: {1}'.format(
                    menu_id, list(self._APPLICATION_MENUS.keys()))
            )

        return self._APPLICATION_MENUS[menu_id]

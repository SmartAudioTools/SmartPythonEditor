# -*- coding: utf-8 -*-
"""Chronometre de demarrage de SmartPythonEditor (ajout SmartOS).

Active par la variable d'environnement SMARTOS_STARTUP_TRACE=<chemin.json> ; sans elle, ce
module n'est jamais importe (voir patch_spyder_startup_trace.py, un seul point d'appel au
debut de spyder.app.start.main).

Tous les instants sont en millisecondes depuis la CREATION DU PROCESSUS python (lue dans
/proc, donc l'interpreteur et les imports de tete de start.py sont comptes). Si le lanceur
exporte SMARTOS_T0 (secondes epoch, `date +%s.%N`), `lanceur_ms` donne en plus ce que le
shell a coute avant le processus.

Rien n'est modifie dans le code de Spyder : les reperes sont poses par enveloppement a
l'execution, et une trace qui echoue ne doit jamais empecher l'editeur de s'ouvrir.
"""

import atexit
import functools
import importlib.machinery
import json
import os
import sys
import time

_ETAT = {
    "reperes": {},      # nom -> ms depuis la creation du processus
    "durees": {},       # nom -> ms
    "greffons": [],     # [nom, ms], dans l'ordre d'instanciation
    "contexte": {},
}
_CHEMIN = None
_DEBUT_PROCESSUS = None  # en secondes CLOCK_BOOTTIME


def _maintenant_ms():
    return (time.clock_gettime(time.CLOCK_BOOTTIME) - _DEBUT_PROCESSUS) * 1000.0


def _debut_processus():
    """Instant de creation du processus, en secondes CLOCK_BOOTTIME (resolution 10 ms)."""
    with open("/proc/self/stat") as flux:
        champs = flux.read().rsplit(")", 1)[1].split()
    return int(champs[19]) / os.sysconf("SC_CLK_TCK")


def repere(nom):
    """Horodate `nom` (la premiere fois seulement)."""
    if nom not in _ETAT["reperes"]:
        _ETAT["reperes"][nom] = round(_maintenant_ms(), 1)


def _charge():
    try:
        return os.getloadavg()[0]
    except OSError:
        return None


def ecrire():
    if _CHEMIN is None:
        return
    _ETAT["contexte"]["charge_fin"] = _charge()
    _ETAT["contexte"]["modules_fin"] = len(sys.modules)
    try:
        temporaire = _CHEMIN + ".tmp"
        with open(temporaire, "w", encoding="utf-8") as flux:
            json.dump(_ETAT, flux, indent=1, ensure_ascii=False)
        os.replace(temporaire, _CHEMIN)
    except OSError:
        pass


def _chronometrer(conteneur, attribut, nom):
    """Enveloppe conteneur.attribut : repere `<nom>_debut`, `<nom>_fin` et duree."""
    original = getattr(conteneur, attribut)

    @functools.wraps(original)
    def enveloppe(*args, **kwargs):
        repere(nom + "_debut")
        debut = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            _ETAT["durees"][nom] = round(
                _ETAT["durees"].get(nom, 0) + (time.perf_counter() - debut) * 1000, 1)
            repere(nom + "_fin")

    setattr(conteneur, attribut, enveloppe)
    return original


class _GuetteurMainwindow:
    """Chercheur d'import qui ne sert qu'a encadrer l'import de spyder.app.mainwindow."""

    CIBLE = "spyder.app.mainwindow"

    def find_spec(self, nom, chemin, cible=None):
        if nom != self.CIBLE:
            return None
        sys.meta_path.remove(self)
        spec = importlib.machinery.PathFinder.find_spec(nom, chemin)
        if spec is None or spec.loader is None:
            return None
        executer = spec.loader.exec_module

        def exec_module(module):
            repere("import_mainwindow_debut")
            executer(module)
            repere("import_mainwindow_fin")
            _ETAT["contexte"]["modules_apres_import_mainwindow"] = len(sys.modules)
            try:
                _instrumenter(module)
            except Exception:
                import traceback
                traceback.print_exc()

        spec.loader.exec_module = exec_module
        return spec


def _instrumenter(mainwindow):
    from spyder.api.plugin_registration.registry import PLUGIN_REGISTRY

    _chronometrer(mainwindow, "create_application", "create_application")
    _chronometrer(mainwindow, "find_internal_plugins", "decouverte_greffons_internes")
    _chronometrer(mainwindow, "find_external_plugins", "decouverte_greffons_externes")

    instancier = PLUGIN_REGISTRY._instantiate_spyder_plugin

    def _instantiate_spyder_plugin(main_window, PluginClass, external):
        debut = time.perf_counter()
        try:
            return instancier(main_window, PluginClass, external)
        finally:
            _ETAT["greffons"].append([
                getattr(PluginClass, "NAME", PluginClass.__name__),
                round((time.perf_counter() - debut) * 1000, 1)])

    PLUGIN_REGISTRY._instantiate_spyder_plugin = _instantiate_spyder_plugin

    Fenetre = mainwindow.MainWindow
    _chronometrer(Fenetre, "__init__", "mainwindow_init")
    _chronometrer(Fenetre, "pre_visible_setup", "pre_visible_setup")

    setup = Fenetre.setup

    @functools.wraps(setup)
    def setup_trace(self):
        repere("setup_debut")
        try:
            return setup(self)
        finally:
            repere("setup_fin")
            _ETAT["contexte"]["modules_apres_setup"] = len(sys.modules)
            _suivre_noyau_et_lsp(self)

    Fenetre.setup = setup_trace

    def show(self):
        repere("show_debut")
        super(Fenetre, self).show()
        repere("fenetre_visible")

    Fenetre.show = show

    post_visible_setup = Fenetre.post_visible_setup

    @functools.wraps(post_visible_setup)
    def post_visible_setup_trace(self):
        repere("post_visible_setup_debut")
        try:
            return post_visible_setup(self)
        finally:
            repere("post_visible_setup_fin")
            _suivre_noyau_et_lsp(self)
            from qtpy.QtCore import QTimer
            # Premier retour au repos de la boucle d'evenements : c'est l'instant ou
            # l'editeur repond au clavier.
            QTimer.singleShot(0, lambda: _utilisable(self))

    Fenetre.post_visible_setup = post_visible_setup_trace


def _utilisable(fenetre):
    repere("editeur_utilisable")
    try:
        editeur = fenetre.get_plugin("editor", error=False)
        if editeur is not None:
            _ETAT["contexte"]["fichiers_ouverts"] = len(editeur.get_filenames())
    except Exception:
        pass
    ecrire()


def _jalon(nom):
    def recepteur(*args):
        if nom not in _ETAT["reperes"]:
            repere(nom)
            ecrire()
    return recepteur


_SUIVIS = set()
_RECEPTEURS = []  # references gardees : PySide ne retient pas toujours une fermeture


def _suivre_noyau_et_lsp(fenetre):
    """Branche `noyau_pret` et `lsp_pret` (appele apres setup, puis apres l'affichage)."""
    try:
        console = fenetre.get_plugin("ipython_console", error=False)
        if console is not None:
            if "console" not in _SUIVIS:
                _SUIVIS.add("console")

                def nouveau_shell(shell):
                    recepteur = _jalon("noyau_pret")
                    _RECEPTEURS.append(recepteur)
                    shell.sig_prompt_ready.connect(recepteur)

                _RECEPTEURS.append(nouveau_shell)
                console.sig_shellwidget_created.connect(nouveau_shell)
            shell = console.get_current_shellwidget()
            if shell is not None and "shell" not in _SUIVIS:
                _SUIVIS.add("shell")
                recepteur = _jalon("noyau_pret")
                _RECEPTEURS.append(recepteur)
                shell.sig_prompt_ready.connect(recepteur)
        completions = fenetre.get_plugin("completions", error=False)
        if completions is not None and "lsp" not in _SUIVIS:
            _SUIVIS.add("lsp")
            recepteur = _jalon("lsp_pret")
            _RECEPTEURS.append(recepteur)
            completions.sig_language_completions_available.connect(recepteur)
    except Exception:
        import traceback
        traceback.print_exc()


def demarrer():
    """Point d'entree : appele au debut de spyder.app.start.main()."""
    global _CHEMIN, _DEBUT_PROCESSUS
    chemin = os.environ.get("SMARTOS_STARTUP_TRACE")
    if not chemin or _CHEMIN is not None:
        return
    _DEBUT_PROCESSUS = _debut_processus()
    _CHEMIN = chemin
    repere("main_entree")
    contexte = _ETAT["contexte"]
    contexte.update({
        "pid": os.getpid(),
        "argv": sys.argv[1:],
        "charge_debut": _charge(),
        "modules_a_l_entree": len(sys.modules),
        "plateforme_qt": os.environ.get("QT_QPA_PLATFORM", ""),
    })
    t0 = os.environ.get("SMARTOS_T0")
    if t0:
        try:
            ecart_epoch = time.time() - time.clock_gettime(time.CLOCK_BOOTTIME)
            contexte["lanceur_ms"] = round(
                (_DEBUT_PROCESSUS + ecart_epoch - float(t0)) * 1000, 1)
        except ValueError:
            pass
    sys.meta_path.insert(0, _GuetteurMainwindow())
    atexit.register(ecrire)

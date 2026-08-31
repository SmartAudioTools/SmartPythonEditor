#!/usr/bin/env python3
"""Moteur de SCENARIOS pour Spyder : joue une suite d'actions au demarrage, sans clic.

Utilise par l'option "spyder --actions <fichier.json>" (Commun/scripts_installation/spyder_patch/patch_spyder_actions.py).

POURQUOI CE MODULE EXISTE, ET POURQUOI IL N'EST PAS DANS LE PATCH
-----------------------------------------------------------------
Sous Wayland, rien ne simule un clic ni une frappe : kdotool ne fait que du controle de
fenetre. Les options deja en place (--run-file, --profile-file, --gui-exec) declenchent
chacune UNE action, ce qui suffisait tant qu'un test tenait en une action. Des qu'il en faut
plusieurs - ouvrir un fichier, attendre que le noyau reponde, profiler, attendre l'artefact,
capturer, fermer - il faut SEQUENCER, donc attendre des conditions sans bloquer la boucle
d'evenements de Qt. C'est tout l'objet de ce module.

Il vit a cote de Spyder plutot que dans le patch parce qu'un patch de site-packages doit rester
minuscule : il est reapplique a chaque mise a jour du paquet, et plus il contient de code, plus
il a de chances de casser. Le patch se reduit donc a "importer ce module et l'appeler".

CE QUE LE MOTEUR GARANTIT
-------------------------
  - il ne bloque JAMAIS la boucle d'evenements : aucune attente n'est un sleep, tout est
    scrute par un QTimer. Un scenario qui attend un artefact laisse donc Spyder vivre, ce qui
    est la condition pour que l'artefact arrive ;
  - il ne peut PAS empecher Spyder de s'ouvrir : toute exception d'une action est capturee,
    consignee dans le rapport, et le scenario continue (ou s'arrete proprement) ;
  - le rapport est reecrit sur DISQUE apres chaque changement d'etat. Un scenario qui se fige
    laisse donc quand meme un rapport exploitable, avec l'action fautive en "en_cours" - c'est
    la difference entre un diagnostic et un silence.

FORMAT DU FICHIER D'ACTIONS (JSON)
----------------------------------
    {
      "rapport": "/chemin/rapport.json",      (optionnel : par defaut <actions>.rapport.json)
      "depart": 8,                            (optionnel : secondes avant la 1re action, def. 8)
      "arreter_si_echec": true,               (optionnel : def. false)
      "actions": [
        {"action": "ouvrir",   "fichier": "/chemin/demo.py"},
        {"action": "profiler", "fichier": "/chemin/demo.py"},
        {"action": "attendre", "fichier": "~/.config/spyder-py3/lineprofiler.results",
                               "plus_recent_que_depart": true, "delai": 180},
        {"action": "capture",  "fichier": "/DATA/Python/SmartOS/HGIGNORED/spyder.png"},
        {"action": "fermer"}
      ]
    }

Chaque action accepte "delai" (secondes avant abandon de CETTE action, defaut 60) et "nom"
(libelle libre repris dans le rapport).

VERBES
------
  ouvrir    {fichier}                  charge le fichier dans l'editeur.
  poser_marqueur {fichier, ligne}      pose un marqueur "profiler cette fonction" sur la
                                       fonction contenant `ligne`, comme un clic dans la
                                       marge du Line Profiler (snap sur la ligne du `def`,
                                       idempotent). Le fichier vise doit etre l'editeur
                                       COURANT : faire un "ouvrir" juste avant.
  profiler  {fichier}                  lance le profilage combine (Line Profiler) sur ce fichier.
  python    {code} ou {fichier}        execute du code dans le PROCESSUS GUI. Namespace :
                                       `main` (MainWindow), `app` (QApplication), `etat` (dict
                                       partage entre actions, recopie dans le rapport).
  attendre  {fichier[, plus_recent_que_depart]} | {expression}
                                       scrute jusqu'a ce que la condition soit vraie. Une
                                       expression est evaluee avec le meme namespace que
                                       "python" : c'est ce qui permet d'attendre un etat de
                                       l'interface et pas seulement un fichier.
  pause     {secondes}                 attente seche, en dernier recours.
  capture   {fichier[, widget]}        enregistre un PNG. Sans "widget", c'est la fenetre
                                       principale. Avec, c'est le widget du greffon nomme
                                       (ex. "spyder_line_profiler").
                                       ⚠ grab() rend le widget A SA TAILLE COURANTE : un
                                       panneau replie ou jamais affiche donne une vignette de
                                       quelques dizaines de pixels, pas une erreur. La taille
                                       est consignee dans le rapport, justement pour que ce cas
                                       se voie au lieu de passer pour une capture reussie.
  fermer    {}                         ferme Spyder.

⚠ POURQUOI LA CAPTURE SE FAIT ICI ET NON PAR spectacle. spectacle -a photographie la fenetre
ACTIVE a l'aveugle : s'il y a maldonne, on capture la Konsole, voire le terminal d'une autre
session - fuite de confidentialite deja rencontree sur ce depot. Un QWidget.grab() rend le
widget lui-meme : on ne peut pas se tromper de fenetre, et rien de ce qui est au-dessus n'est
photographie. C'est plus sur ET plus precis.

⚠ LES PIXELS D'UN grab() SONT PHYSIQUES, PAS LOGIQUES. Sur cet ecran l'echelle vaut 1,3 ou 1
selon la configuration : toute mesure prise sur l'image doit etre divisee par le
devicePixelRatio, consigne dans le rapport a cet effet.

⚠ "fermer" NE PEUT PAS ABOUTIR SI UN FICHIER EST NON SAUVEGARDE : Spyder ouvre une boite
modale "Enregistrer les modifications ?" et la fermeture reste en attente. Meme limite que
kdotool windowclose, deja documentee. Le rapport le signale par un echec de l'action avec son
delai depasse, plutot que par un blocage muet.
"""

import json
import os
import time
import traceback


INTERVALLE_MS = 200          # periode de scrutation
DELAI_ACTION_DEFAUT = 60     # secondes
DELAI_DEPART_DEFAUT = 8      # secondes avant la premiere action


class MoteurActions:
    """Joue les actions l'une apres l'autre, sans jamais bloquer la boucle d'evenements."""

    def __init__(self, main, app, chemin_actions):
        self.main = main
        self.app = app
        self.chemin_actions = chemin_actions
        self.etat = {}                  # partage entre actions, recopie dans le rapport
        self.debut = time.time()
        self.index = -1                 # -1 = pas encore demarre
        self.debut_action = None
        self.entrees = []               # une entree de rapport par action
        self.termine = False
        self.timer = None

        with open(chemin_actions, encoding="utf-8") as fichier:
            self.scenario = json.load(fichier)
        self.actions = self.scenario.get("actions", [])
        self.arreter_si_echec = bool(self.scenario.get("arreter_si_echec", False))
        self.chemin_rapport = self.scenario.get("rapport") or (chemin_actions + ".rapport.json")
        self.delai_depart = float(self.scenario.get("depart", DELAI_DEPART_DEFAUT))

    # ----- cycle de vie ---------------------------------------------------------------

    def demarrer(self):
        from qtpy.QtCore import QTimer
        self.timer = QTimer(self.main)
        self.timer.setInterval(INTERVALLE_MS)
        self.timer.timeout.connect(self._tic)
        self.debut_action = time.time() + self.delai_depart   # le "depart" est une attente
        self.ecrire_rapport()
        self.timer.start()

    def _tic(self):
        """Un pas. Ne leve jamais : une exception ici arreterait le timer sans rien dire."""
        try:
            self._pas()
        except Exception:
            self._consigner_echec("moteur", traceback.format_exc())
            self._terminer("erreur du moteur")

    def _pas(self):
        """Un tour de scrutation. ITERATIF, jamais recursif.

        Une premiere version enchainait les actions par recursion mutuelle (cloture -> demarrage
        de la suivante -> cloture immediate si l'action est instantanee...). Ca marche, mais la
        pile croit avec le nombre d'actions instantanees consecutives, pour rien : la boucle
        ci-dessous fait la meme chose a plat, et se lit d'un coup.
        """
        if self.termine:
            return
        # Phase d'amorcage : on laisse Spyder finir de s'ouvrir avant la premiere action.
        if self.index < 0:
            if time.time() < self.debut_action:
                return
            self.index = 0

        while not self.termine:
            if self.index >= len(self.actions):
                self._terminer("scenario termine")
                return

            # L'action courante n'a pas encore d'entree de rapport : c'est qu'elle demarre.
            if len(self.entrees) <= self.index:
                if not self._demarrer_action():
                    return          # action en cours, on rend la main jusqu'au prochain tic
                continue

            entree = self.entrees[self.index]
            if entree["etat"] != "en_cours":
                self.index += 1
                continue

            try:
                fini, message = self._poursuivre(self.actions[self.index], entree)
            except Exception:
                self._clore_action("echec", traceback.format_exc())
                continue
            if fini:
                self._clore_action("ok", message)
                continue
            if time.time() - entree["_depart"] > entree["delai"]:
                self._clore_action("delai_depasse", message or "condition jamais satisfaite")
                continue
            return              # toujours en cours : on attend le prochain tic

    def _demarrer_action(self):
        """Cree l'entree de rapport et lance l'action. Renvoie False si elle reste en cours."""
        action = self.actions[self.index]
        entree = {
            "n": self.index + 1,
            "action": action.get("action", "?"),
            "nom": action.get("nom", ""),
            "etat": "en_cours",
            "message": "",
            "duree_s": 0.0,
            "_depart": time.time(),
            "delai": float(action.get("delai", DELAI_ACTION_DEFAUT)),
        }
        self.entrees.append(entree)
        self.ecrire_rapport()
        try:
            fini, message = self._lancer(action, entree)
        except Exception:
            self._clore_action("echec", traceback.format_exc())
            return True
        if fini:
            self._clore_action("ok", message)
            return True
        return False

    def _clore_action(self, etat, message):
        """Ferme l'action courante. NE fait PAS avancer l'index : c'est la boucle qui avance."""
        entree = self.entrees[self.index]
        entree["etat"] = etat
        entree["message"] = message or ""
        entree["duree_s"] = round(time.time() - entree["_depart"], 2)
        self.ecrire_rapport()
        if etat != "ok" and self.arreter_si_echec:
            self._terminer("arret sur echec a l'action %d (%s)" % (self.index + 1, etat))

    def _consigner_echec(self, ou, message):
        self.entrees.append({"n": len(self.entrees) + 1, "action": ou, "nom": "",
                             "etat": "echec", "message": message, "duree_s": 0.0,
                             "_depart": time.time(), "delai": 0})

    def _terminer(self, raison):
        self.termine = True
        if self.timer is not None:
            self.timer.stop()
        self.raison_fin = raison
        self.ecrire_rapport()

    # ----- rapport --------------------------------------------------------------------

    def ecrire_rapport(self):
        """Ecrit le rapport sur disque, par fichier temporaire + rename atomique.

        Appele apres CHAQUE changement d'etat : c'est ce qui rend un scenario fige quand meme
        diagnosticable (l'action bloquante y est visible en "en_cours").
        """
        rapport = {
            "actions_fichier": self.chemin_actions,
            "debut_horodatage": self.debut,
            "duree_totale_s": round(time.time() - self.debut, 2),
            "termine": self.termine,
            "raison_fin": getattr(self, "raison_fin", ""),
            "device_pixel_ratio": self._ratio(),
            "etat": {cle: repr(valeur) for cle, valeur in self.etat.items()},
            "actions": [{cle: valeur for cle, valeur in entree.items()
                         if not cle.startswith("_")} for entree in self.entrees],
        }
        try:
            temporaire = self.chemin_rapport + ".tmp"
            with open(temporaire, "w", encoding="utf-8") as fichier:
                json.dump(rapport, fichier, indent=2, ensure_ascii=False)
            os.replace(temporaire, self.chemin_rapport)
        except OSError:
            pass    # un rapport qu'on ne peut pas ecrire ne doit pas casser le scenario

    def _ratio(self):
        try:
            return float(self.main.devicePixelRatioF())
        except Exception:
            return 0.0

    # ----- verbes ---------------------------------------------------------------------

    def _namespace(self):
        return {"main": self.main, "app": self.app, "etat": self.etat, "__name__": "__main__"}

    def _lancer(self, action, entree):
        """Demarre une action. Renvoie (fini, message)."""
        verbe = action.get("action")
        methode = getattr(self, "_verbe_" + str(verbe), None)
        if methode is None:
            raise ValueError("verbe inconnu : %r" % (verbe,))
        return methode(action, entree)

    def _poursuivre(self, action, entree):
        """Rappele a chaque tic tant qu'une action n'est pas finie."""
        verbe = action.get("action")
        methode = getattr(self, "_suite_" + str(verbe), None)
        if methode is None:
            return True, ""
        return methode(action, entree)

    def _verbe_ouvrir(self, action, entree):
        # Identifiant en clair plutot que `from spyder.api.plugins import Plugins` : la valeur
        # est une constante de chaine (Plugins.Editor == "editor", verifie dans le
        # api/plugins/enum.py installe), et s'en passer rend ce module importable - donc
        # testable - SANS Spyder. Le moteur n'a alors plus aucune dependance a l'application
        # qu'il pilote, ce qui est exactement ce qu'on veut d'un harnais de test.
        chemin = os.path.expanduser(action["fichier"])
        editeur = self.main.get_plugin("editor", error=False)
        if editeur is None:
            raise RuntimeError("greffon 'editor' absent")
        editeur.load(chemin)
        return True, chemin

    def _verbe_poser_marqueur(self, action, entree):
        """Pose un marqueur de profilage, comme le clic dans la marge que ce verbe remplace.

        Passe par `ProfileTargetsManager.toggle_target()` (profile_targets.py), le meme code
        qu'un clic reel : la resolution ligne -> `def`, la persistance CONF et le repaint de
        la marge sont donc exactement ceux du chemin utilisateur, pas une ecriture directe de
        la configuration qui les court-circuiterait.

        Exige que `fichier` soit l'editeur COURANT (get_current_editor()) : c'est ce que
        Spyder retient apres un "ouvrir", et c'est la seule facon d'atteindre le
        ProfileTargetsManager reellement branche sur CET editeur (il vit sur l'instance
        CodeEditor, pas sur le plugin).
        """
        chemin = os.path.expanduser(action["fichier"])
        ligne = int(action["ligne"])
        editeur = self.main.get_plugin("editor", error=False)
        if editeur is None:
            raise RuntimeError("greffon 'editor' absent")
        codeeditor = editeur.get_current_editor()
        if codeeditor is None:
            raise RuntimeError("aucun editeur courant (poser d'abord un verbe 'ouvrir')")
        cible_norm = os.path.normcase(os.path.abspath(chemin))
        courant_norm = os.path.normcase(os.path.abspath(str(codeeditor.filename)))
        if courant_norm != cible_norm:
            raise RuntimeError(
                "editeur courant sur %r, pas sur %r (poser d'abord un verbe 'ouvrir' sur ce "
                "fichier)" % (codeeditor.filename, chemin))
        manager = getattr(codeeditor, "profile_targets_manager", None)
        if manager is None:
            raise RuntimeError(
                "pas de gestionnaire de marqueurs sur cet editeur (fichier non Python ?)")
        def_line = manager.def_line_for(ligne)
        cible = def_line if def_line is not None else ligne
        if cible not in manager.get_targets():
            manager.toggle_target(ligne)
        return True, "marqueur pose ligne %d (def ligne %d)" % (ligne, cible)

    def _verbe_profiler(self, action, entree):
        chemin = os.path.expanduser(action["fichier"])
        lp = self.main.get_plugin("spyder_line_profiler", error=False)
        if lp is None:
            raise RuntimeError("greffon spyder_line_profiler absent")
        lp.get_widget().analyze(chemin, wdir=os.path.dirname(chemin))
        return True, chemin

    def _verbe_python(self, action, entree):
        if "fichier" in action:
            chemin = os.path.expanduser(action["fichier"])
            with open(chemin, encoding="utf-8") as fichier:
                code = fichier.read()
        else:
            code = action["code"]
            chemin = "<actions:%d>" % (self.index + 1)
        exec(compile(code, chemin, "exec"), self._namespace())
        return True, chemin

    def _verbe_pause(self, action, entree):
        entree["delai"] = float(action.get("secondes", 1)) + 5
        entree["_fin_pause"] = time.time() + float(action.get("secondes", 1))
        return False, ""

    def _suite_pause(self, action, entree):
        return time.time() >= entree["_fin_pause"], ""

    def _verbe_attendre(self, action, entree):
        # POINT DE REFERENCE DE "plus_recent_que_depart" : le debut de l'action PRECEDENTE.
        #
        # Ni l'instant de cette attente-ci (l'artefact peut etre ecrit pendant que l'action
        # declenchante se termine, donc avant que l'attente commence : on manquerait un vrai
        # succes), ni le depart du SCENARIO - c'etait la premiere version, et elle etait trop
        # laxiste : entre le depart et l'attente il y a la temporisation d'amorcage, plusieurs
        # secondes pendant lesquelles Spyder ecrit lui-meme quantite de fichiers. Un artefact
        # d'un run PRECEDENT reecrit au demarrage aurait donc valide l'attente sans que rien
        # n'ait tourne - le faux positif exact que cette option existe pour eviter.
        # Le debut de l'action precedente est le seul instant qui encadre juste : il est
        # forcement anterieur a l'ecriture cherchee, et posterieur a tout le bruit d'amorcage.
        entree["_reference"] = self.debut
        if self.index > 0:
            entree["_reference"] = self.entrees[self.index - 1]["_depart"]
        return self._suite_attendre(action, entree)

    def _suite_attendre(self, action, entree):
        if "expression" in action:
            try:
                valeur = eval(action["expression"], self._namespace())
            except Exception as erreur:
                return False, "expression pas encore evaluable : %s" % erreur
            return bool(valeur), "" if valeur else "expression fausse"
        chemin = os.path.expanduser(action["fichier"])
        if not os.path.exists(chemin):
            return False, "fichier absent"
        if action.get("plus_recent_que_depart"):
            if os.path.getmtime(chemin) < entree["_reference"]:
                return False, "fichier present mais anterieur au depart du scenario"
        return True, chemin

    def _verbe_capture(self, action, entree):
        chemin = os.path.expanduser(action["fichier"])
        nom_greffon = action.get("widget")
        if nom_greffon:
            greffon = self.main.get_plugin(nom_greffon, error=False)
            if greffon is None:
                raise RuntimeError("greffon %r absent" % (nom_greffon,))
            cible = greffon.get_widget()
        else:
            cible = self.main
        dossier = os.path.dirname(chemin)
        if dossier:
            os.makedirs(dossier, exist_ok=True)
        image = cible.grab()
        if not image.save(chemin, "PNG"):
            raise RuntimeError("echec de l'enregistrement de %s" % chemin)
        return True, "%s (%dx%d pixels physiques)" % (chemin, image.width(), image.height())

    def _verbe_fermer(self, action, entree):
        """Demande la fermeture, et s'accroche a la SORTIE DE L'APPLICATION.

        ⚠ POURQUOI PAS SEULEMENT LA SCRUTATION. Premiere version : on demandait la fermeture et
        on attendait qu'un tic suivant constate isVisible() == False. Ca a marche au premier
        lancement reel... et pas au second, sur exactement le meme scenario. C'est une COURSE :
        apres main.close(), Qt sort de sa boucle d'evenements et le processus meurt - souvent
        avant le tic suivant, qui n'arrive donc jamais. Le rapport restait alors fige sur
        "fermer / en_cours" alors que tout s'etait bien passe, et le verrou d'instance unique
        n'etait pas nettoye. Un test qui reussit une fois sur deux sans que rien ne change est
        un test qu'on finit par croire, ce qui est le pire des cas.

        aboutToQuit est emis PENDANT la sortie, avant la fin de la boucle : c'est le dernier
        instant ou l'on peut encore ecrire. La scrutation reste en place pour l'autre cas, celui
        ou la fermeture N'ABOUTIT PAS (fichier non sauvegarde -> boite modale) : la, aboutToQuit
        ne sera jamais emis, et c'est le delai de l'action qui doit parler.
        """
        try:
            self.app.aboutToQuit.connect(self._finaliser_fermeture)
        except Exception:
            pass    # pas de signal (test hors Qt) : la scrutation ci-dessous suffira
        self.main.close()
        return False, "fermeture demandee"

    def _finaliser_fermeture(self):
        if self.termine:
            return
        message = self._nettoyer_verrou()
        if self.index < len(self.entrees):
            entree = self.entrees[self.index]
            entree["etat"] = "ok"
            entree["message"] = message
            entree["duree_s"] = round(time.time() - entree["_depart"], 2)
        self._terminer("Spyder ferme")

    def _suite_fermer(self, action, entree):
        if not self.main.isVisible():
            message = self._nettoyer_verrou()
            self._terminer("Spyder ferme")
            return True, message
        return False, "fenetre encore visible (fichier non sauvegarde ?)"

    def _nettoyer_verrou(self):
        """Retire le verrou d'instance unique s'il pointe vers NOTRE processus.

        Constate le 26/07/2026 sur le premier scenario reel : apres main.close(), le processus
        sort bien (code 0) mais spyder.lock RESTE. Or ce verrou est un lien symbolique vers un
        PID : au lancement suivant, Spyder le croit vivant, lui transmet les fichiers et
        RESSORT AUSSITOT, code 0 et journal vide. Un scenario suivant ne demarrerait donc
        jamais, sans le moindre message - le piege est deja documente dans le CLAUDE.md du
        depot, ou il coute une purge manuelle a chaque fois.

        On ne supprime QUE si le lien pointe vers notre propre PID : un verrou appartenant a une
        autre instance (une session de l'utilisateur, une autre session Claude) ne nous regarde
        pas, et l'effacer a l'aveugle autoriserait deux Spyder concurrents sur la meme
        configuration - donc une configuration ecrasee, ce qui est pire que le probleme.
        """
        try:
            from spyder.config.base import get_conf_path
            verrou = get_conf_path("spyder.lock")
        except Exception:
            verrou = os.path.expanduser("~/.config/spyder-py3/spyder.lock")
        try:
            if os.readlink(verrou) != str(os.getpid()):
                return "verrou laisse en place (il appartient a un autre processus)"
        except OSError:
            return "aucun verrou a retirer"
        try:
            os.unlink(verrou)
        except OSError as erreur:
            return "verrou non supprimable : %s" % erreur
        return "verrou de notre propre instance retire"


def demarrer(main, app, chemin_actions):
    """Point d'entree appele par le patch. Ne leve jamais."""
    try:
        moteur = MoteurActions(main, app, chemin_actions)
    except Exception:
        traceback.print_exc()
        return None
    # Reference gardee sur la fenetre : sans cela le moteur (et son QTimer) serait ramasse par
    # le garbage collector des la sortie de cette fonction, et le scenario ne demarrerait pas.
    main._smartos_moteur_actions = moteur
    moteur.demarrer()
    return moteur

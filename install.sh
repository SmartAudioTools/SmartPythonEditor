#!/usr/bin/env bash
# Installeur autonome de SmartPythonEditor (fork SmartOS de Spyder + pile de greffons).
#
# Ce script est livre A LA RACINE du depot SmartPythonEditor : il n'exige RIEN d'autre que
# ce depot (clone) et un Python de la bonne version - pas d'acces au depot SmartOS, pas de
# pyenv impose, pas de KDE requis. Tout passe par pip : l'editeur (ce depot), puis chaque
# greffon (un depot GitHub par greffon, installables a la carte).
#
# Usage :
#     ./install.sh [options]
#
#   --python CHEMIN     interpreteur EXISTANT a utiliser (typiquement le python d'un venv
#                       pyenv deja cree). Par defaut : un venv est cree dans PREFIX/venv.
#   --prefix DOSSIER    racine de l'installation (venv, config, lanceur).
#                       Defaut : ~/.local/smartpythoneditor
#   --plugins LISTE     greffons a installer : "all" (defaut), "none", ou une liste
#                       separee par des virgules (ex. spyder_collab,spyder_pyxel).
#                       Catalogue : smartos-support/plugins-catalogue.txt
#   --local-plugins DIR installer les greffons depuis des clones locaux (DIR/<nom>) au
#                       lieu de GitHub - utile avant publication, ou hors ligne.
#
# Ce que ce script ne fait JAMAIS : toucher a la configuration KDE (kwinrulesrc,
# kservicemenurc...), installer des paquets systeme, ecrire hors de PREFIX et du dossier de
# configuration. Les recommandations correspondantes sont AFFICHEES en fin d'installation.
set -euo pipefail

ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT="$ICI/smartos-support"

PREFIX="$HOME/.local/smartpythoneditor"
PYTHON=""
PLUGINS="all"
LOCAL_PLUGINS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --python)        PYTHON="$2"; shift 2 ;;
        --prefix)        PREFIX="$2"; shift 2 ;;
        --plugins)       PLUGINS="$2"; shift 2 ;;
        --local-plugins) LOCAL_PLUGINS="$2"; shift 2 ;;
        --help|-h)       sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Option inconnue : $1 (essayer --help)" >&2; exit 2 ;;
    esac
done

PY_REQUISE="$(cat "$SUPPORT/python-version.txt")"

# ----------------------------------------------------------------- interpreteur
if [ -n "$PYTHON" ]; then
    [ -x "$PYTHON" ] || { echo "ECHEC : interpreteur introuvable : $PYTHON" >&2; exit 1; }
    VERSION_REELLE="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if [ "$VERSION_REELLE" != "$PY_REQUISE" ]; then
        echo "ECHEC : ce SmartPythonEditor exige Python $PY_REQUISE (les versions du" >&2
        echo "        requirements sont gelees pour lui) ; $PYTHON est en $VERSION_REELLE." >&2
        exit 1
    fi
    VENV_PY="$PYTHON"
else
    BASE_PY="$(command -v "python$PY_REQUISE" || true)"
    if [ -z "$BASE_PY" ]; then
        echo "ECHEC : aucun 'python$PY_REQUISE' dans le PATH. Installer Python $PY_REQUISE" >&2
        echo "        (paquet systeme, ou pyenv install $PY_REQUISE puis --python <chemin>)." >&2
        exit 1
    fi
    mkdir -p "$PREFIX"
    if [ ! -x "$PREFIX/venv/bin/python" ]; then
        echo "== Creation du venv ($BASE_PY) dans $PREFIX/venv"
        "$BASE_PY" -m venv "$PREFIX/venv"
    fi
    VENV_PY="$PREFIX/venv/bin/python"
fi
echo "Interpreteur : $VENV_PY"

# ----------------------------------------------------------------- editeur (pip)
echo "== Installation de SmartPythonEditor et de sa pile figee (pip)"
"$VENV_PY" -m pip install --upgrade pip >/dev/null
( cd "$ICI" && "$VENV_PY" -m pip install -r smartos-requirements.txt )

# ----------------------------------------------------------------- binding Qt
# Rien a faire (08/08/2026) : le binding est porte par les REQUIREMENTS et par le setup.py
# du fork (PySide6 dans la plage acceptee par check_qt(), PyQt6 sur aarch64 - marqueurs
# d'environnement). L'ancien cycle desinstallation/reinstallation n'a plus d'objet : plus
# rien ne tire PyQt5.

# ----------------------------------------------------------------- greffons
installer_greffon() {  # installer_greffon <nom> <url>
    if [ -n "$LOCAL_PLUGINS" ]; then
        "$VENV_PY" -m pip install --quiet "$LOCAL_PLUGINS/$1"
    else
        "$VENV_PY" -m pip install --quiet "git+$2"
    fi
    echo "   + $1"
}
if [ "$PLUGINS" != "none" ]; then
    echo "== Greffons ($PLUGINS)"
    while read -r NOM URL; do
        [ -z "$NOM" ] && continue
        case ",$PLUGINS," in
            ",all,"|*",$NOM,"*) installer_greffon "$NOM" "$URL" ;;
        esac
    done < "$SUPPORT/plugins-catalogue.txt"
fi

# ----------------------------------------------------------------- patchs tiers
# Un correctif vise un paquet TIERS que ni le fork de l'editeur ni un greffon ne peuvent
# porter : spyder-kernels (le traceback des interruptions de profilage). Idempotent, echec
# bruyant si son point d'ancrage a disparu. Les correctifs du line-profiler, eux, vivent
# en dur dans le greffon spyder_line_profiler du catalogue (fork du greffon officiel).
SITE="$("$VENV_PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"

# Module du moteur de scenarios (option "spyder --actions", ajoutee par les correctifs du
# fork) : il vit A COTE de spyder dans site-packages, pas dedans - "pip install ." ne
# l'installe donc pas, on le depose ici (meme geste que l'installation SmartOS).
cp -f "$ICI/smartos_spyder_actions.py" "$SITE/smartos_spyder_actions.py"

echo "== Patch du paquet tiers spyder-kernels"
"$VENV_PY" "$SUPPORT/patchs-tiers/patch_spyder_kernels_profile_interrupt.py" \
    "$SITE/spyder_kernels/customize/code_runner.py"

# ----------------------------------------------------------------- configuration
# Dossier de configuration DEDIE (SPYDER_CONFDIR) : ne partage rien avec un eventuel
# Spyder deja installe sur la machine (~/.config/spyder-py3), et s'efface d'un rm -rf.
CONFDIR="$PREFIX/config"
mkdir -p "$CONFDIR/config"
if [ ! -f "$CONFDIR/config/spyder.ini" ]; then
    echo "== Configuration initiale ($CONFDIR)"
    cp "$SUPPORT/config-reference/spyder.ini" "$SUPPORT/config-reference/transient.ini" \
        "$CONFDIR/config/"
    bash "$SUPPORT/substituer_home.sh" "$CONFDIR/config"
else
    echo "== Configuration existante conservee ($CONFDIR)"
fi

# CONF_VERSION du Spyder reellement installe : sans cet estampillage, la migration de
# configuration de Spyder ecrase silencieusement les reglages a la premiere ouverture
# (_update_defaults remet au defaut toute option dont le defaut a change).
CONF_VERSION="$(sed -nE "s/^CONF_VERSION = '([^']+)'.*/\1/p" "$SITE/spyder/config/main.py")"
[ -n "$CONF_VERSION" ] || { echo "ECHEC : CONF_VERSION introuvable dans $SITE/spyder/config/main.py" >&2; exit 1; }
CSS_PATH="$(ls -d "$SITE"/spyder/plugins/help/utils/static/*dark_css 2>/dev/null | head -1)"

"$VENV_PY" - "$CONFDIR/config" "$CONF_VERSION" "$CSS_PATH" "$VENV_PY" <<'PYEOF'
# Reglages dependant de l'environnement installe, poses ligne a ligne (jamais configparser :
# il reordonnerait les sections et reformaterait des valeurs de plusieurs milliers de
# caracteres). Idempotent : rejouer l'installation reecrit les memes valeurs.
import re
import sys

confdir, conf_version, css_path, venv_py = sys.argv[1:5]


def poser(fichier, reglages):
    """reglages : {(section|None, cle): valeur} - echoue si une cle manque."""
    chemin = f"{confdir}/{fichier}"
    restantes = dict(reglages)
    section = None
    lignes = []
    with open(chemin, encoding="utf-8") as flux:
        for ligne in flux:
            m = re.match(r"\[(.+)\]\s*$", ligne)
            if m:
                section = m.group(1)
            else:
                m = re.match(r"([^=\s][^=]*?)\s*=", ligne)
                if m:
                    cle = m.group(1)
                    for (sec, nom) in list(restantes):
                        if nom == cle and sec in (None, section):
                            ligne = f"{cle} = {restantes.pop((sec, nom))}\n"
                            break
            lignes.append(ligne)
    if restantes:
        raise SystemExit(f"Cle(s) introuvable(s) dans {chemin} : {sorted(restantes)}")
    with open(chemin, "w", encoding="utf-8") as flux:
        flux.writelines(lignes)


poser("spyder.ini", {
    ("main", "version"): conf_version,
    (None, "css_path"): css_path,
})
poser("transient.ini", {
    ("main", "version"): conf_version,
    ("main_interpreter", "custom_interpreters_list"): f"['{venv_py}']",
    ("main_interpreter", "custom_interpreter"): venv_py,
    ("main_interpreter", "executable"): venv_py,
    ("main_interpreter", "last_envs"): "{}",
})
print("   spyder.ini / transient.ini alignes sur l'environnement installe")
PYEOF

# ----------------------------------------------------------------- lanceur
mkdir -p "$PREFIX/bin"
LANCEUR="$PREFIX/bin/smartpythoneditor"
cat > "$LANCEUR" <<LANCEOF
#!/usr/bin/env bash
# Lanceur SmartPythonEditor - genere par install.sh, regenere a chaque installation.
export SPYDER_CONFDIR="$CONFDIR"
# QT_API : le binding reellement installe fait foi (qtpy choisirait pyqt5 par defaut).
if "$VENV_PY" -c 'import PySide6' 2>/dev/null; then
    export QT_API=pyside6
else
    export QT_API=pyqt6
fi
exec "$VENV_PY" -m spyder.app.start "\$@"
LANCEOF
chmod 755 "$LANCEUR"

echo ""
echo "== Installation terminee =="
echo "Lancer :  $LANCEUR"
echo ""
echo "Recommandations (rien n'a ete modifie automatiquement) :"
echo "  - KDE Wayland : installer 'kdotool' pour que la fenetre deja ouverte remonte au"
echo "    premier plan quand un fichier lui est transmis (sinon repli silencieux)."
echo "  - Terminal integre / panneau Claude : compiler le binding QTermWidget -"
echo "    voir qtermwidget_binding/build.sh dans le depot du greffon spyder_konsole."
echo "  - KDE : le greffon window_controls pose LUI-MEME la regle KWin 'sans cadre' et le"
echo "    schema de couleurs de la barre de titre au premier demarrage (option integration_kde"
echo "    pour le desactiver)."

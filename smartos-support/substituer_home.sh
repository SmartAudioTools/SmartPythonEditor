#!/bin/bash
# Remplace le marqueur __HOME__ par un vrai chemin de dossier personnel, dans des fichiers qui
# viennent d'etre DEPLOYES (jamais dans les sources du depot : elles doivent garder le marqueur).
#
# Raison d'etre : sortir le nom de compte fige des scripts a laisse une famille d'occurrences que
# rien ne peut developper a l'execution - les fichiers de DONNEES copies verbatim (.desktop, .ini,
# .xbel, .directory, .xml) et les unites systemd SYSTEME. Les deux autres familles, elles, ont leur
# solution native et n'ont pas besoin de ce script :
#   - code shell            -> $HOME, developpe par le shell ;
#   - unite systemd UTILISATEUR -> %h, developpe par systemd (invalide en unite systeme : /root).
#
# Usage : substituer_home.sh [--home=/chemin] FICHIER_OU_DOSSIER...
#   --home  dossier a ecrire a la place du marqueur. Defaut : le $HOME de l'appelant. C'est ce
#           parametre qui permet de servir un AUTRE compte que celui qui lance - root deployant
#           dans /etc/systemd/system, ou le compte isole claude-agent.
#
# Idempotent : une fois substitue, le marqueur n'est plus la, un second passage ne fait rien.

set -u

MAISON="$HOME"
CIBLES=()
for arg in "$@"; do
    case "$arg" in
        --home=*) MAISON="${arg#--home=}" ;;
        *)        CIBLES+=("$arg") ;;
    esac
done

if [ ${#CIBLES[@]} -eq 0 ]; then
    echo "usage: $(basename "$0") [--home=/chemin] FICHIER_OU_DOSSIER..." >&2
    exit 1
fi

# Delimiteur # laisse tel quel, sans echappement : un nom de connexion Linux ne peut contenir ni /
# ni #, donc aucun chemin de dossier personnel ne peut casser l'expression sed.
n=0
while IFS= read -r -d '' fichier; do
    sed -i "s#__HOME__#$MAISON#g" "$fichier"
    n=$((n + 1))
done < <(grep -rlZ --binary-files=without-match -- '__HOME__' "${CIBLES[@]}" 2>/dev/null)

echo "  __HOME__ -> $MAISON : $n fichier(s)"

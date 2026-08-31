#!/usr/bin/env python3
"""Fait REMONTER le KeyboardInterrupt apres le message "Profiling was interrupted".

CE QUE CE PATCH FAIT
--------------------
Modifie spyder_kernels/customize/code_runner.py, fonction profile_with_context() :
son except KeyboardInterrupt affiche un message convivial puis AVALE l'exception -
contrairement au F5 normal (%runfile), qui laisse le KeyboardInterrupt remonter et
affiche son traceback. Demande de l'utilisateur (TODO - Spyder - Pyxel.txt, 02/08/2026,
pendant le diagnostic du bouton Stop) : garder le message ET voir le traceback, comme F5.

POURQUOI CETTE FORME
--------------------
Un seul `raise` ajoute a la fin du bloc except, qui relance l'exception EN COURS (pas de
nouvelle exception, meme traceback). cProfile.runctx() a deja ecrit ses statistiques
PARTIELLES dans son propre `finally` avant que l'exception ne remonte jusqu'ici - rien a
changer de ce cote, le patch ne fait que RELANCER apres coup.

CE FICHIER EXISTE DANS PLUSIEURS ENVIRONNEMENTS SUR CETTE MACHINE
-------------------------------------------------------------------
spyder_kernels tourne dans l'interpreteur du PROJET (main_interpreter), pas forcement
celui de Spyder lui-meme (verifie le 01/08/2026 pour le pont Pyxel, meme situation ici) :
chaque venv SmartPython-*_2026-07-26 a sa PROPRE copie installee par pip, en plus de celle
du venv Spyder. Le script d'installation appelant celui-ci doit donc BOUCLER sur toutes
les copies trouvees, pas patcher une seule fois.

Localisation par le module ast (nom de fonction), echec BRUYANT si le point d'ancrage
manque, idempotent (marqueur _smartos_profile_keyboardinterrupt).

Usage : patch_spyder_kernels_profile_interrupt.py <code_runner.py>
"""

import ast
import sys

MARKER = "_smartos_profile_keyboardinterrupt"

PATCH_LINE = (
    "        # Ajout SmartOS ({marker}) : garder le KeyboardInterrupt visible (comme F5),\n"
    "        # en plus du message convivial. Cf.\n"
    "        # Commun/scripts/patch_spyder_kernels_profile_interrupt.py\n"
    "        raise\n"
).format(marker=MARKER)


def find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def find_except_keyboardinterrupt(function_node):
    for node in ast.walk(function_node):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                target = handler.type
                # `except KeyboardInterrupt:` -> handler.type est un ast.Name('KeyboardInterrupt')
                if isinstance(target, ast.Name) and target.id == "KeyboardInterrupt":
                    return handler
    return None


def patch(path):
    with open(path, encoding="utf-8") as stream:
        source = stream.read()

    if MARKER in source:
        print(f"Deja patche : {path}")
        return True

    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        print(f"ERREUR : {path} illisible ({error})", file=sys.stderr)
        return False

    function = find_function(tree, "profile_with_context")
    if function is None:
        print(f"ERREUR : profile_with_context introuvable dans {path} (structure changee ?)",
              file=sys.stderr)
        return False

    handler = find_except_keyboardinterrupt(function)
    if handler is None or not handler.body:
        print(f"ERREUR : 'except KeyboardInterrupt' introuvable dans profile_with_context "
              f"de {path} (structure changee ?)", file=sys.stderr)
        return False

    # Insere juste apres la DERNIERE instruction du bloc except (le print convivial) :
    # end_lineno est disponible depuis Python 3.8, toujours vrai ici.
    last_statement = handler.body[-1]
    insert_line = last_statement.end_lineno

    lines = source.splitlines(keepends=True)
    lines.insert(insert_line, PATCH_LINE)
    patched = "".join(lines)

    try:
        ast.parse(patched)
    except SyntaxError as error:
        print(f"ERREUR : patch invalide pour {path} ({error})", file=sys.stderr)
        return False

    with open(path, "w", encoding="utf-8") as stream:
        stream.write(patched)
    print(f"Patche : {path} (KeyboardInterrupt visible apres le profilage)")
    return True


def main(argv):
    if len(argv) != 2:
        print(f"Usage : {argv[0]} <code_runner.py>", file=sys.stderr)
        return 1
    return 0 if patch(argv[1]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

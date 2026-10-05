# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""Module checking Spyder installation requirements"""

# Third-party imports
from packaging.version import parse


def show_warning(message):
    """Show warning using Tkinter if available"""
    try:
        # If tkinter is installed (highly probable), show an error pop-up.
        # From https://stackoverflow.com/a/17280890/438386
        import tkinter as tk
        root = tk.Tk()
        root.title("Spyder")
        label = tk.Label(root, text=message, justify='left')
        label.pack(side="top", fill="both", expand=True, padx=20, pady=20)
        button = tk.Button(root, text="OK", command=root.destroy)
        button.pack(side="bottom", fill="none", expand=True)
        root.mainloop()
    except Exception:
        pass

    raise RuntimeError(message)


def check_qt():
    """Check Qt binding requirements"""
    qt_infos = dict(
        pyqt5=("PyQt5", ("5.15.0", "5.16")),
        pyside2=("PySide2", ("5.15.0", "5.16")),
        pyqt6=("PyQt6", ("6.9.0", "7.0.0")),
        pyside6=("PySide6", ("6.8.0", "6.9.0")),
    )

    try:
        import qtpy
        package_name, required_ver = qt_infos[qtpy.API]

        # Ajout SmartOS (_smartos_qt_version_override) : permet d'outrepasser la plage de
        # version figee en dur ci-dessus via des variables d'environnement, sans avoir a
        # repatcher ce fichier a chaque nouvelle version de binding Qt testee. Cf.
        # Commun/scripts/patch_spyder_qt_version_override.py
        import os as _smartos_os
        if _smartos_os.environ.get("SPYDER_QT_SKIP_VERSION_CHECK") == "1":
            return
        _smartos_min = _smartos_os.environ.get("SPYDER_QT_MIN_VERSION")
        _smartos_max = _smartos_os.environ.get("SPYDER_QT_MAX_VERSION")
        if _smartos_min or _smartos_max:
            required_ver = (
                _smartos_min or required_ver[0],
                _smartos_max or required_ver[1],
            )
        actual_ver = qtpy.QT_VERSION

        if actual_ver is None or not (
            parse(required_ver[0])
            <= parse(actual_ver)
            < parse(required_ver[1])
        ):
            show_warning(
                (
                    "Please check Spyder installation requirements:\n\n"
                    "{} >={},<{} is required but version {} was found."
                ).format(
                    package_name, required_ver[0], required_ver[1], actual_ver
                )
            )
    except Exception as e:
        show_warning("Failed to import qtpy.\n\nThe error was {}".format(e))

# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
This module contains the edge line panel
"""

# Third party imports
from qtpy.QtCore import Qt
from qtpy.QtGui import QPainter, QColor, QPen

# Local imports
from spyder.plugins.editor.api.panel import Panel


class EdgeLine(Panel):
    """Source code editor's edge line (default: 79 columns, PEP8)"""

    # --- Qt Overrides
    # -----------------------------------------------------------------
    def __init__(self):
        Panel.__init__(self)
        self.columns = (79,)
        self.color = Qt.darkGray

    def paintEvent(self, event):
        """Override Qt method"""
        painter = QPainter(self)
        size = self.size()

        # Ligne de bord POINTILLEE, discrete et pixel-perfect (ajout SmartOS, cf.
        # Commun/scripts/patch_spyder_edgeline_dotted.py) : la distingue de la ligne PLEINE
        # de demarcation du greffon line-profiler entre le code et les temps.
        # Couleur = fond de l'editeur a peine eclairci (10 %), meme teinte discrete que le
        # trait de demarcation, pour la coherence.
        # ⚠ setCosmetic(True) : la largeur d'un stylo cosmetique est en pixels PHYSIQUES,
        # donc le trait fait toujours pile 1 px net quel que soit le facteur d'echelle KDE
        # (fractionnaire compris). Motif [6, 2].
        _base = self.editor.palette().base().color()
        color = QColor(round(_base.red() * 0.9 + 25.5),
                       round(_base.green() * 0.9 + 25.5),
                       round(_base.blue() * 0.9 + 25.5))
        pen = QPen(color)
        pen.setWidth(1)
        pen.setCosmetic(True)
        pen.setDashPattern([6, 2])
        painter.setPen(pen)

        for column in self.columns:
            # draw edge line at column n + 3 to account for line number margin
            x = self.editor.fontMetrics().width(column * '9') + 3
            painter.drawLine(x, 0, x, size.height())

    def sizeHint(self):
        """Override Qt method."""
        return self.size()

    # --- Other methods
    # -----------------------------------------------------------------

    def set_enabled(self, state):
        """Toggle edge line visibility"""
        self._enabled = state
        self.setVisible(state)

    def set_columns(self, columns):
        """Set edge line columns values."""
        if isinstance(columns, tuple):
            self.columns = columns
        elif columns:
            columns = str(columns)
            self.columns = tuple(int(e) for e in columns.split(','))

        self.update()

    def update_color(self):
        """
        Set edgeline color using syntax highlighter color for comments
        """
        self.color = self.editor.highlighter.get_color_name('comment')

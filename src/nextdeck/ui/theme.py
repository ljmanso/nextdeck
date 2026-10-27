"""Application-wide appearance."""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

#: The font Qt picked for us, captured before any override is applied.
_default_point_size: float | None = None


def apply_font_size(point_size: int) -> None:
    """Set the application font size; 0 restores the system default.

    Applied app-wide so every widget stays consistent. Cards are rebuilt on
    the next render, which is what picks up the new metrics.
    """
    global _default_point_size
    app = QApplication.instance()
    if app is None:
        return

    font = QFont(app.font())
    if _default_point_size is None:
        _default_point_size = font.pointSizeF()

    size = float(point_size) if point_size else _default_point_size
    if size and size > 0:
        font.setPointSizeF(size)
        app.setFont(font)

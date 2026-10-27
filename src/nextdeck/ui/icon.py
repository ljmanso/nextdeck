"""Application icon.

The SVG is the real asset, but loading it depends on Qt's SVG image plugin, so
there is a hand-painted fallback: a blank tray icon is worse than an ugly one.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

ICON_PATH = Path(__file__).resolve().parent.parent / "resources" / "nextdeck.svg"
BRAND = QColor("#0082c9")

_cached: QIcon | None = None


def app_icon() -> QIcon:
    global _cached
    if _cached is not None:
        return _cached

    icon = QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QIcon()
    if icon.isNull() or icon.availableSizes() == []:
        icon = _painted_icon()
    _cached = icon
    return icon


def _painted_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(BRAND)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = size * 0.18
        painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

        painter.setPen(QColor("white"))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(8, int(size * 0.62)))
        painter.setFont(font)
        painter.drawText(
            pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "N"
        )
        painter.end()
        icon.addPixmap(pixmap)
    return icon

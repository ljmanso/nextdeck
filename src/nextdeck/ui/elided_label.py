"""A label that wraps to a bounded number of lines and elides the overflow.

QLabel cannot do this. With word wrap on it will happily grow to any height,
and inside a fixed-width column that made long card titles either stretch the
card or get clipped with the first line pushed out of view. Laying the text out
with QTextLayout gives exact control: always start at the top, wrap up to
`max_lines`, and end the last visible line with an ellipsis when there is more.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontMetrics, QPainter, QTextLayout
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class ElidedLabel(QWidget):
    """Top-aligned, word-wrapped text limited to `max_lines`."""

    def __init__(
        self, text: str = "", max_lines: int = 2, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self._text = text
        self._max_lines = max(1, int(max_lines))
        self._elided = False
        self._line_count = 0
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    # -- content ----------------------------------------------------------

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._sync_height()
            self.update()

    def maxLines(self) -> int:
        return self._max_lines

    def setMaxLines(self, lines: int) -> None:
        lines = max(1, int(lines))
        if lines != self._max_lines:
            self._max_lines = lines
            self._sync_height()
            self.update()

    def isElided(self) -> bool:
        """True when the text did not fit; valid after a layout pass."""
        self._layout_lines(self.width())
        return self._elided

    def lineCount(self) -> int:
        """Lines actually used at the current width."""
        self._layout_lines(self.width())
        return self._line_count

    # -- layout -----------------------------------------------------------

    def _layout_lines(self, width: int) -> list[tuple[str, float]]:
        """Break the text into at most `max_lines` (text, y) pairs.

        Returns the lines to draw; sets `_elided` and `_line_count`.
        """
        metrics = QFontMetrics(self.font())
        self._elided = False
        self._line_count = 0
        if not self._text or width <= 0:
            return []

        spacing = metrics.lineSpacing()
        lines: list[tuple[str, float]] = []

        layout = QTextLayout(self._text, self.font())
        option = layout.textOption()
        option.setWrapMode(option.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)

        layout.beginLayout()
        y = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)

            start = line.textStart()
            length = line.textLength()
            is_last_allowed = len(lines) == self._max_lines - 1

            if is_last_allowed:
                # Anything after this line has to be folded into it, elided.
                remainder = self._text[start:]
                shown = metrics.elidedText(remainder, Qt.TextElideMode.ElideRight, width)
                lines.append((shown, y))
                self._elided = shown != remainder
                break

            lines.append((self._text[start : start + length], y))
            y += spacing
        layout.endLayout()

        self._line_count = len(lines)
        return lines

    # -- geometry ---------------------------------------------------------

    def heightForWidth(self, width: int) -> int:
        metrics = QFontMetrics(self.font())
        lines = self._layout_lines(width)
        return max(1, len(lines)) * metrics.lineSpacing()

    def sizeHint(self) -> QSize:
        width = self.width() or 200
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, QFontMetrics(self.font()).lineSpacing())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # A new width means a new line count. Qt's layouts honour
        # heightForWidth only patchily once nested two deep, which clipped the
        # last line of wrapped titles, so pin the height here instead.
        self._sync_height()

    def _sync_height(self) -> None:
        wanted = self.heightForWidth(self.width())
        if wanted != self.height() or self.minimumHeight() != wanted:
            self.setFixedHeight(wanted)
            self.updateGeometry()

    # -- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        metrics = QFontMetrics(self.font())
        ascent = metrics.ascent()
        for text, y in self._layout_lines(self.width()):
            painter.drawText(0, int(y + ascent), text)
        painter.end()


class WrappedLabel(QLabel):
    """A word-wrapping QLabel that keeps room for every line it wraps to.

    Qt computes a wrapped label's height from `heightForWidth`, which layouts
    honour only patchily once nested a couple of levels deep — the settings
    dialog's hint text came out ten pixels short and ran under the checkbox
    below it. Pinning the height here, the same way `ElidedLabel` does, is the
    fix that actually holds.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        super().setText(text)
        self._sync_height()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self) -> None:
        width = self.width()
        if width <= 0:
            return
        wanted = self.heightForWidth(width)
        if wanted > 0 and self.minimumHeight() != wanted:
            self.setMinimumHeight(wanted)
            self.updateGeometry()

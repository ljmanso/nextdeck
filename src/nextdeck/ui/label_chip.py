"""Label chips.

Deck gives every label a colour, which is most of what makes a label useful
at a glance, so the chips wear it. Two flavours: a read-only one for cards,
which filters the board when clicked, and a toggleable one for the editor.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..model import Label

#: Chips sit on a line of their own and must not push a card wider.
CHIP_PADDING = "1px 6px"


def _chip_style(selector: str, background: str, foreground: str, border: str) -> str:
    return f"""
        {selector} {{
            background: {background};
            color: {foreground};
            border: 1px solid {border};
            border-radius: 7px;
            padding: {CHIP_PADDING};
        }}
    """


class LabelChip(QPushButton):
    """One label on a card. Clicking it filters the board by that label."""

    clicked_label = Signal(str)

    def __init__(self, label: Label, parent: QWidget | None = None):
        super().__init__(label.title, parent)
        self.label = label
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setToolTip(f"Show everything labelled “{label.title}”")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            _chip_style(
                "QPushButton",
                label.css_color,
                "#ffffff" if label.is_dark else "#202020",
                label.css_color,
            )
        )
        self.clicked.connect(lambda: self.clicked_label.emit(label.title))


class LabelToggle(QPushButton):
    """A label in the editor, on or off for the card being edited."""

    def __init__(self, label: Label, parent: QWidget | None = None):
        super().__init__(label.title, parent)
        self.label = label
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        # Unchecked is an outline in the label's colour rather than a filled
        # chip, so "on" and "off" are obvious without reading the text.
        self.setStyleSheet(
            _chip_style("QPushButton", "transparent", "palette(text)", label.css_color)
            + _chip_style(
                "QPushButton:checked",
                label.css_color,
                "#ffffff" if label.is_dark else "#202020",
                label.css_color,
            )
        )


class LabelRow(QWidget):
    """A wrapping row of chips.

    Qt has no flow layout, and labels are short and few, so this lays them out
    by hand: a vertical stack of horizontal rows, wrapped at a width budget.
    """

    label_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rows = QVBoxLayout(self)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(3)
        self._chips: list[QPushButton] = []

    def chips(self) -> list[QPushButton]:
        return list(self._chips)

    def clear(self) -> None:
        self._chips.clear()
        while self._rows.count():
            item = self._rows.takeAt(0)
            inner = item.layout()
            if inner is None:
                continue
            while inner.count():
                child = inner.takeAt(0).widget()
                if child is not None:
                    child.hide()
                    child.deleteLater()
            inner.deleteLater()

    def add(self, chip: QPushButton, width_budget: int) -> None:
        """Place `chip`, starting a new row when this one is full."""
        chip.adjustSize()
        row = self._rows.itemAt(self._rows.count() - 1)
        layout = row.layout() if row is not None else None

        if layout is not None:
            used = sum(
                layout.itemAt(i).widget().width()
                for i in range(layout.count())
                if layout.itemAt(i).widget() is not None
            )
            if used + chip.width() > width_budget:
                layout = None

        if layout is None:
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(3)
            layout.addStretch(1)
            self._rows.addLayout(layout)

        layout.insertWidget(layout.count() - 1, chip)
        self._chips.append(chip)

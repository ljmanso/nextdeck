"""Managing the board's labels.

Deck labels are a title and a colour, defined per board. `LabelManagerWindow`
lists them and adds, edits or deletes them; `LabelDialog` is the small form
used for adding and editing one. The colour is most of what makes a label
readable at a glance, so the form offers a handful of distinct presets and
previews the chip as it will appear on a card.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .elided_label import WrappedLabel
from .label_chip import LabelChip
from ..model import Label

#: Deck's own default label colours, then a few more that stay distinct.
PRESET_COLORS = (
    "31CC7C",
    "317CCC",
    "FF7A66",
    "F1DB50",
    "7C31CC",
    "CC317C",
    "3A3B3D",
    "B0B6BD",
)


class LabelDialog(QDialog):
    """Collects a label's title and colour, for a new label or an existing one."""

    def __init__(
        self,
        existing: list[Label] | None = None,
        label: Label | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit label" if label else "New label")
        self.setMinimumWidth(360)
        #: Deck accepts duplicate titles, but two labels nobody can tell
        #: apart by name only make filtering by label ambiguous. A label being
        #: edited may of course keep its own title.
        self._taken = {
            other.title.casefold()
            for other in existing or []
            if label is None or other.id != label.id
        }
        self._color = (
            label.css_color.lstrip("#").upper() if label else PRESET_COLORS[0]
        )

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.title_edit = QLineEdit(label.title if label else "")
        self.title_edit.setPlaceholderText("e.g. urgent, errands, waiting")
        form.addRow("Title", self.title_edit)

        swatches = QHBoxLayout()
        swatches.setSpacing(4)
        self._swatches: list[QPushButton] = []
        for color in PRESET_COLORS:
            swatch = QPushButton()
            swatch.setCheckable(True)
            swatch.setFixedSize(22, 22)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.setToolTip(f"#{color}")
            swatch.setStyleSheet(
                f"QPushButton {{ background: #{color}; border: 1px solid #{color};"
                " border-radius: 11px; }"
                " QPushButton:checked { border: 2px solid palette(text); }"
            )
            swatch.clicked.connect(lambda _=False, c=color: self.set_color(c))
            swatches.addWidget(swatch)
            self._swatches.append(swatch)
        self.custom_button = QPushButton("Custom…")
        self.custom_button.clicked.connect(self._pick_custom)
        swatches.addWidget(self.custom_button)
        swatches.addStretch(1)
        form.addRow("Colour", swatches)

        self._preview_holder = QHBoxLayout()
        self._preview_holder.setContentsMargins(0, 0, 0, 0)
        self._preview_holder.addStretch(1)
        self._preview: LabelChip | None = None
        form.addRow("Preview", self._preview_holder)

        layout.addLayout(form)

        self._problem = WrappedLabel("")
        self._problem.setEnabled(False)
        self._problem.hide()
        layout.addWidget(self._problem)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setText("Save" if label else "Create")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.title_edit.textChanged.connect(self._refresh)
        self.set_color(self._color)
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def set_color(self, color: str) -> None:
        """Choose `color`, a RRGGBB string with or without a leading hash."""
        self._color = color.lstrip("#").upper()
        for swatch, preset in zip(self._swatches, PRESET_COLORS):
            swatch.setChecked(preset == self._color)
        self._refresh()

    def _pick_custom(self) -> None:
        chosen = QColorDialog.getColor(QColor(f"#{self._color}"), self, "Label colour")
        if chosen.isValid():
            self.set_color(chosen.name())

    def _refresh(self) -> None:
        title = self.title_edit.text().strip()
        duplicate = title.casefold() in self._taken
        self._ok_button.setEnabled(bool(title) and not duplicate)
        self._problem.setText(
            f"This board already has a label called “{title}”." if duplicate else ""
        )
        self._problem.setVisible(duplicate)

        if self._preview is not None:
            self._preview_holder.removeWidget(self._preview)
            self._preview.hide()
            self._preview.deleteLater()
        self._preview = LabelChip(Label(0, title or "label", self._color))
        # Inert rather than disabled: a disabled chip would grey its colour.
        self._preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._preview.setToolTip("")
        self._preview_holder.insertWidget(0, self._preview)

    def values(self) -> tuple[str, str]:
        """The title and the bare RRGGBB colour Deck stores."""
        return self.title_edit.text().strip(), self._color


def _swatch_icon(label: Label, size: int = 14) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(label.css_color))
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return QIcon(pixmap)


class LabelManagerWindow(QDialog):
    """Lists the board's labels and adds, edits or deletes them.

    Not modal, so the board stays usable while it is open. It never talks to
    the server itself: it asks through its signals, and the window feeds the
    reloaded label set back in through `set_labels` once Deck has answered.
    """

    #: (title, colour)
    create_requested = Signal(str, str)
    #: (label id, title, colour)
    update_requested = Signal(int, str, str)
    #: (label id, title)
    delete_requested = Signal(int, str)

    def __init__(self, board_title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(
            f"Labels — {board_title}" if board_title else "Labels"
        )
        self.setMinimumSize(340, 320)
        self._labels: list[Label] = []
        self._usage: dict[int, int] = {}

        layout = QVBoxLayout(self)

        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setIconSize(QSize(14, 14))
        self.list.itemDoubleClicked.connect(lambda _item: self.edit_selected())
        self.list.currentRowChanged.connect(lambda _row: self._update_buttons())
        body.addWidget(self.list, 1)

        side = QVBoxLayout()
        self.add_button = QPushButton("Add…")
        self.add_button.clicked.connect(self.add_label)
        side.addWidget(self.add_button)
        self.edit_button = QPushButton("Edit…")
        self.edit_button.clicked.connect(self.edit_selected)
        side.addWidget(self.edit_button)
        self.delete_button = QPushButton("Delete…")
        self.delete_button.clicked.connect(self.delete_selected)
        side.addWidget(self.delete_button)
        side.addStretch(1)
        body.addLayout(side)
        layout.addLayout(body, 1)

        self._empty = WrappedLabel("This board has no labels yet.")
        self._empty.setEnabled(False)
        layout.addWidget(self._empty)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        self.set_labels([])

    # -- contents ---------------------------------------------------------

    def set_labels(self, labels: list[Label], usage: dict[int, int] | None = None) -> None:
        """Show `labels`, keeping the selection on the same label if it survives.

        `usage` maps a label id to how many tasks wear it, so deleting one is
        not a guess about what it will touch.
        """
        current = self.selected_label()
        self._labels = list(labels)
        if usage is not None:
            self._usage = dict(usage)
        self.list.clear()
        for label in self._labels:
            item = QListWidgetItem(_swatch_icon(label), self._item_text(label))
            item.setData(Qt.ItemDataRole.UserRole, label.id)
            self.list.addItem(item)
            if current is not None and label.id == current.id:
                self.list.setCurrentItem(item)
        self._empty.setVisible(not self._labels)
        self._update_buttons()

    def set_usage(self, usage: dict[int, int]) -> None:
        self.set_labels(self._labels, usage)

    def _item_text(self, label: Label) -> str:
        count = self._usage.get(label.id, 0)
        if not count:
            return label.title
        return f"{label.title}  ·  {count} task{'s' if count != 1 else ''}"

    def selected_label(self) -> Label | None:
        item = self.list.currentItem()
        if item is None:
            return None
        label_id = item.data(Qt.ItemDataRole.UserRole)
        return next((label for label in self._labels if label.id == label_id), None)

    def _update_buttons(self) -> None:
        chosen = self.selected_label() is not None
        self.edit_button.setEnabled(chosen)
        self.delete_button.setEnabled(chosen)

    # -- actions ----------------------------------------------------------

    def add_label(self) -> None:
        values = self._ask_label(None)
        if values is not None:
            self.create_requested.emit(*values)

    def edit_selected(self) -> None:
        label = self.selected_label()
        if label is None:
            return
        values = self._ask_label(label)
        if values is None:
            return
        title, color = values
        if (title, color) != (label.title, label.css_color.lstrip("#").upper()):
            self.update_requested.emit(label.id, title, color)

    def delete_selected(self) -> None:
        label = self.selected_label()
        if label is not None and self._confirm_delete(label):
            self.delete_requested.emit(label.id, label.title)

    # -- prompts, split out so tests can answer them ----------------------

    def _ask_label(self, label: Label | None) -> tuple[str, str] | None:
        dialog = LabelDialog(self._labels, label, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        title, color = dialog.values()
        return (title, color) if title else None

    def _confirm_delete(self, label: Label) -> bool:
        count = self._usage.get(label.id, 0)
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Delete label")
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setText(f"Delete the label “{label.title}”?")
        confirm.setInformativeText(
            f"It will be removed from the {count} task"
            f"{'s' if count != 1 else ''} that use it."
            if count
            else "No tasks use it."
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return confirm.exec() == QMessageBox.StandardButton.Yes

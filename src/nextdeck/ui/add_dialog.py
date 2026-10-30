"""Dialogs for creating a task and for picking a due date."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, QDateTime, QTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .elided_label import WrappedLabel
from .label_chip import LabelRow, LabelToggle
from ..model import Label

#: How wide the label toggles may run before wrapping onto another row.
LABEL_ROW_WIDTH = 300


def _to_datetime(edit: QDateTimeEdit) -> datetime:
    """Read a QDateTimeEdit as an aware local datetime."""
    value = edit.dateTime().toPython()
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.astimezone()
    return value


class DueDateWidget(QWidget):
    """A due-date field that can also be 'no due date', with quick presets."""

    def __init__(self, initial: datetime | None = None, parent: QWidget | None = None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.enabled_box = QCheckBox("Has a due date")
        self.enabled_box.setChecked(initial is not None)
        layout.addWidget(self.enabled_box)

        self.edit = QDateTimeEdit()
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        if initial is not None:
            local = initial.astimezone()
            self.edit.setDateTime(
                QDateTime(
                    QDate(local.year, local.month, local.day),
                    QTime(local.hour, local.minute),
                )
            )
        else:
            # Noon reads as "sometime that day" in a way midnight does not.
            self.edit.setDateTime(QDateTime(QDate.currentDate(), QTime(12, 0)))
        layout.addWidget(self.edit)

        presets = QHBoxLayout()
        presets.setSpacing(4)
        for label, offset in (("Today", 0), ("Tomorrow", 1), ("Next week", 7)):
            button = QPushButton(label)
            button.setFlat(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, days=offset: self._preset(days))
            presets.addWidget(button)
        presets.addStretch(1)
        layout.addLayout(presets)

        self.enabled_box.toggled.connect(self.edit.setEnabled)
        self.edit.setEnabled(self.enabled_box.isChecked())

    def _preset(self, days: int) -> None:
        stamp = QDateTime(QDate.currentDate().addDays(days), self.edit.time())
        self.edit.setDateTime(stamp)
        self.enabled_box.setChecked(True)

    def value(self) -> datetime | None:
        if not self.enabled_box.isChecked():
            return None
        return _to_datetime(self.edit)


class AddTaskDialog(QDialog):
    """Collects a new task's title, description, optional due date and labels."""

    def __init__(
        self,
        board_hint: str = "",
        parent: QWidget | None = None,
        labels: list[Label] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Add task")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("What needs doing?")
        form.addRow("Title", self.title_edit)

        self.description_edit = QPlainTextEdit()
        self.description_edit.setPlaceholderText("Optional details (Markdown)")
        self.description_edit.setFixedHeight(80)
        form.addRow("Details", self.description_edit)

        self.due_widget = DueDateWidget()
        form.addRow("Due", self.due_widget)

        # A board with no labels defined gets no empty row.
        self.label_row = LabelRow()
        for label in labels or []:
            self.label_row.add(LabelToggle(label), LABEL_ROW_WIDTH)
        if labels:
            form.addRow("Labels", self.label_row)

        layout.addLayout(form)

        if board_hint:
            hint = WrappedLabel(f"Will be added to {board_hint}")
            hint.setEnabled(False)
            layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setText("Add")
        self._ok_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # An empty title would create an unreadable card, so gate the button.
        self.title_edit.textChanged.connect(
            lambda text: self._ok_button.setEnabled(bool(text.strip()))
        )
        self.title_edit.setFocus()

    def values(self) -> tuple[str, str, datetime | None]:
        return (
            self.title_edit.text().strip(),
            self.description_edit.toPlainText().strip(),
            self.due_widget.value(),
        )

    def selected_label_ids(self) -> list[int]:
        return [
            chip.label.id for chip in self.label_row.chips() if chip.isChecked()
        ]


class DueDateDialog(QDialog):
    """Edits just the due date of an existing task."""

    def __init__(
        self, title: str, initial: datetime | None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.setWindowTitle("Set due date")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)

        heading = WrappedLabel(title)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)

        self.due_widget = DueDateWidget(initial)
        layout.addWidget(self.due_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> datetime | None:
        return self.due_widget.value()

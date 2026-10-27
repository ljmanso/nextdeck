"""Inline task editor.

A panel inside the main window rather than a dialog, so the board stays
visible while a task is being edited. Saving is explicit: Save (or Ctrl+Enter)
writes, Escape and Cancel close and throw the edits away.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .elided_label import WrappedLabel
from .label_chip import LabelRow, LabelToggle
from ..model import Label, Task
from .add_dialog import DueDateWidget

PANEL_WIDTH = 340


class TaskEditor(QFrame):
    """Edits one task's title, due date and description in place."""

    #: (task, title, description, duedate, label_ids) — only when something
    #: changed. label_ids is None when the board's labels are not loaded.
    save_requested = Signal(object, str, str, object, object)
    closed = Signal()
    #: Emitted when the editor closes with edits that were thrown away, so the
    #: window can say so rather than letting the work vanish silently.
    discarded = Signal(object)
    delete_requested = Signal(object)
    toggle_done_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.task: Task | None = None
        self._due_widget: DueDateWidget | None = None

        self.setObjectName("editor")
        self.setFixedWidth(PANEL_WIDTH)
        self.setStyleSheet(
            """
            QFrame#editor {
                background: palette(window);
                border-left: 1px solid palette(mid);
            }
            """
        )

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 10, 12, 10)
        self._layout.setSpacing(8)

        self._heading = QLabel("Edit task")
        heading_font = self._heading.font()
        heading_font.setBold(True)
        self._heading.setFont(heading_font)
        self._layout.addWidget(self._heading)

        self._layout.addWidget(self._field_label("Title"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Task title")
        self._layout.addWidget(self.title_edit)

        self._layout.addWidget(self._field_label("Due"))
        self._due_holder = QVBoxLayout()
        self._due_holder.setContentsMargins(0, 0, 0, 0)
        self._layout.addLayout(self._due_holder)

        self._layout.addWidget(self._field_label("Details"))
        self.description_edit = QPlainTextEdit()
        self.description_edit.setPlaceholderText("Markdown is supported")
        self._layout.addWidget(self.description_edit, 1)

        self._labels_heading = self._field_label("Labels")
        self._layout.addWidget(self._labels_heading)
        self.label_row = LabelRow()
        self._layout.addWidget(self.label_row)
        self._labels: list[Label] = []
        self._show_labels(False)

        self._context = WrappedLabel("")
        self._context.setEnabled(False)
        self._layout.addWidget(self._context)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)

        self.done_button = QPushButton("Complete")
        self.done_button.clicked.connect(self._on_toggle_done)
        buttons.addWidget(self.done_button)

        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._on_delete)
        buttons.addWidget(self.delete_button)

        buttons.addStretch(1)
        self._layout.addLayout(buttons)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.discard_button = QPushButton("Cancel")
        self.discard_button.setToolTip("Discard changes and close  (Escape)")
        self.discard_button.clicked.connect(self.cancel)
        actions.addWidget(self.discard_button)
        actions.addStretch(1)
        self.save_button = QPushButton("Save")
        self.save_button.setDefault(True)
        self.save_button.setAutoDefault(True)
        self.save_button.setToolTip("Save changes and close  (Ctrl+Enter)")
        self.save_button.clicked.connect(self.save_and_close)
        actions.addWidget(self.save_button)
        self._layout.addLayout(actions)

        # A shortcut rather than relying on key events bubbling out of the
        # line edit and the text area, which is not guaranteed.
        for sequence, handler in (
            (QKeySequence(Qt.Key.Key_Escape), self.cancel),
            (QKeySequence("Ctrl+Return"), self.save_and_close),
        ):
            shortcut = QShortcut(sequence, self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)

        self.hide()

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setEnabled(False)
        return label

    # -- opening and closing ---------------------------------------------

    def open_task(
        self,
        task: Task,
        title: str | None = None,
        description: str | None = None,
        duedate: datetime | None = None,
        use_duedate: bool = False,
        label_ids: list[int] | None = None,
    ) -> None:
        """Show the panel populated from `task`.

        Opening another card while one is being edited discards those edits,
        the same as Escape: saving only ever happens through Save.

        The optional field overrides reinstate an edit that the server
        refused, so the text is still there to copy out or send again. They
        are kept separate from `task` because the card itself has been rolled
        back to what the server holds.
        """
        if self.task is not None and self.task.id != task.id:
            self.cancel()
        self.task = task
        self.title_edit.setText(task.title if title is None else title)
        self.description_edit.setPlainText(
            task.description if description is None else description
        )

        # DueDateWidget takes its initial value at construction, so rebuild it.
        if self._due_widget is not None:
            self._due_holder.removeWidget(self._due_widget)
            self._due_widget.deleteLater()
        self._due_widget = DueDateWidget(duedate if use_duedate else task.duedate)
        self._due_holder.addWidget(self._due_widget)

        self._build_label_toggles(label_ids if label_ids is not None else task.label_ids)

        # Just the stack: the labels are right above as toggles, and
        # repeating them here only competes with them.
        self._context.setText(task.stack_title or "")
        self.done_button.setText("Reopen" if task.is_done else "Complete")

        self.show()
        self.title_edit.setFocus()
        # Select everything but leave the cursor at the start, so a long title
        # shows its beginning rather than scrolling to the end.
        shown = self.title_edit.text()
        self.title_edit.setSelection(len(shown), -len(shown))

    # -- labels -----------------------------------------------------------

    def set_labels(self, labels: list[Label]) -> None:
        """The board's label set. Rebuilds the toggles for the open card."""
        self._labels = list(labels)
        if self.task is not None:
            self._build_label_toggles(self.task.label_ids)
        else:
            self._show_labels(bool(self._labels))

    def _show_labels(self, visible: bool) -> None:
        # A board with no labels defined gets no empty heading.
        self._labels_heading.setVisible(visible)
        self.label_row.setVisible(visible)

    def _build_label_toggles(self, selected: list[int]) -> None:
        self.label_row.clear()
        chosen = set(selected)
        for label in self._labels:
            toggle = LabelToggle(label)
            toggle.setChecked(label.id in chosen)
            self.label_row.add(toggle, PANEL_WIDTH - 40)
        self._show_labels(bool(self._labels))

    def selected_label_ids(self) -> list[int]:
        return [
            chip.label.id for chip in self.label_row.chips() if chip.isChecked()
        ]

    def current_values(self) -> tuple[str, str, datetime | None]:
        due = self._due_widget.value() if self._due_widget is not None else None
        return (
            self.title_edit.text().strip(),
            self.description_edit.toPlainText().strip(),
            due,
        )

    def has_changes(self) -> bool:
        if self.task is None:
            return False
        title, description, due = self.current_values()
        if title != self.task.title or description != (self.task.description or ""):
            return True
        # Compare to the minute: the picker has no finer resolution, so a
        # round trip through it must not look like an edit.
        before = self.task.duedate.astimezone().replace(
            second=0, microsecond=0
        ) if self.task.duedate else None
        after = due.astimezone().replace(second=0, microsecond=0) if due else None
        if before != after:
            return True
        # Labels the board does not define cannot be toggled here, so compare
        # only the ones the panel actually offers.
        offered = {label.id for label in self._labels}
        return set(self.selected_label_ids()) != (
            set(self.task.label_ids) & offered
        )

    def save_and_close(self) -> None:
        """Save if anything changed, then close. The Save button and Ctrl+Enter.

        Saving is explicit: nothing else in this panel writes to the server.
        """
        task = self.task
        if task is not None and self.has_changes():
            title, description, due = self.current_values()
            # An empty title would make the card unreadable; keep the old one.
            if not title:
                title = task.title
            self.save_requested.emit(
                task, title, description, due, self._label_ids_to_save()
            )
        self.close_without_saving()

    def _label_ids_to_save(self) -> list[int] | None:
        """The card's labels after this edit, or None when none are offered.

        None means "leave labels alone": with no label set loaded there is
        nothing to compare against, and sending an empty list would strip
        every label the card already has.
        """
        if not self._labels or self.task is None:
            return None
        untouched = [
            label_id
            for label_id in self.task.label_ids
            if label_id not in {label.id for label in self._labels}
        ]
        return sorted(set(self.selected_label_ids()) | set(untouched))

    def cancel(self) -> None:
        """Close, throwing away any edits. Bound to Escape and Cancel.

        Reports what was dropped so a mistaken Escape is visible rather than a
        silent loss of work.
        """
        task = self.task
        dropped = task is not None and self.has_changes()
        self.close_without_saving()
        if dropped:
            self.discarded.emit(task)

    def close_without_saving(self) -> None:
        self.task = None
        self.hide()
        self.closed.emit()

    # -- row actions ------------------------------------------------------

    def _on_toggle_done(self) -> None:
        if self.task is not None:
            task = self.task
            self.close_without_saving()
            self.toggle_done_requested.emit(task)

    def _on_delete(self) -> None:
        if self.task is not None:
            task = self.task
            self.close_without_saving()
            self.delete_requested.emit(task)

    # -- keyboard ---------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self.cancel()
            return
        # Ctrl+Enter is the conventional "commit" for a multi-line form.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            event.accept()
            self.save_and_close()
            return
        super().keyPressEvent(event)

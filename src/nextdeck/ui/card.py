"""A single task card."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QFont, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMenu,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..model import DAY_STEP, WEEK_STEP, Bucket, Label, Task
from .elided_label import ElidedLabel
from .label_chip import LabelChip, LabelRow

#: Private drag format. Carries the card id as ASCII digits.
TASK_MIME = "application/x-nextdeck-task-id"

#: A calm left-edge accent per column. Overdue is amber rather than red: it
#: should read as "look here", not as an alarm.
ACCENTS: dict[Bucket, str] = {
    Bucket.OVERDUE: "#c77700",
    Bucket.TODAY: "#0082c9",
    Bucket.TOMORROW: "#4aa3df",
    Bucket.NEXT_7: "#7d8fa3",
    Bucket.NEXT_30: "#95a1ad",
    Bucket.LATER: "#a9b2bb",
    Bucket.NO_DUE_DATE: "#b0b6bd",
    Bucket.DONE: "#5c9e5c",
}


class CardWidget(QFrame):
    """Shows one task and acts as the drag source for rescheduling."""

    toggle_done_requested = Signal(object)
    delete_requested = Signal(object)
    edit_due_requested = Signal(object)
    #: Emitted on a plain click (never after a drag), to open the editor.
    activated = Signal(object)
    #: Bracket a drag, so the board can open up its collapsed drop targets.
    drag_started = Signal()
    drag_finished = Signal()
    #: Keyboard navigation away from this card: "up", "down", "left",
    #: "right", "first" or "last". The board decides where that lands.
    navigate_requested = Signal(str)
    #: Move this card's due date by a number of days, +ve later, -ve earlier.
    shift_due_requested = Signal(object, int)
    #: A label chip was clicked: filter the board by that label's title.
    label_clicked = Signal(str)

    def __init__(
        self,
        task: Task,
        bucket: Bucket,
        date_format: str = "%a %d %b",
        title_lines: int = 2,
        label_colors: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.task = task
        #: Label title -> Deck colour, so chips can wear the board's palette.
        self._label_colors = label_colors or {}
        #: Only built when the card has labels; a row of empty chips per card
        #: would be pure overhead on a large board.
        self.label_row = None
        self.bucket = bucket
        self._date_format = date_format
        self._title_lines = max(1, int(title_lines))
        self._press_pos: QPoint | None = None
        self._dragged = False

        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        # Reachable by keyboard, and by a click that does not land on one of
        # the child buttons.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setStyleSheet(self._stylesheet())
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        self._build()

    # -- construction -----------------------------------------------------

    def _stylesheet(self) -> str:
        accent = ACCENTS.get(self.bucket, "#b0b6bd")
        return f"""
            QFrame#card {{
                background: palette(base);
                border: 1px solid palette(mid);
                border-left: 3px solid {accent};
                border-radius: 4px;
            }}
            QFrame#card:hover {{ background: palette(alternate-base); }}
            QFrame#card:focus {{
                background: palette(alternate-base);
                border: 1px solid palette(highlight);
                border-left: 3px solid {accent};
            }}
        """

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 6, 6)
        outer.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(4)

        self.title_label = ElidedLabel(
            self.task.title or "(untitled)", max_lines=self._title_lines
        )
        font = self.title_label.font()
        if self.task.is_done:
            font.setStrikeOut(True)
            self.title_label.setEnabled(False)
        self.title_label.setFont(font)
        top.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignTop)

        self.done_button = QToolButton()
        self.done_button.setAutoRaise(True)
        self.done_button.setCursor(Qt.CursorShape.PointingHandCursor)
        if self.task.is_done:
            self.done_button.setText("↺")
            self.done_button.setToolTip("Mark as not complete")
        else:
            self.done_button.setText("✓")
            self.done_button.setToolTip("Mark as complete")
        self.done_button.clicked.connect(
            lambda: self.toggle_done_requested.emit(self.task)
        )
        top.addWidget(self.done_button, 0, Qt.AlignmentFlag.AlignTop)

        self.delete_button = QToolButton()
        self.delete_button.setAutoRaise(True)
        self.delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        trash = self.style().standardIcon(
            QStyle.StandardPixmap.SP_TrashIcon
        )
        if trash.isNull():
            self.delete_button.setText("✕")
        else:
            self.delete_button.setIcon(trash)
        self.delete_button.setToolTip("Delete this task")
        self.delete_button.clicked.connect(
            lambda: self.delete_requested.emit(self.task)
        )
        top.addWidget(self.delete_button, 0, Qt.AlignmentFlag.AlignTop)

        outer.addLayout(top)

        if self.task.labels:
            self.label_row = LabelRow()
            for title in self.task.labels:
                chip = LabelChip(
                    Label(0, title, self._label_colors.get(title, ""))
                )
                chip.clicked_label.connect(self.label_clicked)
                # A card is only as wide as its column, so wrap generously.
                self.label_row.add(chip, max(80, self.width() - 24))
            outer.addWidget(self.label_row)

        meta = self._meta_text()
        if meta:
            meta_label = ElidedLabel(meta, max_lines=1)
            meta_font = QFont(meta_label.font())
            meta_font.setPointSizeF(max(7.0, meta_font.pointSizeF() - 1.0))
            meta_label.setFont(meta_font)
            meta_label.setEnabled(False)
            outer.addWidget(meta_label)

        tooltip = [self.task.title] if self.task.title else []
        if self.task.description:
            tooltip.append(self.task.description)
        if tooltip:
            # Elision loses text, so keep the whole thing reachable on hover.
            self.setToolTip("\n\n".join(tooltip))

    def _meta_text(self) -> str:
        parts: list[str] = []

        if self.task.is_done and self.task.done is not None:
            parts.append(
                "done " + self.task.done.astimezone().strftime(self._date_format)
            )
        elif self.task.duedate is not None:
            parts.append(self._due_text())

        if self.task.stack_title:
            parts.append(self.task.stack_title)
        # Labels used to live here as plain text; they have their own row of
        # clickable chips now.
        return "  ·  ".join(parts)

    def _due_text(self) -> str:
        local = self.task.duedate.astimezone()  # type: ignore[union-attr]
        stamp = local.strftime(self._date_format)
        days = self.task.days_until_due(date.today())

        if days is None:
            return stamp
        if days < 0:
            unit = "day" if abs(days) == 1 else "days"
            return f"{stamp} · {abs(days)} {unit} ago"
        if local.hour or local.minute:
            return f"{stamp} {local.strftime('%H:%M')}"
        return stamp

    # -- context menu -----------------------------------------------------

    def _show_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        if self.task.is_done:
            menu.addAction(
                "Mark as not complete",
                lambda: self.toggle_done_requested.emit(self.task),
            )
        else:
            menu.addAction(
                "Mark as complete", lambda: self.toggle_done_requested.emit(self.task)
            )
        menu.addAction("Set due date…", lambda: self.edit_due_requested.emit(self.task))
        menu.addSeparator()
        menu.addAction("Delete", lambda: self.delete_requested.emit(self.task))
        menu.exec(self.mapToGlobal(pos))

    # -- keyboard ---------------------------------------------------------

    #: Keys that move the focus somewhere else on the board.
    NAVIGATION = {
        Qt.Key.Key_Up: "up",
        Qt.Key.Key_Down: "down",
        Qt.Key.Key_Left: "left",
        Qt.Key.Key_Right: "right",
        Qt.Key.Key_Home: "first",
        Qt.Key.Key_End: "last",
    }

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Drive the board from the focused card.

        Ctrl+left/right nudge the due date rather than hopping a column, so
        the modifier changes what the arrow acts on — the card instead of the
        focus — while keeping the direction meaning the same thing.
        """
        key = event.key()
        modifiers = event.modifiers()
        control = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)

        if control and key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = WEEK_STEP if shift else DAY_STEP
            direction = 1 if key == Qt.Key.Key_Right else -1
            self.shift_due_requested.emit(self.task, direction * step)
            event.accept()
            return

        if not modifiers and key in self.NAVIGATION:
            self.navigate_requested.emit(self.NAVIGATION[key])
            event.accept()
            return

        if not modifiers and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activated.emit(self.task)
            event.accept()
            return

        if not modifiers and key == Qt.Key.Key_Space:
            self.toggle_done_requested.emit(self.task)
            event.accept()
            return

        if not modifiers and key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_requested.emit(self.task)
            event.accept()
            return

        super().keyPressEvent(event)

    # -- drag source ------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_pos is None:
            return super().mouseMoveEvent(event)
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return super().mouseMoveEvent(event)

        moved = (event.position().toPoint() - self._press_pos).manhattanLength()
        if moved < self.startDragDistance():
            return super().mouseMoveEvent(event)

        self._dragged = True
        self._start_drag()
        self._press_pos = None

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was_press = self._press_pos is not None
        self._press_pos = None
        super().mouseReleaseEvent(event)
        # A press that never turned into a drag is a click: open the editor.
        # The ✓/🗑 buttons are children and consume their own clicks, so they
        # never reach here.
        if was_press and not self._dragged and event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.task)

    def startDragDistance(self) -> int:
        from PySide6.QtWidgets import QApplication

        return QApplication.startDragDistance()

    def _start_drag(self) -> None:
        mime = QMimeData()
        mime.setData(TASK_MIME, str(self.task.id).encode("ascii"))

        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        self.drag_started.emit()
        try:
            # Blocking: the drop, and so the re-render, happens inside here.
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.drag_finished.emit()

"""A due-date column: header, scrollable card list, and drop target."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFontMetrics,
    QPalette,
)
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..model import DROP_TARGET_OFFSETS, Bucket, Task
from .card import TASK_MIME, CardWidget

#: Minimum column width; the real width scales with the font so that a larger
#: font size does not simply clip the card text.
COLUMN_WIDTH = 230
#: Roughly how many average characters a column should fit.
COLUMN_CHARS = 32
#: Padding around a collapsed column's header text.
COMPACT_PADDING = 28
#: Breathing room left around a card scrolled into view.
CARD_MARGIN = 6
#: How far past its floor a column may grow to soak up unused window width.
#: Uncapped, showing a single column stretches one card across the whole
#: screen, stranding its buttons an arm's length from its title.
MAX_GROWTH = 2.0

#: Buckets a card can be dropped on. Overdue is excluded because scheduling
#: something into the past is not a meaningful action; Done is included because
#: dropping there is how you complete a task.
DROPPABLE: frozenset[Bucket] = frozenset(DROP_TARGET_OFFSETS) | {Bucket.DONE}


class ColumnWidget(QFrame):
    """One column of the board."""

    #: (card id, target bucket)
    task_dropped = Signal(int, object)
    toggle_done_requested = Signal(object)
    delete_requested = Signal(object)
    edit_due_requested = Signal(object)
    card_activated = Signal(object)
    drag_started = Signal()
    drag_finished = Signal()
    #: (source card, direction) — the board resolves where focus should land.
    navigate_requested = Signal(object, str)
    shift_due_requested = Signal(object, int)
    label_clicked = Signal(str)

    def __init__(
        self,
        bucket: Bucket,
        date_format: str = "%a %d %b",
        title_lines: int = 2,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.bucket = bucket
        self._date_format = date_format
        self._title_lines = max(1, int(title_lines))
        self._label_colors: dict[str, str] = {}
        self._drag_active = False

        self.setObjectName("column")
        self._compact = False
        self.setMinimumWidth(self._preferred_width())
        self.setAcceptDrops(bucket in DROPPABLE)
        self._apply_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.header = QLabel()
        self.header.setObjectName("columnHeader")
        header_font = self.header.font()
        header_font.setBold(True)
        self.header.setFont(header_font)
        layout.addWidget(self.header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.viewport().setAutoFillBackground(False)

        self._list = QWidget()
        self._list.setAutoFillBackground(False)
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch(1)

        self._scroll.setWidget(self._list)
        layout.addWidget(self._scroll, 1)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.set_tasks([])

    def _preferred_width(self) -> int:
        metrics = QFontMetrics(self.font())
        return max(COLUMN_WIDTH, metrics.averageCharWidth() * COLUMN_CHARS)

    def _compact_width(self) -> int:
        """Just wide enough for the header, so an empty column stops hogging
        space it has nothing to put in."""
        metrics = QFontMetrics(self.header.font())
        return metrics.horizontalAdvance(f"{self.bucket.title} (00)") + COMPACT_PADDING

    def _max_width(self) -> int:
        return int(self._preferred_width() * MAX_GROWTH)

    @property
    def is_compact(self) -> bool:
        return self._compact

    def set_compact(self, compact: bool) -> None:
        """Collapse to header width, or allow growing to fill the window."""
        self._compact = compact
        if compact:
            self.setMinimumWidth(self._compact_width())
            self.setMaximumWidth(self._compact_width())
            self.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
            )
        else:
            self.setMaximumWidth(self._max_width())
            self.setMinimumWidth(self._preferred_width())
            self.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        self.updateGeometry()

    # -- styling ----------------------------------------------------------

    def _apply_style(self) -> None:
        background = "palette(midlight)" if self._drag_active else "palette(window)"
        border = "palette(highlight)" if self._drag_active else "palette(mid)"
        style = "dashed" if self._drag_active else "solid"
        self.setStyleSheet(
            f"""
            QFrame#column {{
                background: {background};
                border: 1px {style} {border};
                border-radius: 6px;
            }}
            QLabel#columnHeader {{ border: none; background: transparent; }}
            """
        )

    # -- content ----------------------------------------------------------

    def set_date_format(self, date_format: str) -> None:
        """Change how due dates are rendered; takes effect on the next refill."""
        self._date_format = date_format

    def set_label_colors(self, colors: dict[str, str]) -> None:
        """The board's label palette; takes effect on the next refill."""
        self._label_colors = dict(colors)

    def set_title_lines(self, lines: int) -> None:
        """Change the card title line limit; takes effect on the next refill."""
        self._title_lines = max(1, int(lines))

    def set_tasks(self, tasks: list[Task]) -> None:
        """Replace the column's cards."""
        self._clear()
        count = len(tasks)
        self._set_header(count)

        for task in tasks:
            card = CardWidget(
                task,
                self.bucket,
                self._date_format,
                self._title_lines,
                self._label_colors,
            )
            card.toggle_done_requested.connect(self.toggle_done_requested)
            card.delete_requested.connect(self.delete_requested)
            card.edit_due_requested.connect(self.edit_due_requested)
            card.activated.connect(self.card_activated)
            card.drag_started.connect(self.drag_started)
            card.drag_finished.connect(self.drag_finished)
            card.navigate_requested.connect(
                lambda direction, source=card: self.navigate_requested.emit(
                    source, direction
                )
            )
            card.shift_due_requested.connect(self.shift_due_requested)
            card.label_clicked.connect(self.label_clicked)
            # Insert before the trailing stretch.
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)

        if not count:
            placeholder = QLabel("—")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setEnabled(False)
            self._list_layout.insertWidget(self._list_layout.count() - 1, placeholder)

    def ensure_card_visible(self, card: CardWidget) -> None:
        """Scroll this column so `card` is on screen."""
        if card.parentWidget() is not self._list:
            return
        self._scroll.ensureWidgetVisible(card, CARD_MARGIN, CARD_MARGIN)

    def cards(self) -> list[CardWidget]:
        """The card widgets currently in this column, top to bottom."""
        found = []
        for index in range(self._list_layout.count()):
            widget = self._list_layout.itemAt(index).widget()
            if isinstance(widget, CardWidget):
                found.append(widget)
        return found

    def task_ids(self) -> list[int]:
        return [card.task.id for card in self.cards()]

    def _set_header(self, count: int) -> None:
        """Column name plus a muted, bracketed count.

        The count is informational and never styled as an alert.
        """
        muted = self.palette().color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText
        ).name()
        self.header.setTextFormat(Qt.TextFormat.RichText)
        self.header.setText(
            f"{html.escape(self.bucket.title)}"
            f' <span style="color:{muted}; font-weight:normal">({count})</span>'
        )

    def _clear(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Deliberately not setParent(None): a widget with no parent is
                # a top-level window, so reparenting the card that currently
                # has focus briefly turns it into one, which drops the focus
                # and deactivates the real window. takeAt has already removed
                # it from the layout, so hiding and deleting is enough.
                widget.hide()
                widget.deleteLater()

    # -- drop target ------------------------------------------------------

    def _set_drag_active(self, active: bool) -> None:
        if self._drag_active != active:
            self._drag_active = active
            self._apply_style()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(TASK_MIME):
            event.acceptProposedAction()
            self._set_drag_active(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasFormat(TASK_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_drag_active(False)
        data = event.mimeData().data(TASK_MIME)
        if not data:
            event.ignore()
            return
        try:
            task_id = int(bytes(data.data()).decode("ascii"))
        except (ValueError, TypeError, UnicodeDecodeError):
            event.ignore()
            return

        event.acceptProposedAction()
        self.task_dropped.emit(task_id, self.bucket)

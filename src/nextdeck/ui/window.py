"""The board window."""

from __future__ import annotations

import dataclasses
import html
import logging
from datetime import date, datetime, timezone
from time import monotonic
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QApplication,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QToolBar,
    QWidget,
)

from .. import config as cfgmod
from .. import state as statemod
from ..config import Config
from ..model import (
    DROP_TARGET_OFFSETS,
    Bucket,
    Task,
    bucket_of,
    buckets_from_keys,
    group_by_bucket,
    reschedule_datetime,
    shift_duedate,
    task_matches,
)
from ..sync import SyncController
from .add_dialog import AddTaskDialog, DueDateDialog
from .card import CardWidget
from .column import ColumnWidget
from .editor import TaskEditor
from .label_dialog import NewLabelDialog
from .icon import app_icon
from .theme import apply_font_size
from .settings_dialog import SettingsDialog
from .shortcuts_dialog import ShortcutsDialog

log = logging.getLogger(__name__)

#: How long after a show-triggered refresh another one is suppressed.
SHOW_REFRESH_COOLDOWN_S = 5.0


def _system_tray_available() -> bool:
    from PySide6.QtWidgets import QSystemTrayIcon

    return QSystemTrayIcon.isSystemTrayAvailable()


class MainWindow(QMainWindow):
    """Shows one Deck board projected onto due-date columns."""

    quit_requested = Signal()

    def __init__(
        self,
        config: Config,
        controller: SyncController,
        config_path: "Path | None" = None,
        state_path: "Path | None" = None,
    ):
        super().__init__()
        self.config = config
        self.controller = controller
        #: Where to write settings back to, honouring `--config`.
        self.config_path = config_path
        #: Where window geometry is remembered; None means the default spot.
        self.state_path = state_path
        #: Server truth. Optimistic edits are rendered without touching this,
        #: so a failed write can be rolled back by re-rendering from here.
        self._tasks: list[Task] = []
        self._quitting = False
        self._hidden_count = 0
        #: True while a card is being dragged across the board.
        self._dragging = False
        #: Whether a sync has ever come back, successfully or not. Until one
        #: has, the initial refresh is someone else's job and showing the
        #: window must not duplicate it.
        self._has_synced = False
        #: When a show last asked for a refresh, to damp down toggling.
        self._last_show_refresh = 0.0
        #: The last values handed to a save, kept so a refusal can give them
        #: back rather than losing what was typed.
        self._last_save_attempt: tuple | None = None
        #: Which card the editor is on, so focus can return to it on close.
        self._editing_task_id: int | None = None
        #: The live search filter; empty means "show everything".
        self._query = ""
        self._filtered_out = 0
        #: The board's label set, and a title -> colour map built from it.
        self._labels: list = []
        self._label_colors: dict[str, str] = {}
        #: Overridable so tests need not monkeypatch a Qt type.
        self._tray_available = _system_tray_available

        self.setWindowTitle(self._window_title())
        self.setWindowIcon(app_icon())
        self.resize(1250, 720)
        self._restore_geometry()

        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        self.controller.tasks_loaded.connect(self._on_tasks_loaded)
        self.controller.error.connect(self._on_error)
        self.controller.busy_changed.connect(self._on_busy)
        self.controller.mutated.connect(self._on_mutated)
        self.controller.save_failed.connect(self._on_save_failed)
        self.controller.labels_loaded.connect(self._on_labels_loaded)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.controller.refresh)
        self._apply_refresh_interval()

        # Columns are relative to "today", so re-render after midnight even if
        # nothing on the server changed.
        self._midnight_timer = QTimer(self)
        self._midnight_timer.setInterval(60_000)
        self._midnight_timer.timeout.connect(self._check_day_rollover)
        self._midnight_timer.start()
        self._rendered_day = date.today()

    # -- construction -----------------------------------------------------

    def _window_title(self) -> str:
        if self.config.board.title:
            return f"nextdeck — {self.config.board.title}"
        return "nextdeck"

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(toolbar)

        self.add_action = QAction("Add task", self)
        self.add_action.setShortcut(QKeySequence(QKeySequence.StandardKey.New))
        self.add_action.triggered.connect(self.add_task)
        toolbar.addAction(self.add_action)

        self.new_label_action = QAction("New label", self)
        self.new_label_action.setToolTip("Define a new label on this board")
        self.new_label_action.triggered.connect(self.new_label)
        toolbar.addAction(self.new_label_action)

        self.refresh_action = QAction("Refresh", self)
        self.refresh_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Refresh))
        self.refresh_action.triggered.connect(self.controller.refresh)
        toolbar.addAction(self.refresh_action)

        # Ctrl+W hides to the tray; Ctrl+Q genuinely exits.
        self.hide_action = QAction("Hide", self)
        self.hide_action.setShortcut(QKeySequence("Ctrl+W"))
        self.hide_action.triggered.connect(self.close)
        self.addAction(self.hide_action)

        self.quit_action = QAction("Quit", self)
        self.quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        self.quit_action.triggered.connect(self.quit_requested.emit)
        self.addAction(self.quit_action)

        toolbar.addSeparator()

        self.settings_action = QAction("Settings", self)
        self.settings_action.triggered.connect(self.open_settings)
        toolbar.addAction(self.settings_action)

        self.shortcuts_action = QAction("Shortcuts", self)
        self.shortcuts_action.setShortcut(QKeySequence(Qt.Key.Key_F1))
        self.shortcuts_action.setToolTip("Keyboard shortcuts  (F1)")
        self.shortcuts_action.triggered.connect(self.show_shortcuts)
        toolbar.addAction(self.shortcuts_action)

        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        toolbar.addWidget(spacer)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search tasks…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setToolTip(
            "Filter by title, details, label or stack  (Ctrl+F)"
        )
        self.search_edit.setMaximumWidth(260)
        self.search_edit.textChanged.connect(self._on_search_changed)
        # Enter hands the keyboard to the results; Escape backs out of the
        # filter entirely, so neither leaves the cursor stuck in the box.
        self.search_edit.returnPressed.connect(self.focus_first_card)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.search_edit)
        escape.setContext(Qt.ShortcutContext.WidgetShortcut)
        escape.activated.connect(self.clear_search)
        toolbar.addWidget(self.search_edit)

        self.find_action = QAction("Find", self)
        self.find_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Find))
        self.find_action.triggered.connect(self.focus_search)
        self.addAction(self.find_action)

        self._toggle_shortcut = QShortcut(QKeySequence(), self)
        self._toggle_shortcut.setContext(
            Qt.ShortcutContext.ApplicationShortcut
        )
        self._toggle_shortcut.activated.connect(self.toggle_visibility)
        self.apply_toggle_shortcut(self.config.ui.toggle_shortcut)

    def apply_toggle_shortcut(self, sequence: str) -> None:
        """Rebind the show/hide key, or unbind it when `sequence` is empty.

        Only reaches nextdeck while nextdeck has the keyboard, so this hides a
        window that is in front of you; summoning one from the tray needs a
        binding the desktop owns, pointed at `nextdeck --toggle`.
        """
        key = QKeySequence(sequence or "")
        # An unparseable string is not empty — Qt keeps it as one unknown key —
        # but it does round-trip back to nothing, which is how a typo in the
        # config file ends up switching the shortcut off instead of binding
        # some key nobody asked for.
        usable = bool(key.toString())
        self._toggle_shortcut.setKey(key if usable else QKeySequence())
        self._toggle_shortcut.setEnabled(usable)
        # The cheat sheet names this key, so the cached one is now out of date.
        existing = getattr(self, "_shortcuts_dialog", None)
        if existing is not None:
            existing.close()
            existing.deleteLater()
            self._shortcuts_dialog = None

    def _build_central(self) -> None:
        """Board scroll area on the left, task editor panel on the right."""
        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._board_scroll = QScrollArea()
        self._board_scroll.setWidgetResizable(True)
        self._board_scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self._board_scroll, 1)

        self.editor = TaskEditor()
        self.editor.save_requested.connect(self._on_editor_save)
        self.editor.discarded.connect(self._on_editor_discarded)
        self.editor.delete_requested.connect(self._on_delete)
        self.editor.toggle_done_requested.connect(self._on_toggle_done)
        self.editor.closed.connect(self._on_editor_closed)
        outer.addWidget(self.editor, 0)

        self.setCentralWidget(central)
        self._build_columns()

    def _build_columns(self) -> None:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.columns: dict[Bucket, ColumnWidget] = {}
        for bucket in self._visible_buckets():
            column = ColumnWidget(
                bucket,
                self.config.ui.date_format,
                title_lines=self.config.ui.card_title_lines,
            )
            column.task_dropped.connect(self._on_task_dropped)
            column.toggle_done_requested.connect(self._on_toggle_done)
            column.delete_requested.connect(self._on_delete)
            column.edit_due_requested.connect(self._on_edit_due)
            column.card_activated.connect(self._on_card_activated)
            column.drag_started.connect(self._on_drag_started)
            column.drag_finished.connect(self._on_drag_finished)
            column.navigate_requested.connect(self._on_navigate)
            column.shift_due_requested.connect(self._on_shift_due)
            column.label_clicked.connect(self.search_for)
            column.set_label_colors(self._label_colors)
            layout.addWidget(column, 1)
            self.columns[bucket] = column
        layout.addStretch(1)

        self._board_layout = layout
        self._board_stretch_index = layout.count() - 1
        self._board_scroll.setWidget(container)
        self._update_column_widths()

    def _visible_buckets(self) -> tuple[Bucket, ...]:
        return buckets_from_keys(self.config.ui.visible_columns)

    def _build_statusbar(self) -> None:
        self.status_label = QLabel("Starting…")
        self.statusBar().addWidget(self.status_label, 1)
        self.updated_label = QLabel("")
        self.updated_label.setEnabled(False)
        self.statusBar().addPermanentWidget(self.updated_label)

    def _apply_refresh_interval(self) -> None:
        minutes = max(1, int(self.config.ui.refresh_interval_minutes or 10))
        self._refresh_timer.setInterval(minutes * 60_000)
        self._refresh_timer.start()

    # -- rendering --------------------------------------------------------

    def _render(self, tasks: list[Task]) -> None:
        today = date.today()
        self._rendered_day = today
        matching = [task for task in tasks if task_matches(task, self._query)]
        self._filtered_out = len(tasks) - len(matching)
        grouped = group_by_bucket(matching, today, self.config.ui.done_column_days)
        focused = self._focused_task_id()
        for bucket, column in self.columns.items():
            column.set_tasks(grouped.get(bucket, []))
        self._update_column_widths()
        if focused is not None and not self._focus_task(focused):
            # set_tasks rebuilds every card, so focus has to be put back on
            # the card that now stands for the same task. When that card has
            # gone — filtered out, completed, deleted — the focused widget was
            # destroyed with it and the keyboard would be left pointing at
            # nothing, so fall back to the first card still on the board.
            self.focus_first_card()
        hidden = sum(
            len(items) for b, items in grouped.items() if b not in self.columns
        )
        self._hidden_count = hidden

    def _board_summary(self) -> str:
        """What the status bar says about the board as a whole."""
        total = len(self._tasks)
        if not total:
            return "No tasks on this board"

        if self._query:
            shown = total - self._filtered_out
            if not shown:
                return f"Nothing matches “{self._query}”"
            summary = f"{shown} of {total} tasks shown"
        else:
            summary = f"{total} tasks"

        if self._hidden_count:
            summary += f" · {self._hidden_count} in hidden columns"
        return summary

    # -- labels -----------------------------------------------------------

    def _on_labels_loaded(self, labels: list) -> None:
        """Take the board's label set and re-colour what is on screen."""
        self._labels = list(labels)
        self._label_colors = {label.title: label.color for label in self._labels}
        for column in self.columns.values():
            column.set_label_colors(self._label_colors)
        self.editor.set_labels(self._labels)
        self._render(self._tasks)

    # -- search -----------------------------------------------------------

    def focus_search(self) -> None:
        """Ctrl+F: put the cursor in the search box, selecting what is there."""
        self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_edit.selectAll()

    def clear_search(self) -> None:
        """Escape: drop the filter and give the board back the keyboard."""
        self.search_edit.clear()
        self.focus_first_card()

    def search_for(self, text: str) -> None:
        """Filter the board by `text`, as clicking a label does."""
        self.search_edit.setText(text)

    def _on_search_changed(self, text: str) -> None:
        self._query = text.strip()
        self._render(self._tasks)
        self.status_label.setText(self._board_summary())

    # -- keyboard navigation ----------------------------------------------

    def _focused_card(self):
        """The focused card widget, if the focus is on one."""
        widget = QApplication.focusWidget()
        while widget is not None:
            if isinstance(widget, CardWidget):
                return widget
            widget = widget.parentWidget()
        return None

    def _focused_task_id(self) -> int | None:
        card = self._focused_card()
        return None if card is None else card.task.id

    def _focus_card(self, card) -> None:
        """Give `card` the keyboard focus, then make sure it can be seen.

        The scroll waits for the next event cycle. Straight after `set_tasks`
        rebuilds a column its new cards have no useful geometry yet, so
        scrolling to one here would aim at where it is not; by the time the
        timer fires Qt has laid them out.
        """
        card.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, self._reveal_focused_card)

    def _reveal_focused_card(self) -> None:
        """Scroll both axes so the focused card is on screen.

        Two scroll areas can hide it: the board scrolls sideways across the
        columns, and each column scrolls down its own cards. Moving the focus,
        or a nudge that throws a card into a column scrolled elsewhere, would
        otherwise leave it out of sight.

        Deliberately works from whatever is focused now rather than from a
        remembered widget: by the time this runs the board may have been
        rebuilt and that widget deleted.
        """
        card = self._focused_card()
        if card is None:
            return
        for column in self.columns.values():
            if card in column.cards():
                self._board_scroll.ensureWidgetVisible(column, 0, 0)
                column.ensure_card_visible(card)
                return

    def _focus_task(self, task_id: int) -> bool:
        """Put focus on the card showing `task_id`. False if it is gone."""
        for column in self.columns.values():
            for card in column.cards():
                if card.task.id == task_id:
                    self._focus_card(card)
                    return True
        return False

    def focus_first_card(self) -> bool:
        """Focus the first card on the board, left to right."""
        for column in self.columns.values():
            cards = column.cards()
            if cards:
                self._focus_card(cards[0])
                return True
        return False

    def _on_navigate(self, source, direction: str) -> None:
        """Move focus from `source` in `direction`."""
        order = list(self.columns.values())
        here = next(
            (
                (index, column.cards())
                for index, column in enumerate(order)
                if source in column.cards()
            ),
            None,
        )
        if here is None:
            return
        column_index, cards = here
        row = cards.index(source)

        if direction in ("up", "down", "first", "last"):
            target = {
                "up": row - 1,
                "down": row + 1,
                "first": 0,
                "last": len(cards) - 1,
            }[direction]
            # Clamping rather than wrapping: at the top of a column, Up should
            # do nothing visible instead of jumping to the bottom.
            target = max(0, min(target, len(cards) - 1))
            self._focus_card(cards[target])
            return

        step = 1 if direction == "right" else -1
        index = column_index + step
        while 0 <= index < len(order):
            # Skip the columns with nothing in them; stopping on one would
            # strand the focus with nowhere to go but back.
            neighbours = order[index].cards()
            if neighbours:
                self._focus_card(neighbours[min(row, len(neighbours) - 1)])
                return
            index += step

    def _on_shift_due(self, task: Task, days: int) -> None:
        """Nudge a due date by a number of days, from the keyboard.

        Counted from the card on screen rather than from `self._tasks`.
        Server truth only catches up after the write and a refresh, so going
        back to it would make two quick presses land on the same date and
        quietly drop the first one.
        """
        new_due = shift_duedate(task, days)
        self._render_with_override(task.id, duedate=new_due)
        self.controller.set_duedate(task, new_due)
        self.status_label.setText(
            f"“{task.title or '(untitled)'}” due "
            f"{new_due.astimezone().strftime(self.config.ui.date_format)}"
        )

    def _update_column_widths(self) -> None:
        """Share the spare width between the columns that hold cards.

        Showing a subset of the columns often leaves width going unused, and
        handing it to the columns that actually have something in them beats
        leaving a gap down the right-hand side. An empty column collapses to
        its header rather than reserving a full-width lane it has nothing to
        put in — it stays a drop target, so a card can still be filed there.
        """
        # A collapsed column is a smaller target, and the empty ones are
        # exactly what you aim at to clear a date, so open them up again for
        # as long as a card is in the air.
        compact_empty = bool(self.config.ui.compact_empty_columns) and not self._dragging
        expandable = 0
        for index, column in enumerate(self.columns.values()):
            compact = compact_empty and not column.cards()
            column.set_compact(compact)
            self._board_layout.setStretch(index, 0 if compact else 1)
            expandable += 0 if compact else 1
        # With nothing growable left there is no one to absorb the slack, so
        # the trailing spacer goes back to keep the columns left-aligned.
        self._board_layout.setStretch(
            self._board_stretch_index, 0 if expandable else 1
        )

    def _on_drag_started(self) -> None:
        self._dragging = True
        self._update_column_widths()

    def _on_drag_finished(self) -> None:
        self._dragging = False
        self._update_column_widths()

    def _check_day_rollover(self) -> None:
        if date.today() != self._rendered_day:
            log.info("day changed; re-bucketing")
            self._render(self._tasks)

    def _on_tasks_loaded(self, tasks: list[Task]) -> None:
        first_load = not self._has_synced
        self._has_synced = True
        self._tasks = list(tasks)
        self._render(self._tasks)
        if first_load and self._focused_card() is None and self.editor.isHidden():
            # Give the board a keyboard starting point, but only once: later
            # refreshes must not yank focus out of whatever is in use.
            self.focus_first_card()
        stamp = datetime.now().strftime("%H:%M")
        self.updated_label.setText(f"Updated {stamp}")
        self.status_label.setText(self._board_summary())

    def _on_busy(self, busy: bool) -> None:
        self.refresh_action.setEnabled(not busy)
        if busy:
            self.status_label.setText("Syncing…")

    def _on_mutated(self, message: str) -> None:
        self.status_label.setText(message)

    def _on_error(self, message: str) -> None:
        # A failed first sync still counts as having tried, so that reopening
        # the window retries rather than sitting on the error forever.
        self._has_synced = True
        # Roll back any optimistic edit by re-rendering server truth.
        self._render(self._tasks)
        self.status_label.setText(message)
        self.statusBar().showMessage(message, 8000)

    # -- optimistic edits -------------------------------------------------

    def _render_with_override(self, task_id: int, **changes) -> None:
        """Re-render with one task locally modified, pending the real write."""
        preview = [
            dataclasses.replace(task, **changes) if task.id == task_id else task
            for task in self._tasks
        ]
        self._render(preview)

    def _find(self, task_id: int) -> Task | None:
        return next((t for t in self._tasks if t.id == task_id), None)

    # -- actions ----------------------------------------------------------

    def add_task(self) -> None:
        if not self.config.is_complete():
            self.open_settings()
            return

        hint = self.config.board.title
        if self.config.board.default_stack_title:
            hint = f"{hint} › {self.config.board.default_stack_title}"
        dialog = AddTaskDialog(hint, self, labels=self._labels)
        if dialog.exec() != AddTaskDialog.DialogCode.Accepted:
            return
        title, description, duedate = dialog.values()
        if not title:
            return
        self.controller.create_task(
            title, description, duedate, dialog.selected_label_ids()
        )

    def new_label(self) -> None:
        """Define a label on the board. It reaches the editor once Deck has it."""
        if not self.config.is_complete():
            self.open_settings()
            return
        dialog = NewLabelDialog(self._labels, self.config.board.title, self)
        if dialog.exec() != NewLabelDialog.DialogCode.Accepted:
            return
        title, color = dialog.values()
        if title:
            self.controller.create_label(title, color)

    def _on_card_activated(self, task: Task) -> None:
        """A click on a card, or Enter, opens the editor panel for it."""
        self._editing_task_id = task.id
        self.editor.open_task(task)

    def _on_editor_closed(self) -> None:
        """Hand the keyboard back to the board when the editor goes away.

        Without this, closing the editor leaves focus on a hidden widget and
        the next arrow key does nothing at all.
        """
        if self._editing_task_id is None:
            return
        if not self._focus_task(self._editing_task_id):
            self.focus_first_card()
        self._editing_task_id = None

    def _on_editor_discarded(self, task: Task) -> None:
        self.status_label.setText(f"Discarded changes to “{task.title}”")

    def _on_editor_save(
        self, task: Task, title: str, description: str, duedate, label_ids=None
    ) -> None:
        # Held so a refused save can put the text back in the panel.
        self._last_save_attempt = (task, title, description, duedate, label_ids)
        override = {"title": title, "description": description, "duedate": duedate}
        if label_ids is not None:
            by_id = {label.id: label.title for label in self._labels}
            override["label_ids"] = list(label_ids)
            override["labels"] = [
                by_id[label_id] for label_id in label_ids if label_id in by_id
            ]
        self._render_with_override(task.id, **override)
        self.controller.save_task(task, title, description, duedate, label_ids)

    def _on_save_failed(self, task: Task, reason: str) -> None:
        """Say that an edit did not reach the server, and hand it back.

        `_on_error` has already rolled the card back to what the server holds,
        which would otherwise take the typed text with it. So the attempted
        values go back into the editor: the work is still on screen to copy
        out, send again, or drop with Escape. Nothing is queued or retried
        behind the user's back — an edit that silently lands later is harder to
        reason about than one made again deliberately.
        """
        title = task.title or "(untitled)"
        message = f"Not saved: “{title}” — {reason}"
        self.status_label.setText(message)
        # _on_error has already flashed the bare reason over the status bar;
        # replace it with the one that says which card it was about.
        self.statusBar().showMessage(message, 8000)

        attempt = self._last_save_attempt
        restored = attempt is not None and attempt[0].id == task.id
        if restored:
            _, tried_title, tried_description, tried_due, tried_labels = attempt
            self.editor.open_task(
                task,
                title=tried_title,
                description=tried_description,
                duedate=tried_due,
                use_duedate=True,
                label_ids=tried_labels,
            )

        warning = QMessageBox(self)
        warning.setIcon(QMessageBox.Icon.Warning)
        warning.setWindowTitle("Could not save the task")
        warning.setText(f"“{title}” was not saved.")
        tail = (
            "Your changes are still in the editor, so you can copy them out or "
            "press Save to try again. Escape discards them."
            if restored
            else "Your changes were not applied."
        )
        warning.setInformativeText(f"{reason}\n\n{tail}")
        warning.setStandardButtons(QMessageBox.StandardButton.Ok)
        warning.exec()

    def _on_toggle_done(self, task: Task) -> None:
        target = not task.is_done
        self._render_with_override(
            task.id, done=datetime.now(timezone.utc) if target else None
        )
        self.controller.set_done(task, target)

    def _on_delete(self, task: Task) -> None:
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Delete task")
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setText(f"Delete “{task.title}”?")
        confirm.setInformativeText(
            "Nextcloud moves deleted cards to the board's trash, so you can "
            "still restore it from the Deck web interface."
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return
        self.controller.delete_task(task)

    def _on_edit_due(self, task: Task) -> None:
        dialog = DueDateDialog(task.title, task.duedate, self)
        if dialog.exec() != DueDateDialog.DialogCode.Accepted:
            return
        new_due = dialog.value()
        self._render_with_override(task.id, duedate=new_due)
        self.controller.set_duedate(task, new_due)

    def _on_task_dropped(self, task_id: int, bucket: Bucket) -> None:
        task = self._find(task_id)
        if task is None:
            return

        current = bucket_of(task, date.today(), self.config.ui.done_column_days)
        if current is bucket:
            return  # dropped back where it started

        if bucket is Bucket.DONE:
            self._render_with_override(task.id, done=datetime.now(timezone.utc))
            self.controller.set_done(task, True)
            return

        if bucket not in DROP_TARGET_OFFSETS:
            return

        new_due = reschedule_datetime(task, bucket)

        if task.is_done:
            # Dragging out of Done both reopens the task and dates it.
            self._render_with_override(task.id, done=None, duedate=new_due)
            self.controller.reopen_with_duedate(task, new_due)
        else:
            self._render_with_override(task.id, duedate=new_due)
            self.controller.set_duedate(task, new_due)

    def toolbar_first_focus(self) -> None:
        """Put focus on the first toolbar action, as Tab-ing in would."""
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_shortcuts(self) -> None:
        """Open the cheat sheet, reusing the window if it is already up."""
        existing = getattr(self, "_shortcuts_dialog", None)
        if existing is None:
            existing = ShortcutsDialog(self, self.config.ui.toggle_shortcut)
            self._shortcuts_dialog = existing
        existing.show()
        existing.raise_()
        existing.activateWindow()

    def open_settings(self) -> None:
        dialog = SettingsDialog(
            self.config, self.controller, self, config_path=self.config_path
        )
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return

        new_config = dialog.result_config()
        was_incomplete = not self.config.is_complete()
        old_shortcut = self.config.ui.toggle_shortcut
        self.config = new_config
        cfgmod.save(new_config, self.config_path)

        self.sync_desktop_hotkey(old_shortcut, new_config.ui.toggle_shortcut)

        from .. import autostart

        autostart.apply(new_config.autostart)

        self.controller.apply_config(new_config)
        apply_font_size(new_config.ui.font_size_pt)
        self.setWindowTitle(self._window_title())
        self._apply_refresh_interval()
        self.apply_toggle_shortcut(new_config.ui.toggle_shortcut)
        self._rebuild_columns_if_needed()

        if new_config.is_complete():
            self.controller.refresh()
            # The board may have changed, and labels belong to the board.
            self.controller.fetch_labels()
        elif was_incomplete:
            self.status_label.setText("Add your server details to load the board")

    def sync_desktop_hotkey(self, before: str, after: str) -> None:
        """Keep the desktop's global binding in step with the chosen key.

        Registering one is asked for, never assumed: it writes to the user's
        desktop configuration, which is not nextdeck's to change on a whim.
        Removing the binding nextdeck itself registered is not asked about —
        turning the key off and leaving a live global shortcut behind would be
        a bug, not caution.
        """
        from .. import hotkey as hotkeymod

        desk = hotkeymod.backend()
        if not after:
            if desk.name and desk.installed_sequence():
                removed = desk.remove()
                self.status_label.setText(
                    f"Removed the {desk.name} shortcut"
                    if removed
                    else f"Could not remove the {desk.name} shortcut"
                )
            return

        if not desk.name:
            # Nothing to offer: the settings dialog already shows the command
            # to bind by hand, and the shortcut list explains the rest.
            return

        # Compared against what is actually registered, not against what the
        # key used to be. A key already in config.yaml but never registered —
        # set before this existed, or on another machine — is the whole reason
        # this is here: watching only for a change would skip it forever.
        if desk.installed_sequence() == after:
            return
        if after == before and statemod.load(self.state_path).declined_hotkey == after:
            # Asked about this exact key before and told no. Leave it be.
            return

        command = hotkeymod.toggle_command(self.config_path)
        answer = QMessageBox.question(
            self,
            "Register the shortcut?",
            f"<b>{after}</b> only reaches nextdeck while nextdeck has the "
            "keyboard, so on its own it cannot bring the window back once it "
            f"is hidden.<br><br>Register it with {desk.name} as well, so it "
            "works from anywhere?"
            f"<br><br><small>{desk.name} will be set to run:<br>"
            f"<tt>{html.escape(command)}</tt></small>",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            statemod.remember_declined_hotkey(after, self.state_path)
            self.status_label.setText(
                f"{after} will work only while nextdeck is in front"
            )
            return

        if desk.install(after, command):
            # Clear any earlier "no" so the state is simply "registered".
            statemod.remember_declined_hotkey("", self.state_path)
            self.status_label.setText(f"{after} registered with {desk.name}")
            if not desk.went_live():
                # Written correctly and still dead is the one outcome nobody
                # would guess at, so it is said out loud rather than left in
                # the status line to be missed.
                QMessageBox.information(
                    self,
                    "Log out for the shortcut to start working",
                    f"{after} is registered with {desk.name}, but it will not "
                    "work until you log out and back in.<br><br>"
                    "<small>Nothing on this session can load it sooner: the "
                    "shortcut daemon is part of the compositor, and that "
                    "reads the shortcut file only when it starts.</small>",
                )
        else:
            QMessageBox.warning(
                self,
                "Could not register the shortcut",
                f"{desk.name} would not take the shortcut. You can add it by "
                "hand in your keyboard settings, running:\n\n"
                f"{command}",
            )

    def _rebuild_columns_if_needed(self) -> None:
        """Re-create the columns when the configured set or order changes."""
        if tuple(self.columns) != self._visible_buckets():
            self._build_columns()
        else:
            for column in self.columns.values():
                column.set_date_format(self.config.ui.date_format)
                column.set_title_lines(self.config.ui.card_title_lines)
        self._render(self._tasks)

    # -- window behaviour -------------------------------------------------

    def _restore_geometry(self) -> None:
        """Put the window back where it was, if that still makes sense.

        `restoreGeometry` refuses data it cannot use and checks the result
        against the screens that exist now, so a window saved on a monitor
        that has since been unplugged falls back to the default size rather
        than opening somewhere unreachable.
        """
        saved = statemod.load(self.state_path).geometry_bytes()
        if saved and self.restoreGeometry(saved):
            log.debug("restored the saved window geometry")

    def save_geometry(self) -> None:
        """Remember the window's size and position for next time.

        Read-modify-write: the state file holds more than the geometry now,
        and writing a fresh WindowState would quietly reset the rest of it.
        """
        state = statemod.load(self.state_path)
        state.geometry = statemod.WindowState.from_geometry_bytes(
            self.saveGeometry()
        ).geometry
        statemod.save(state, self.state_path)

    def prepare_quit(self) -> None:
        """Let the next close actually exit instead of hiding to the tray."""
        self._quitting = True
        self.save_geometry()

    def closeEvent(self, event: QCloseEvent) -> None:
        # Closing hides to the tray; only prepare_quit() makes it a real exit.
        # Without a tray there is nowhere to hide to, so let the close through.
        # Hiding to the tray is the usual way this window goes away, so it is
        # the main chance to record where it was.
        self.save_geometry()
        if not self._quitting and self._tray_available():
            event.ignore()
            self.hide()
            return
        event.accept()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Catches the window manager and the taskbar as well as our own menus.
        self._maybe_refresh_on_show()

    def _maybe_refresh_on_show(self) -> None:
        """Refresh because the board just came into view.

        Deliberately quiet about failing: this is a convenience on top of the
        timer, so a server that is down should not greet every glance at the
        window with a fresh complaint beyond the usual status line.
        """
        if not self.config.ui.refresh_on_show:
            return
        if not self.config.is_complete():
            return
        if not self._has_synced:
            # Startup kicks off its own first refresh once the config is known.
            return
        now = monotonic()
        if now - self._last_show_refresh < SHOW_REFRESH_COOLDOWN_S:
            # showEvent and show_and_raise can both fire for one toggle, and
            # flicking the window in and out is not a reason to hammer anyone.
            return
        self._last_show_refresh = now
        self.controller.refresh()

    def toggle_visibility(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_and_raise()

    def show_and_raise(self) -> None:
        # showEvent does not fire when a *minimised* window is restored, so
        # ask here too; the cooldown collapses the two into one request.
        self.showNormal()
        self._maybe_refresh_on_show()
        self.raise_()
        self.activateWindow()

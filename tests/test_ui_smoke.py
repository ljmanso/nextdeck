"""Offscreen UI smoke tests.

These need a working PySide6, so they skip where Qt cannot load (for example a
container without libGL). Run them with:

    QT_QPA_PLATFORM=offscreen pytest tests/test_ui_smoke.py
"""

from __future__ import annotations

import importlib
import os
from datetime import date, datetime, timedelta, timezone

import pytest

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError as exc:  # pragma: no cover - environment dependent
    # Covers both "PySide6 not installed" and a Qt that cannot load its shared
    # libraries (a container without libGL, for instance).
    pytest.skip(f"PySide6 is unusable here: {exc}", allow_module_level=True)

from PySide6.QtCore import QObject, QPointF, Qt, Signal  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nextdeck.config import BoardConfig, Config, ServerConfig, UIConfig  # noqa: E402
from nextdeck.model import BUCKET_ORDER, Bucket, Task  # noqa: E402
from nextdeck.ui.card import TASK_MIME  # noqa: E402
from nextdeck.ui.column import ColumnWidget  # noqa: E402
from nextdeck.ui.window import MainWindow  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TODAY = date.today()


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeController(QObject):
    """Stands in for SyncController, recording what the window asks for."""

    tasks_loaded = Signal(list)
    error = Signal(str)
    busy_changed = Signal(bool)
    mutated = Signal(str)
    save_failed = Signal(object, str)
    labels_loaded = Signal(list)
    connection_ok = Signal(list)
    stacks_loaded = Signal(list)

    def __init__(self):
        super().__init__()
        self.calls: list[tuple] = []

    def refresh(self):
        self.calls.append(("refresh",))

    def create_task(self, title, description="", duedate=None, label_ids=None):
        self.calls.append(("create_task", title, description, duedate))

    def create_label(self, title, color):
        self.calls.append(("create_label", title, color))

    def set_done(self, task, done):
        self.calls.append(("set_done", task.id, done))

    def set_duedate(self, task, duedate):
        self.calls.append(("set_duedate", task.id, duedate))

    def reopen_with_duedate(self, task, duedate):
        self.calls.append(("reopen_with_duedate", task.id, duedate))

    def delete_task(self, task):
        self.calls.append(("delete_task", task.id))

    def fetch_labels(self):
        self.calls.append(("fetch_labels",))

    def apply_config(self, config):
        self.calls.append(("apply_config",))

    def test_connection(self, config):
        self.calls.append(("test_connection",))

    def fetch_stacks(self, config, board_id):
        self.calls.append(("fetch_stacks", board_id))

    def stop(self):
        self.calls.append(("stop",))

    def named(self, name):
        return [call for call in self.calls if call[0] == name]


def make_config(**ui_kw) -> Config:
    return Config(
        server=ServerConfig(url="https://x.example", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2, title="Work"),
        ui=UIConfig(**ui_kw),
    )


def task(task_id: int, offset: int | None = None, done: bool = False) -> Task:
    due = None
    if offset is not None:
        due = datetime.combine(
            TODAY + timedelta(days=offset), datetime.min.time()
        ).replace(hour=9).astimezone()
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        board_id=1,
        stack_id=2,
        stack_title="ToDo",
        duedate=due,
        done=datetime.now(timezone.utc) if done else None,
        raw={"id": task_id, "stackId": 2, "title": f"Task {task_id}"},
    )


@pytest.fixture
def window(qapp):
    controller = FakeController()
    win = MainWindow(make_config(), controller, config_path=None)
    yield win, controller
    win.deleteLater()


# -- layout ---------------------------------------------------------------


def test_all_eight_columns_are_present_in_order(window):
    win, _ = window
    assert list(win.columns) == list(BUCKET_ORDER)


def test_done_column_can_be_switched_off(qapp):
    keys = [b.key for b in BUCKET_ORDER if b is not Bucket.DONE]
    win = MainWindow(make_config(visible_columns=keys), FakeController())
    assert Bucket.DONE not in win.columns
    assert len(win.columns) == len(BUCKET_ORDER) - 1


def test_overdue_column_refuses_drops_but_others_accept(window):
    win, _ = window
    assert not win.columns[Bucket.OVERDUE].acceptDrops()
    for bucket in (Bucket.TODAY, Bucket.TOMORROW, Bucket.NO_DUE_DATE, Bucket.DONE):
        assert win.columns[bucket].acceptDrops(), bucket


# -- rendering ------------------------------------------------------------


def test_tasks_land_in_the_expected_columns(window):
    win, controller = window
    controller.tasks_loaded.emit(
        [
            task(1, -3),
            task(2, 0),
            task(3, 1),
            task(4, 5),
            task(5, 20),
            task(6, 99),
            task(7, None),
            task(8, -1, done=True),
        ]
    )

    assert win.columns[Bucket.OVERDUE].task_ids() == [1]
    assert win.columns[Bucket.TODAY].task_ids() == [2]
    assert win.columns[Bucket.TOMORROW].task_ids() == [3]
    assert win.columns[Bucket.NEXT_7].task_ids() == [4]
    assert win.columns[Bucket.NEXT_30].task_ids() == [5]
    assert win.columns[Bucket.LATER].task_ids() == [6]
    assert win.columns[Bucket.NO_DUE_DATE].task_ids() == [7]
    assert win.columns[Bucket.DONE].task_ids() == [8], "done beats overdue"


def test_empty_board_reports_in_the_status_bar(window):
    win, controller = window
    controller.tasks_loaded.emit([])
    assert "No tasks" in win.status_label.text()
    assert all(not col.task_ids() for col in win.columns.values())


def test_done_card_title_is_struck_through(window):
    win, controller = window
    controller.tasks_loaded.emit([task(1, 0, done=True)])
    card = win.columns[Bucket.DONE].cards()[0]
    assert card.title_label.font().strikeOut()


# -- mutations ------------------------------------------------------------


def test_complete_button_requests_done(window):
    win, controller = window
    controller.tasks_loaded.emit([task(1, 0)])
    win.columns[Bucket.TODAY].cards()[0].done_button.click()
    assert controller.named("set_done") == [("set_done", 1, True)]


def test_completing_moves_the_card_immediately(window):
    """The optimistic render must not wait for the server."""
    win, controller = window
    controller.tasks_loaded.emit([task(1, 0)])
    win.columns[Bucket.TODAY].cards()[0].done_button.click()

    assert win.columns[Bucket.TODAY].task_ids() == []
    assert win.columns[Bucket.DONE].task_ids() == [1]


def test_reopen_button_requests_undone(window):
    win, controller = window
    controller.tasks_loaded.emit([task(1, 0, done=True)])
    win.columns[Bucket.DONE].cards()[0].done_button.click()
    assert controller.named("set_done") == [("set_done", 1, False)]


def test_an_error_rolls_the_optimistic_move_back(window):
    win, controller = window
    controller.tasks_loaded.emit([task(1, 0)])
    win.columns[Bucket.TODAY].cards()[0].done_button.click()
    assert win.columns[Bucket.DONE].task_ids() == [1]

    controller.error.emit("server said no")

    assert win.columns[Bucket.TODAY].task_ids() == [1], "reverted to server truth"
    assert win.columns[Bucket.DONE].task_ids() == []
    assert "server said no" in win.status_label.text()


# -- drag to reschedule ---------------------------------------------------


def test_drop_on_tomorrow_reschedules(window):
    win, controller = window
    controller.tasks_loaded.emit([task(1, -3)])

    win.columns[Bucket.TOMORROW].task_dropped.emit(1, Bucket.TOMORROW)

    calls = controller.named("set_duedate")
    assert len(calls) == 1
    assert calls[0][2].date() == TODAY + timedelta(days=1)
    assert win.columns[Bucket.TOMORROW].task_ids() == [1], "moves optimistically"


def test_drop_on_no_due_date_clears_the_date(window):
    win, controller = window
    controller.tasks_loaded.emit([task(1, 3)])
    win.columns[Bucket.NO_DUE_DATE].task_dropped.emit(1, Bucket.NO_DUE_DATE)
    assert controller.named("set_duedate") == [("set_duedate", 1, None)]


def test_drop_on_done_completes_the_task(window):
    win, controller = window
    controller.tasks_loaded.emit([task(1, 3)])
    win.columns[Bucket.DONE].task_dropped.emit(1, Bucket.DONE)
    assert controller.named("set_done") == [("set_done", 1, True)]


def test_dragging_out_of_done_reopens_and_dates_in_one_call(window):
    win, controller = window
    controller.tasks_loaded.emit([task(1, -5, done=True)])

    win.columns[Bucket.TODAY].task_dropped.emit(1, Bucket.TODAY)

    calls = controller.named("reopen_with_duedate")
    assert len(calls) == 1
    assert calls[0][2].date() == TODAY
    assert not controller.named("set_duedate"), "must not be two separate writes"


def test_dropping_a_card_back_where_it_started_is_a_no_op(window):
    win, controller = window
    controller.tasks_loaded.emit([task(1, 0)])
    win.columns[Bucket.TODAY].task_dropped.emit(1, Bucket.TODAY)
    assert controller.named("set_duedate") == []
    assert controller.named("set_done") == []


def test_dropping_an_unknown_card_is_ignored(window):
    win, controller = window
    controller.tasks_loaded.emit([task(1, 0)])
    win.columns[Bucket.TOMORROW].task_dropped.emit(999, Bucket.TOMORROW)
    assert controller.named("set_duedate") == []


def test_rescheduled_card_reappears_in_the_column_it_was_dropped_on(window):
    win, controller = window
    for bucket in (Bucket.TODAY, Bucket.TOMORROW, Bucket.NEXT_7, Bucket.NEXT_30):
        controller.tasks_loaded.emit([task(1, -4)])
        win.columns[bucket].task_dropped.emit(1, bucket)
        assert win.columns[bucket].task_ids() == [1], bucket


# -- the MIME round trip --------------------------------------------------


def test_card_drag_payload_round_trips_through_a_real_drop_event(qapp):
    """Verifies the drag format the card writes is the one the column reads."""
    from PySide6.QtCore import QMimeData

    column = ColumnWidget(Bucket.TOMORROW)
    received: list[tuple] = []
    column.task_dropped.connect(lambda tid, bucket: received.append((tid, bucket)))

    mime = QMimeData()
    mime.setData(TASK_MIME, b"42")
    event = QDropEvent(
        QPointF(5, 5),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    column.dropEvent(event)

    assert received == [(42, Bucket.TOMORROW)]
    column.deleteLater()


def test_a_foreign_drop_is_ignored(qapp):
    from PySide6.QtCore import QMimeData

    column = ColumnWidget(Bucket.TOMORROW)
    received: list[tuple] = []
    column.task_dropped.connect(lambda tid, bucket: received.append((tid, bucket)))

    mime = QMimeData()
    mime.setText("some dragged text")
    event = QDropEvent(
        QPointF(5, 5),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    column.dropEvent(event)

    assert received == []
    column.deleteLater()


# -- tray -----------------------------------------------------------------


def test_tray_is_quiet(qapp):
    """No counts, no badge: the tray must not nag."""
    from nextdeck.ui.tray import TrayIcon

    tray = TrayIcon()
    assert tray.toolTip() == "nextdeck"
    labels = [action.text() for action in tray.contextMenu().actions()]
    assert "Show / hide" in labels
    assert "Quit nextdeck" in labels
    assert not any(char.isdigit() for char in tray.toolTip())


def test_window_hides_rather_than_closing_when_a_tray_exists(window):
    win, _ = window
    win._tray_available = lambda: True
    win.show()
    win.close()
    assert not win.isVisible(), "hidden, but still alive"

    win.prepare_quit()
    assert win.close(), "after prepare_quit the close is real"

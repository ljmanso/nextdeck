"""Working the board without a mouse.

Driven with real key events through QTest wherever possible: calling the
handlers directly would pass even if nothing were bound to a key.
"""

from __future__ import annotations

import importlib
import os
from datetime import date, datetime, timedelta, timezone

import pytest

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"PySide6 is unusable here: {exc}", allow_module_level=True)

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nextdeck.config import BoardConfig, Config, ServerConfig, UIConfig  # noqa: E402
from nextdeck.model import Bucket, Task, shift_duedate  # noqa: E402
from nextdeck.ui.window import MainWindow  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TODAY = date.today()
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT_CTRL = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class StubController(QObject):
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
        pass

    def set_done(self, task, done):
        self.calls.append(("set_done", task.id, done))

    def set_duedate(self, task, duedate):
        self.calls.append(("set_duedate", task.id, duedate))

    def delete_task(self, task):
        self.calls.append(("delete_task", task.id))

    def stop(self):
        pass

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


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
        raw={"id": task_id, "stackId": 2},
    )


@pytest.fixture
def board(qapp, tmp_path):
    config = Config(
        server=ServerConfig(url="https://x.example", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2, title="Work"),
        ui=UIConfig(),
    )
    controller = StubController()
    win = MainWindow(config, controller, config_path=None, state_path=tmp_path / "s.yaml")
    win.show()
    qapp.processEvents()
    yield win, controller
    win.deleteLater()


def focused(win):
    return win._focused_card()


def press(win, key, modifier=NONE):
    QTest.keyClick(focused(win), key, modifier)
    QApplication.processEvents()


# -- moving around --------------------------------------------------------


def test_the_first_load_puts_focus_on_a_card(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0), task(2, 1)])
    qapp.processEvents()

    assert focused(win) is not None
    assert focused(win).task.id == 1


def test_a_later_refresh_does_not_steal_focus(board, qapp):
    """Focus must stay where the user put it when the timer fires."""
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0), task(2, 0)])
    qapp.processEvents()
    press(win, Qt.Key.Key_Down)
    assert focused(win).task.id == 2

    controller.tasks_loaded.emit([task(1, 0), task(2, 0)])
    qapp.processEvents()

    assert focused(win).task.id == 2, "the refresh moved the focus"


def test_down_and_up_move_within_a_column(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0), task(2, 0), task(3, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Down)
    assert focused(win).task.id == 2
    press(win, Qt.Key.Key_Down)
    assert focused(win).task.id == 3
    press(win, Qt.Key.Key_Up)
    assert focused(win).task.id == 2


def test_navigation_clamps_rather_than_wrapping(board, qapp):
    """At the top, Up should do nothing, not leap to the bottom."""
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0), task(2, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Up)
    assert focused(win).task.id == 1

    press(win, Qt.Key.Key_Down)
    press(win, Qt.Key.Key_Down)
    assert focused(win).task.id == 2


def test_left_and_right_move_between_columns(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0), task(2, 1)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Right)
    assert focused(win).task.id == 2, "should be in Tomorrow"
    press(win, Qt.Key.Key_Left)
    assert focused(win).task.id == 1


def test_sideways_movement_skips_empty_columns(board, qapp):
    """Landing on an empty column would strand the focus."""
    win, controller = board
    # Today and Next 30 days, with Tomorrow and Next 7 days empty between.
    controller.tasks_loaded.emit([task(1, 0), task(2, 20)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Right)

    assert focused(win).task.id == 2


def test_moving_past_the_last_column_does_nothing(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Right)

    assert focused(win).task.id == 1


def test_sideways_keeps_the_row_where_it_can(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit(
        [task(1, 0), task(2, 0), task(3, 1), task(4, 1)]
    )
    qapp.processEvents()
    press(win, Qt.Key.Key_Down)
    assert focused(win).task.id == 2, "second card in Today"

    press(win, Qt.Key.Key_Right)

    assert focused(win).task.id == 4, "second card in Tomorrow"


def test_sideways_onto_a_shorter_column_lands_on_its_last_card(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0), task(2, 0), task(3, 1)])
    qapp.processEvents()
    press(win, Qt.Key.Key_Down)

    press(win, Qt.Key.Key_Right)

    assert focused(win).task.id == 3


def test_home_and_end(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0), task(2, 0), task(3, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_End)
    assert focused(win).task.id == 3
    press(win, Qt.Key.Key_Home)
    assert focused(win).task.id == 1


# -- acting on the focused card -------------------------------------------


def test_space_completes_the_focused_card(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Space)

    assert controller.named("set_done") == [("set_done", 1, True)]


def test_space_reopens_a_completed_card(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, -1, done=True)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Space)

    assert controller.named("set_done") == [("set_done", 1, False)]


def test_enter_opens_the_editor(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Return)

    assert not win.editor.isHidden()
    assert win.editor.task.id == 1


def test_closing_the_editor_returns_focus_to_the_board(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0), task(2, 0)])
    qapp.processEvents()
    press(win, Qt.Key.Key_Down)
    press(win, Qt.Key.Key_Return)
    assert not win.editor.isHidden()

    win.editor.cancel()
    qapp.processEvents()

    assert focused(win) is not None, "arrow keys would be dead otherwise"
    assert focused(win).task.id == 2, "and back on the card that was edited"


def test_delete_asks_before_deleting(board, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    win, controller = board
    controller.tasks_loaded.emit([task(1, 0)])
    qapp.processEvents()
    asked: list[int] = []

    def fake_exec(self):
        asked.append(1)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    press(win, Qt.Key.Key_Delete)

    assert asked, "deleting from the keyboard must still confirm"
    assert controller.named("delete_task") == [("delete_task", 1)]


# -- nudging the due date -------------------------------------------------


def due_of(call):
    return call[2]


def test_ctrl_right_moves_the_due_date_a_day_later(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Right, CTRL)

    calls = controller.named("set_duedate")
    assert len(calls) == 1
    assert due_of(calls[0]).date() == TODAY + timedelta(days=1)


def test_ctrl_left_moves_it_a_day_earlier(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 5)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Left, CTRL)

    assert due_of(controller.named("set_duedate")[0]).date() == (
        TODAY + timedelta(days=4)
    )


def test_shift_ctrl_moves_a_week(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Right, SHIFT_CTRL)

    assert due_of(controller.named("set_duedate")[0]).date() == (
        TODAY + timedelta(days=7)
    )


def test_the_time_of_day_is_kept(board, qapp):
    """A 09:00 meeting pushed to tomorrow is still at 09:00."""
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Right, CTRL)

    moved = due_of(controller.named("set_duedate")[0]).astimezone()
    assert (moved.hour, moved.minute) == (9, 0)


def test_the_card_moves_column_immediately(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Right, CTRL)

    assert win.columns[Bucket.TODAY].task_ids() == []
    assert win.columns[Bucket.TOMORROW].task_ids() == [1]


def test_repeated_nudges_stack(board, qapp):
    """Each press must count from where the last one left the card.

    Server truth only catches up after the write and a refresh, so counting
    from it would make the second press land on the same day as the first.
    """
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Right, CTRL)
    assert focused(win) is not None and focused(win).task.id == 1, (
        "focus must follow the card, or the next press goes nowhere"
    )

    press(win, Qt.Key.Key_Right, CTRL)
    assert due_of(controller.named("set_duedate")[1]).date() == (
        TODAY + timedelta(days=2)
    )
    assert win.columns[Bucket.NEXT_7].task_ids() == [1]

    press(win, Qt.Key.Key_Left, CTRL)
    assert due_of(controller.named("set_duedate")[2]).date() == (
        TODAY + timedelta(days=1)
    )


def test_a_card_can_be_pushed_into_the_past(board, qapp):
    """Stepping back is deliberate, unlike dropping onto Overdue."""
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Left, CTRL)

    assert due_of(controller.named("set_duedate")[0]).date() == (
        TODAY - timedelta(days=1)
    )
    assert win.columns[Bucket.OVERDUE].task_ids() == [1]


def test_a_card_with_no_due_date_is_treated_as_due_today(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, None)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Right, CTRL)

    assert due_of(controller.named("set_duedate")[0]).date() == (
        TODAY + timedelta(days=1)
    )
    assert win.columns[Bucket.TOMORROW].task_ids() == [1]


def test_overdue_still_refuses_a_drop(board, qapp):
    """The keyboard gains precision; dragging keeps its old rule."""
    win, _ = board
    assert not win.columns[Bucket.OVERDUE].acceptDrops()


def test_the_status_line_reports_the_new_date(board, qapp):
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0)])
    qapp.processEvents()

    press(win, Qt.Key.Key_Right, CTRL)

    expected = (
        shift_duedate(task(1, 0), 1).astimezone().strftime(win.config.ui.date_format)
    )
    assert expected in win.status_label.text()


# -- the cheat sheet ------------------------------------------------------


def test_f1_opens_the_shortcuts_dialog(board, qapp):
    win, _ = board
    QTest.keyClick(win, Qt.Key.Key_F1)
    qapp.processEvents()

    dialog = win._shortcuts_dialog
    assert dialog is not None and not dialog.isHidden()
    dialog.close()


def test_the_cheat_sheet_lists_every_board_key(board, qapp):
    from nextdeck.ui.shortcuts_dialog import SHORTCUTS

    listed = " ".join(keys for _, rows in SHORTCUTS for keys, _ in rows)
    for expected in ("Enter", "Space", "Delete", "Home", "Ctrl", "F1"):
        assert expected in listed, expected


def test_reopening_the_cheat_sheet_reuses_one_window(board, qapp):
    win, _ = board
    win.show_shortcuts()
    first = win._shortcuts_dialog
    win.show_shortcuts()

    assert win._shortcuts_dialog is first
    first.close()


# -- focus is reachable and visible ---------------------------------------


def test_tab_can_reach_the_board(board, qapp):
    """setFocus() works whatever the policy; Tab does not.

    Without a tab-accepting focus policy the cards are unreachable from the
    keyboard alone — you could never get onto the board in the first place.
    """
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0)])
    qapp.processEvents()
    win.toolbar_first_focus()

    from nextdeck.ui.card import CardWidget

    for _ in range(60):
        win.focusNextChild()
        if isinstance(QApplication.focusWidget(), CardWidget):
            return
    pytest.fail("Tab never reached a card")


def test_the_focused_card_looks_different(board, qapp, tmp_path):
    """A focus ring you cannot see is no use at all."""
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0), task(2, 0)])
    qapp.processEvents()

    first, second = win.columns[Bucket.TODAY].cards()
    # The *same* card before and after: two different cards would differ
    # anyway, because their titles differ.
    second.clearFocus()
    first.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()
    plain_png = tmp_path / "plain.png"
    assert second.grab().save(str(plain_png))

    second.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()
    focused_png = tmp_path / "focused.png"
    assert second.grab().save(str(focused_png))

    assert focused_png.read_bytes() != plain_png.read_bytes(), (
        "the focused card renders identically to an unfocused one"
    )


def test_a_rerender_does_not_strand_the_keyboard(board, qapp):
    """Clearing a column must not reparent the focused card into a window.

    setParent(None) makes a widget a top-level window. Doing that to the card
    that currently has focus drops the focus and deactivates the real window,
    so the next keypress goes nowhere.
    """
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0), task(2, 0)])
    qapp.processEvents()
    assert focused(win) is not None
    was_active = win.isActiveWindow()

    # A refresh that rebuilds every card while one of them holds the focus.
    controller.tasks_loaded.emit([task(1, 0), task(2, 0), task(3, 0)])
    qapp.processEvents()

    assert focused(win) is not None, "focus was lost when the cards were rebuilt"
    assert win.isActiveWindow() == was_active, "the window was deactivated"

    press(win, Qt.Key.Key_Down)
    assert focused(win).task.id == 2, "and the keyboard still works"


def test_focus_survives_the_focused_card_disappearing(board, qapp):
    """Completing the focused card must leave the keyboard somewhere useful."""
    win, controller = board
    controller.tasks_loaded.emit([task(1, 0), task(2, 0)])
    qapp.processEvents()

    # Only Today is visible to the filter, so completing moves it to Done.
    controller.tasks_loaded.emit([task(2, 0)])
    qapp.processEvents()

    assert focused(win) is not None
    assert focused(win).task.id == 2

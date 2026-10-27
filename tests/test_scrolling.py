"""The focused card must be on screen.

Two scroll areas can hide it: the board scrolls sideways across the columns,
and each column scrolls down its own cards. Moving the keyboard focus, or
nudging a due date so a card lands in a column that is scrolled elsewhere,
both used to leave the focused card out of sight.

The window is deliberately made too small for its contents here; a board that
fits has nothing to scroll and would pass whatever the code did.
"""

from __future__ import annotations

import importlib
import os
from datetime import date, datetime, timedelta

import pytest

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"PySide6 is unusable here: {exc}", allow_module_level=True)

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nextdeck.config import BoardConfig, Config, ServerConfig, UIConfig  # noqa: E402
from nextdeck.model import Bucket, Task  # noqa: E402
from nextdeck.ui.window import MainWindow  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TODAY = date.today()
CTRL = Qt.KeyboardModifier.ControlModifier

#: Small enough that neither axis fits, on the 800x800 offscreen screen.
CRAMPED = (620, 300)


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

    def refresh(self):
        pass

    def set_duedate(self, task, duedate):
        pass

    def stop(self):
        pass


def task(task_id: int, offset: int | None, hour: int = 9) -> Task:
    due = None
    if offset is not None:
        due = datetime.combine(
            TODAY + timedelta(days=offset), datetime.min.time()
        ).replace(hour=hour).astimezone()
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        board_id=1,
        stack_id=2,
        stack_title="ToDo",
        duedate=due,
        done=None,
        raw={"id": task_id, "stackId": 2},
    )


@pytest.fixture
def board(qapp, tmp_path):
    config = Config(
        server=ServerConfig(url="https://x.example", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2, title="Work"),
        ui=UIConfig(),
    )
    win = MainWindow(
        config, StubController(), config_path=None, state_path=tmp_path / "s.yaml"
    )
    win.resize(*CRAMPED)
    win.show()
    for _ in range(4):
        qapp.processEvents()
    yield win
    win.deleteLater()


def load(win, qapp, tasks):
    win._on_tasks_loaded(tasks)
    for _ in range(4):
        qapp.processEvents()


def vbar(win, bucket):
    return win.columns[bucket]._scroll.verticalScrollBar()


def hbar(win):
    return win._board_scroll.horizontalScrollBar()


def fully_visible(win, card) -> bool:
    """True when every corner of `card` is inside both viewports."""
    for column in win.columns.values():
        if card in column.cards():
            inner = column._scroll.viewport()
            outer = win._board_scroll.viewport()
            rect = card.rect()
            return all(
                inner.rect().contains(card.mapTo(inner, corner))
                and outer.rect().contains(card.mapTo(outer, corner))
                for corner in (rect.topLeft(), rect.bottomRight())
            )
    return False


# -- the fixture has to actually overflow, or nothing below means anything


def test_the_board_really_is_too_small_for_its_contents(board, qapp):
    win = board
    load(win, qapp, [task(i, 0) for i in range(1, 13)])
    assert vbar(win, Bucket.TODAY).maximum() > 0, "the column should overflow"
    assert hbar(win).maximum() > 0, "the board should overflow sideways"


# -- moving the focus -----------------------------------------------------


def test_moving_down_a_long_column_scrolls_it(board, qapp):
    win = board
    load(win, qapp, [task(i, 0) for i in range(1, 13)])
    cards = win.columns[Bucket.TODAY].cards()
    win._focus_card(cards[0])
    qapp.processEvents()
    assert vbar(win, Bucket.TODAY).value() == 0

    for _ in range(len(cards) - 1):
        QTest.keyClick(win._focused_card(), Qt.Key.Key_Down)
        qapp.processEvents()

    assert win._focused_card().task.id == cards[-1].task.id
    assert vbar(win, Bucket.TODAY).value() > 0, "the column never scrolled"
    assert fully_visible(win, win._focused_card())


def test_moving_back_up_scrolls_the_other_way(board, qapp):
    win = board
    load(win, qapp, [task(i, 0) for i in range(1, 13)])
    cards = win.columns[Bucket.TODAY].cards()
    win._focus_card(cards[-1])
    qapp.processEvents()
    assert vbar(win, Bucket.TODAY).value() > 0

    win._focus_card(win.columns[Bucket.TODAY].cards()[0])
    qapp.processEvents()
    assert vbar(win, Bucket.TODAY).value() == 0
    assert fully_visible(win, win._focused_card())


def test_moving_sideways_scrolls_the_board_to_the_column(board, qapp):
    win = board
    load(win, qapp, [task(1, 0), task(2, 3), task(3, 20), task(4, None)])
    win._focus_card(win.columns[Bucket.TODAY].cards()[0])
    qapp.processEvents()
    assert hbar(win).value() == 0

    seen = []
    for _ in range(3):
        QTest.keyClick(win._focused_card(), Qt.Key.Key_Right)
        qapp.processEvents()
        seen.append(hbar(win).value())
        assert fully_visible(win, win._focused_card())

    assert seen == sorted(seen) and seen[-1] > 0, f"board never scrolled: {seen}"


def test_a_card_already_on_screen_does_not_move_the_scroll(board, qapp):
    """Revealing must be a no-op when there is nothing to reveal."""
    win = board
    load(win, qapp, [task(i, 0) for i in range(1, 13)])
    cards = win.columns[Bucket.TODAY].cards()
    win._focus_card(cards[0])
    qapp.processEvents()
    before = vbar(win, Bucket.TODAY).value(), hbar(win).value()

    win._focus_card(cards[1])
    qapp.processEvents()
    assert (vbar(win, Bucket.TODAY).value(), hbar(win).value()) == before


# -- nudging a due date ---------------------------------------------------


def test_a_nudge_into_an_offscreen_column_scrolls_to_it(board, qapp):
    """Ctrl+Right moves a card a day on, which can change its column."""
    win = board
    load(win, qapp, [task(1, 0)] + [task(i, 30) for i in range(2, 8)])
    win._focus_card(win.columns[Bucket.TODAY].cards()[0])
    qapp.processEvents()
    assert hbar(win).value() == 0

    # Today -> Tomorrow -> Next 7, each a column further right.
    for _ in range(3):
        QTest.keyClick(win._focused_card(), Qt.Key.Key_Right, CTRL)
        qapp.processEvents()

    card = win._focused_card()
    assert card is not None and card.task.id == 1, "focus followed the card"
    assert hbar(win).value() > 0, "the board did not follow it"
    assert fully_visible(win, card)


def test_a_nudge_into_a_scrolled_column_scrolls_that_column_too(board, qapp):
    """The target column may be scrolled down from an earlier visit."""
    win = board
    tasks = [task(1, 0)] + [task(i, 1) for i in range(2, 14)]
    load(win, qapp, tasks)

    tomorrow = win.columns[Bucket.TOMORROW]
    win._focus_card(tomorrow.cards()[-1])
    qapp.processEvents()
    assert vbar(win, Bucket.TOMORROW).value() > 0, "Tomorrow should be scrolled down"

    win._focus_card(win.columns[Bucket.TODAY].cards()[0])
    qapp.processEvents()
    QTest.keyClick(win._focused_card(), Qt.Key.Key_Right, CTRL)
    qapp.processEvents()

    card = win._focused_card()
    assert card is not None and card.task.id == 1
    assert card in win.columns[Bucket.TOMORROW].cards(), "it moved column"
    assert fully_visible(win, card)


def test_clearing_a_search_keeps_the_focused_card_in_view(board, qapp):
    """Filtering rebuilds every card; the survivor must still be reachable."""
    win = board
    load(win, qapp, [task(i, 0) for i in range(1, 13)])
    cards = win.columns[Bucket.TODAY].cards()
    win._focus_card(cards[-1])
    qapp.processEvents()

    win.search_for("Task 12")
    qapp.processEvents()
    card = win._focused_card()
    assert card is not None and fully_visible(win, card)

    win.clear_search()
    for _ in range(3):
        qapp.processEvents()
    card = win._focused_card()
    assert card is not None and fully_visible(win, card)


def test_a_nudge_to_the_bottom_of_a_long_column_scrolls_down_to_it(board, qapp):
    """The case a rebuilt board gets wrong without waiting for the layout.

    The nudged card is due late in the day, so it sorts to the *end* of a
    column long enough to need scrolling. Straight after the rebuild the new
    cards have no useful geometry yet, so a reveal computed there scrolls
    nowhere and leaves the card below the fold.
    """
    win = board
    load(
        win,
        qapp,
        [task(1, 0, hour=23)] + [task(i, 1) for i in range(2, 20)],
    )
    tomorrow = win.columns[Bucket.TOMORROW]
    bar = tomorrow._scroll.verticalScrollBar()
    assert bar.maximum() > 0, "Tomorrow has to be long enough to scroll"

    win._focus_card(win.columns[Bucket.TODAY].cards()[0])
    qapp.processEvents()
    QTest.keyClick(win._focused_card(), Qt.Key.Key_Right, CTRL)
    for _ in range(3):
        qapp.processEvents()

    card = win._focused_card()
    assert card is not None and card.task.id == 1
    assert card is tomorrow.cards()[-1], "it should have sorted to the bottom"
    assert bar.value() > 0, "the column never scrolled down to it"
    assert fully_visible(win, card)


def test_a_revealed_card_is_not_flush_against_the_edge(board, qapp):
    """Scrolling a card to exactly the viewport edge reads as cut off."""
    from nextdeck.ui.column import CARD_MARGIN

    win = board
    load(win, qapp, [task(i, 0) for i in range(1, 13)])
    column = win.columns[Bucket.TODAY]
    cards = column.cards()
    win._focus_card(cards[0])
    qapp.processEvents()

    # Walk down until the column has to scroll to keep up.
    viewport = column._scroll.viewport()
    while vbar(win, Bucket.TODAY).value() == 0:
        QTest.keyClick(win._focused_card(), Qt.Key.Key_Down)
        qapp.processEvents()
        assert win._focused_card() is not cards[-1], "never needed to scroll"

    card = win._focused_card()
    bottom = card.mapTo(viewport, card.rect().bottomLeft()).y()
    assert viewport.height() - bottom >= CARD_MARGIN, (
        f"only {viewport.height() - bottom}px below the card"
    )

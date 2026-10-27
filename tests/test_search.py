"""Filtering the board.

Once a board has a hundred cards, finding one means reading eight columns.
"""

from __future__ import annotations

import importlib
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from nextdeck.model import Task, task_matches

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"PySide6 is unusable here: {exc}", allow_module_level=True)

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nextdeck.config import BoardConfig, Config, ServerConfig, UIConfig  # noqa: E402
from nextdeck.model import Bucket  # noqa: E402
from nextdeck.ui.window import MainWindow  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TODAY = date.today()


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

    def stop(self):
        pass


def task(
    task_id: int,
    title: str = "Task",
    offset: int | None = 0,
    description: str = "",
    labels: list[str] | None = None,
    stack: str = "ToDo",
    done: bool = False,
) -> Task:
    due = None
    if offset is not None:
        due = datetime.combine(
            TODAY + timedelta(days=offset), datetime.min.time()
        ).replace(hour=9).astimezone()
    return Task(
        id=task_id,
        title=title,
        board_id=1,
        stack_id=2,
        stack_title=stack,
        duedate=due,
        done=datetime.now(timezone.utc) if done else None,
        description=description,
        labels=labels or [],
        raw={"id": task_id, "stackId": 2},
    )


# -- the matcher (no Qt) --------------------------------------------------


def test_an_empty_query_matches_everything():
    assert task_matches(task(1, "Anything"), "")
    assert task_matches(task(1, "Anything"), "   ")


def test_the_title_is_matched():
    assert task_matches(task(1, "Draft the migration plan"), "migration")


def test_matching_ignores_case():
    assert task_matches(task(1, "Draft the Migration plan"), "MIGRATION")


def test_the_details_are_matched():
    assert task_matches(task(1, "Team sync", description="the migration timeline"),
                        "migration")


def test_labels_are_matched():
    assert task_matches(task(1, "Renew", labels=["migration"]), "migration")


def test_the_stack_name_is_matched():
    assert task_matches(task(1, "Read the RFC", stack="Migration"), "migration")


def test_a_non_match_is_rejected():
    assert not task_matches(task(1, "Buy milk"), "migration")


def test_every_term_has_to_match():
    """Adding a word must narrow the result, not widen it."""
    subject = task(1, "Draft the migration plan", description="for the database")

    assert task_matches(subject, "migration database")
    assert not task_matches(subject, "migration kubernetes")


def test_terms_can_match_different_fields():
    subject = task(1, "Draft the plan", labels=["urgent"], stack="Migration")
    assert task_matches(subject, "plan urgent migration")


def test_a_partial_word_matches():
    assert task_matches(task(1, "Draft the migration plan"), "migr")


# -- the board ------------------------------------------------------------


@pytest.fixture
def board(qapp, tmp_path):
    config = Config(
        server=ServerConfig(url="https://x.example", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2, title="Work"),
        ui=UIConfig(),
    )
    controller = StubController()
    win = MainWindow(
        config, controller, config_path=None, state_path=tmp_path / "s.yaml"
    )
    win.show()
    qapp.processEvents()
    yield win, controller
    win.deleteLater()


SAMPLE = [
    ("Draft the migration plan", 0, "", []),
    ("Reply to Ana", 0, "", []),
    ("Team sync", 1, "discuss the migration timeline", []),
    ("Renew the domain", -2, "", ["migration"]),
    ("Buy milk", 5, "", []),
]


def load_sample(controller):
    controller.tasks_loaded.emit(
        [
            task(i, title, offset=offset, description=desc, labels=labels)
            for i, (title, offset, desc, labels) in enumerate(SAMPLE, start=1)
        ]
    )


def visible_ids(win) -> set[int]:
    return {
        card.task.id for column in win.columns.values() for card in column.cards()
    }


def test_typing_filters_the_board(board, qapp):
    win, controller = board
    load_sample(controller)
    qapp.processEvents()
    assert len(visible_ids(win)) == 5

    QTest.keyClicks(win.search_edit, "migration")
    qapp.processEvents()

    assert visible_ids(win) == {1, 3, 4}


def test_non_matching_cards_are_gone_not_dimmed(board, qapp):
    win, controller = board
    load_sample(controller)
    QTest.keyClicks(win.search_edit, "migration")
    qapp.processEvents()

    assert 2 not in visible_ids(win), "Reply to Ana should be filtered out"
    assert win.columns[Bucket.NEXT_7].task_ids() == []


def test_the_columns_still_group_by_due_date(board, qapp):
    """Filtering must not flatten the board into one list."""
    win, controller = board
    load_sample(controller)
    QTest.keyClicks(win.search_edit, "migration")
    qapp.processEvents()

    assert win.columns[Bucket.TODAY].task_ids() == [1]
    assert win.columns[Bucket.TOMORROW].task_ids() == [3]
    assert win.columns[Bucket.OVERDUE].task_ids() == [4]


def test_emptied_columns_collapse(board, qapp):
    win, controller = board
    load_sample(controller)
    QTest.keyClicks(win.search_edit, "migration")
    qapp.processEvents()

    assert win.columns[Bucket.NEXT_7].is_compact


def test_the_status_bar_says_how_many_are_hidden(board, qapp):
    win, controller = board
    load_sample(controller)
    QTest.keyClicks(win.search_edit, "migration")
    qapp.processEvents()

    assert win.status_label.text() == "3 of 5 tasks shown"


def test_a_query_matching_nothing_says_so(board, qapp):
    win, controller = board
    load_sample(controller)
    QTest.keyClicks(win.search_edit, "kubernetes")
    qapp.processEvents()

    assert visible_ids(win) == set()
    assert "Nothing matches" in win.status_label.text()
    assert "kubernetes" in win.status_label.text()


def test_clearing_brings_everything_back(board, qapp):
    win, controller = board
    load_sample(controller)
    QTest.keyClicks(win.search_edit, "migration")
    qapp.processEvents()

    win.search_edit.clear()
    qapp.processEvents()

    assert len(visible_ids(win)) == 5
    assert win.status_label.text() == "5 tasks"


def test_a_refresh_keeps_the_filter_applied(board, qapp):
    """The timer must not quietly undo what you are looking at."""
    win, controller = board
    load_sample(controller)
    QTest.keyClicks(win.search_edit, "migration")
    qapp.processEvents()

    load_sample(controller)
    qapp.processEvents()

    assert visible_ids(win) == {1, 3, 4}
    assert win.status_label.text() == "3 of 5 tasks shown"


def test_ctrl_f_focuses_the_box(board, qapp):
    win, controller = board
    load_sample(controller)
    qapp.processEvents()

    QTest.keyClick(win, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()

    assert QApplication.focusWidget() is win.search_edit


def test_ctrl_f_selects_what_is_already_there(board, qapp):
    """So a second search replaces the first instead of appending to it."""
    win, controller = board
    load_sample(controller)
    win.search_edit.setText("migration")
    qapp.processEvents()

    win.focus_search()

    assert win.search_edit.selectedText() == "migration"


def test_escape_clears_the_filter_and_leaves_the_box(board, qapp):
    win, controller = board
    load_sample(controller)
    QTest.keyClicks(win.search_edit, "migration")
    qapp.processEvents()

    QTest.keyClick(win.search_edit, Qt.Key.Key_Escape)
    qapp.processEvents()

    assert win.search_edit.text() == ""
    assert len(visible_ids(win)) == 5
    assert win._focused_card() is not None, "the keyboard must come back to the board"


def test_enter_moves_to_the_results(board, qapp):
    win, controller = board
    load_sample(controller)
    QTest.keyClicks(win.search_edit, "migration")
    qapp.processEvents()

    QTest.keyClick(win.search_edit, Qt.Key.Key_Return)
    qapp.processEvents()

    card = win._focused_card()
    assert card is not None and card.task.id in {1, 3, 4}


def test_arrow_keys_only_walk_the_matches(board, qapp):
    """Navigation must not reach a card the filter removed."""
    win, controller = board
    load_sample(controller)
    QTest.keyClicks(win.search_edit, "migration")
    qapp.processEvents()
    win.focus_first_card()

    seen = set()
    for _ in range(6):
        card = win._focused_card()
        assert card is not None
        seen.add(card.task.id)
        QTest.keyClick(card, Qt.Key.Key_Right)
        qapp.processEvents()

    assert seen == {1, 3, 4}


def test_filtering_does_not_touch_the_server_copy(board, qapp):
    """A filter is a view, not a deletion."""
    win, controller = board
    load_sample(controller)
    QTest.keyClicks(win.search_edit, "migration")
    qapp.processEvents()

    assert len(win._tasks) == 5

"""Column sizing and header presentation.

Covers the three things logic tests kept missing: that columns actually grow
into unused window width, that an empty column stops reserving a full lane,
and that the card count reads as an aside rather than part of the column name.
"""

from __future__ import annotations

import importlib
import os
import re
from datetime import date, datetime, timedelta, timezone

import pytest

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"PySide6 is unusable here: {exc}", allow_module_level=True)

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nextdeck.config import BoardConfig, Config, ServerConfig, UIConfig  # noqa: E402
from nextdeck.model import Bucket, Task  # noqa: E402
from nextdeck.ui.column import ColumnWidget  # noqa: E402
from nextdeck.ui.window import MainWindow  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TODAY = date.today()
#: Wide enough that three columns cannot possibly fill it at their floor width.
WIDE = 1600


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


def make_window(qapp, columns: list[str], **ui_kw) -> MainWindow:
    config = Config(
        server=ServerConfig(url="https://x.example", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2, title="Work"),
        ui=UIConfig(visible_columns=columns, **ui_kw),
    )
    win = MainWindow(config, StubController(), config_path=None)
    win.resize(WIDE, 800)
    win.show()
    qapp.processEvents()
    return win


# -- the header count -----------------------------------------------------


def plain_text(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def test_count_is_bracketed_and_separate_from_the_name(qapp):
    column = ColumnWidget(Bucket.TODAY)
    column.set_tasks([task(1, 0), task(2, 0)])

    assert plain_text(column.header.text()) == "Today (2)"
    column.deleteLater()


def test_count_is_muted_and_the_name_is_not(qapp):
    """The grey must apply only to the bracketed number."""
    column = ColumnWidget(Bucket.OVERDUE)
    column.set_tasks([task(1, -2)])
    html = column.header.text()

    match = re.search(r'<span style="color:(#[0-9a-fA-F]{6})[^"]*">\((\d+)\)</span>', html)
    assert match, f"no muted span around the count: {html!r}"
    assert html.index("Overdue") < html.index("<span"), "the name is outside the span"

    muted = match.group(1).lower()
    normal = column.palette().windowText().color().name().lower()
    assert muted != normal, "the count must not be the same colour as the name"
    column.deleteLater()


def test_the_column_name_stays_readable_when_empty(qapp):
    """An empty column used to grey its whole header, name included."""
    column = ColumnWidget(Bucket.NO_DUE_DATE)
    column.set_tasks([])
    assert column.header.isEnabled()
    assert plain_text(column.header.text()) == "No due date (0)"
    column.deleteLater()


# -- growing into unused width -------------------------------------------


def test_a_few_columns_share_the_whole_width(qapp):
    win = make_window(qapp, ["today", "tomorrow", "next_7"])
    win._on_tasks_loaded([task(1, 0), task(2, 1), task(3, 4)])
    qapp.processEvents()

    widths = [c.width() for c in win.columns.values()]
    floors = [c._preferred_width() for c in win.columns.values()]
    assert all(w > f for w, f in zip(widths, floors)), (widths, floors)

    used = sum(widths)
    assert used > WIDE * 0.8, f"columns left {WIDE - used}px of the window unused"
    win.deleteLater()


def test_a_column_stops_growing_before_it_becomes_a_banner(qapp):
    """One visible column must not stretch a card across the whole screen."""
    win = make_window(qapp, ["today"])
    win._on_tasks_loaded([task(1, 0)])
    qapp.processEvents()

    column = win.columns[Bucket.TODAY]
    assert column.width() <= column._max_width()
    assert column.width() < WIDE / 2, column.width()
    assert column.width() > column._preferred_width(), "but it still grew"
    win.deleteLater()


def test_the_spare_width_is_shared_evenly(qapp):
    win = make_window(qapp, ["today", "tomorrow"])
    win._on_tasks_loaded([task(1, 0), task(2, 1)])
    qapp.processEvents()

    a, b = (c.width() for c in win.columns.values())
    assert abs(a - b) <= 2, (a, b)
    win.deleteLater()


def test_all_eight_columns_keep_their_floor_and_scroll_instead(qapp):
    """Filling the width must never squeeze a column below readability."""
    win = make_window(qapp, [b.key for b in Bucket])
    win._on_tasks_loaded([task(i, off) for i, off in enumerate([0, 1, 4, 20, 99])])
    qapp.processEvents()

    for column in win.columns.values():
        if not column.is_compact:
            assert column.width() >= column._preferred_width(), column.bucket
    win.deleteLater()


# -- empty columns --------------------------------------------------------


def test_an_empty_column_is_narrower_than_a_filled_one(qapp):
    win = make_window(qapp, ["today", "no_due_date"])
    win._on_tasks_loaded([task(1, 0)])
    qapp.processEvents()

    filled = win.columns[Bucket.TODAY]
    empty = win.columns[Bucket.NO_DUE_DATE]
    assert empty.is_compact and not filled.is_compact
    # Below the normal floor, not merely denied a share of the spare width:
    # only a real collapse can get it under _preferred_width().
    assert empty.width() < empty._preferred_width(), empty.width()
    assert empty.width() < filled.width() / 2, (empty.width(), filled.width())
    win.deleteLater()


def test_a_collapsed_column_still_fits_its_own_header(qapp):
    """Shrinking must not clip the name — it is the only label left."""
    for bucket in Bucket:
        column = ColumnWidget(bucket)
        column.set_compact(True)
        needed = QFontMetrics(column.header.font()).horizontalAdvance(
            f"{bucket.title} (0)"
        )
        assert column.minimumWidth() == column._compact_width(), bucket
        assert column.minimumWidth() >= needed, bucket
        column.deleteLater()


def test_a_collapsed_column_is_still_a_drop_target(qapp):
    win = make_window(qapp, ["today", "no_due_date"])
    win._on_tasks_loaded([task(1, 0)])
    qapp.processEvents()

    empty = win.columns[Bucket.NO_DUE_DATE]
    assert empty.is_compact
    assert empty.acceptDrops(), "a card must still be droppable onto it"
    win.deleteLater()


def test_a_column_expands_again_once_it_has_a_card(qapp):
    win = make_window(qapp, ["today", "no_due_date"])
    win._on_tasks_loaded([task(1, 0)])
    qapp.processEvents()
    collapsed = win.columns[Bucket.NO_DUE_DATE].width()

    win._on_tasks_loaded([task(1, 0), task(2, None)])
    qapp.processEvents()

    column = win.columns[Bucket.NO_DUE_DATE]
    assert not column.is_compact
    assert column.width() > collapsed
    win.deleteLater()


def test_shrinking_can_be_switched_off(qapp):
    win = make_window(qapp, ["today", "no_due_date"], compact_empty_columns=False)
    win._on_tasks_loaded([task(1, 0)])
    qapp.processEvents()

    empty = win.columns[Bucket.NO_DUE_DATE]
    assert not empty.is_compact
    assert empty.width() >= empty._preferred_width()
    win.deleteLater()


def test_an_entirely_empty_board_keeps_the_columns_left_aligned(qapp):
    """With nothing growable, the trailing spacer takes the slack."""
    win = make_window(qapp, ["today", "tomorrow"])
    win._on_tasks_loaded([])
    qapp.processEvents()

    assert all(c.is_compact for c in win.columns.values())
    assert win._board_layout.stretch(win._board_stretch_index) == 1
    total = sum(c.width() for c in win.columns.values())
    floors = sum(c._preferred_width() for c in win.columns.values())
    assert total < floors, "every column should have collapsed below its floor"
    win.deleteLater()


# -- collapsed columns during a drag --------------------------------------


def test_empty_columns_open_up_while_a_card_is_being_dragged(qapp):
    """Collapsed columns are small targets; a drag must widen them again."""
    win = make_window(qapp, ["today", "no_due_date"])
    win._on_tasks_loaded([task(1, 0)])
    qapp.processEvents()

    empty = win.columns[Bucket.NO_DUE_DATE]
    assert empty.is_compact
    narrow = empty.width()

    win.columns[Bucket.TODAY].cards()[0].drag_started.emit()
    qapp.processEvents()
    assert not empty.is_compact
    assert empty.width() > narrow
    assert empty.width() >= empty._preferred_width()

    win.columns[Bucket.TODAY].cards()[0].drag_finished.emit()
    qapp.processEvents()
    assert empty.is_compact
    assert empty.width() == narrow
    win.deleteLater()


def test_a_card_dropped_on_a_widened_column_lands_there(qapp):
    """The widening must not break the drop it exists to make easier."""
    win = make_window(qapp, ["today", "no_due_date"])
    win._on_tasks_loaded([task(1, 0)])
    qapp.processEvents()

    card = win.columns[Bucket.TODAY].cards()[0]
    card.drag_started.emit()
    win.columns[Bucket.NO_DUE_DATE].task_dropped.emit(1, Bucket.NO_DUE_DATE)
    card.drag_finished.emit()
    qapp.processEvents()

    assert win.columns[Bucket.NO_DUE_DATE].task_ids() == [1]
    assert not win.columns[Bucket.NO_DUE_DATE].is_compact, "it has a card now"
    assert win.columns[Bucket.TODAY].is_compact, "and Today is empty now"
    win.deleteLater()

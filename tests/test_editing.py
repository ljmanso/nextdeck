"""Card clicks, the inline editor panel, visible columns and font size."""

from __future__ import annotations

import importlib
from datetime import date, datetime, timedelta, timezone

import pytest

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError as exc:  # pragma: no cover
    pytest.skip(f"PySide6 is unusable here: {exc}", allow_module_level=True)

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nextdeck.config import BoardConfig, Config, ServerConfig, UIConfig  # noqa: E402
from nextdeck.model import BUCKET_ORDER, Bucket, Task  # noqa: E402
from nextdeck.ui.card import CardWidget  # noqa: E402
from nextdeck.ui.editor import TaskEditor  # noqa: E402
from nextdeck.ui.elided_label import ElidedLabel  # noqa: E402
from nextdeck.ui.theme import apply_font_size  # noqa: E402
from nextdeck.ui.window import MainWindow  # noqa: E402

TODAY = date.today()


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeController(QObject):
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

    def create_task(self, title, description="", duedate=None):
        self.calls.append(("create_task", title, description, duedate))

    def set_done(self, task, done):
        self.calls.append(("set_done", task.id, done))

    def set_duedate(self, task, duedate):
        self.calls.append(("set_duedate", task.id, duedate))

    def reopen_with_duedate(self, task, duedate):
        self.calls.append(("reopen_with_duedate", task.id, duedate))

    def save_task(self, task, title, description, duedate, label_ids=None):
        self.calls.append(("save_task", task.id, title, description, duedate))

    def delete_task(self, task):
        self.calls.append(("delete_task", task.id))

    def fetch_labels(self):
        self.calls.append(("fetch_labels",))

    def apply_config(self, config):
        self.calls.append(("apply_config",))

    def stop(self):
        pass

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


def task(task_id=1, offset=None, done=False, title="Task", description=""):
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
        stack_title="ToDo",
        description=description,
        duedate=due,
        done=datetime.now(timezone.utc) if done else None,
        raw={"id": task_id, "stackId": 2},
    )


def make_config(**ui_kw) -> Config:
    return Config(
        server=ServerConfig(url="https://x", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2, title="Work"),
        ui=UIConfig(**ui_kw),
    )


def release_event():
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(5, 5),
        QPointF(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


# -- card title rendering -------------------------------------------------


def test_card_title_uses_the_bounded_label(qapp):
    card = CardWidget(task(title="x" * 300), Bucket.TODAY, title_lines=2)
    assert isinstance(card.title_label, ElidedLabel)
    assert card.title_label.maxLines() == 2
    card.deleteLater()


def test_card_title_line_limit_is_configurable(qapp):
    card = CardWidget(task(title="x" * 300), Bucket.TODAY, title_lines=5)
    assert card.title_label.maxLines() == 5
    card.deleteLater()


def test_full_title_stays_available_in_the_tooltip(qapp):
    long_title = "A very long title " * 10
    card = CardWidget(task(title=long_title), Bucket.TODAY, title_lines=1)
    assert long_title in card.toolTip(), "elided text must remain reachable"
    card.deleteLater()


def test_tooltip_carries_both_title_and_description(qapp):
    card = CardWidget(task(title="T", description="D"), Bucket.TODAY)
    assert "T" in card.toolTip() and "D" in card.toolTip()
    card.deleteLater()


# -- clicking a card ------------------------------------------------------


def test_clicking_a_card_activates_it(qapp):
    card = CardWidget(task(7), Bucket.TODAY)
    seen = []
    card.activated.connect(lambda t: seen.append(t.id))

    card._press_pos = QPointF(5, 5).toPoint()
    card.mouseReleaseEvent(release_event())

    assert seen == [7]
    card.deleteLater()


def test_a_drag_does_not_also_count_as_a_click(qapp):
    """Otherwise every reschedule would pop the editor open."""
    card = CardWidget(task(7), Bucket.TODAY)
    seen = []
    card.activated.connect(lambda t: seen.append(t.id))

    card._press_pos = QPointF(5, 5).toPoint()
    card._dragged = True
    card.mouseReleaseEvent(release_event())

    assert seen == []
    card.deleteLater()


def test_a_release_without_a_press_is_ignored(qapp):
    card = CardWidget(task(7), Bucket.TODAY)
    seen = []
    card.activated.connect(lambda t: seen.append(t.id))
    card.mouseReleaseEvent(release_event())
    assert seen == []
    card.deleteLater()


# -- the editor panel -----------------------------------------------------


def test_editor_starts_hidden(qapp):
    e = TaskEditor()
    assert not e.isVisible()
    assert e.task is None
    e.deleteLater()


def test_open_task_populates_every_field(qapp):
    t = task(3, offset=2, title="Write docs", description="all of them")
    e = TaskEditor()
    e.open_task(t)
    assert e.title_edit.text() == "Write docs"
    assert e.description_edit.toPlainText() == "all of them"
    due = e.current_values()[2]
    assert due is not None and due.date() == TODAY + timedelta(days=2)
    e.deleteLater()


def test_opening_an_undated_task_offers_no_date(qapp):
    e = TaskEditor()
    e.open_task(task(3, offset=None))
    assert e.current_values()[2] is None
    e.deleteLater()


def test_no_changes_means_no_save(qapp):
    e = TaskEditor()
    e.open_task(task(3, offset=2, title="T", description="D"))
    assert not e.has_changes(), "a round trip through the fields is not an edit"
    saves = []
    e.save_requested.connect(lambda *a: saves.append(a))
    e.save_button.click()
    assert saves == []
    e.deleteLater()


def test_save_button_commits_and_closes(qapp):
    t = task(3, offset=2, title="Old", description="Old body")
    e = TaskEditor()
    e.open_task(t)
    e.title_edit.setText("New title")
    e.description_edit.setPlainText("New body")
    assert e.has_changes()

    saves = []
    closed = []
    e.save_requested.connect(lambda *a: saves.append(a))
    e.closed.connect(lambda: closed.append(True))

    e.save_button.click()

    assert len(saves) == 1
    _, title, description, _due, _labels = saves[0]
    assert (title, description) == ("New title", "New body")
    assert closed == [True]
    assert not e.isVisible()
    e.deleteLater()


def test_escape_discards_and_closes(qapp):
    """Escape cancels, as it does everywhere else."""
    e = TaskEditor()
    e.open_task(task(3, title="Old", description="Old body"))
    e.title_edit.setText("Changed")
    e.description_edit.setPlainText("Also changed")

    saves = []
    discarded = []
    e.save_requested.connect(lambda *a: saves.append(a))
    e.discarded.connect(lambda t: discarded.append(t.id))

    e.cancel()

    assert saves == [], "Escape must never write to the server"
    assert discarded == [3], "but the dropped work must be reported"
    assert not e.isVisible()
    e.deleteLater()


def test_cancel_button_discards(qapp):
    e = TaskEditor()
    e.open_task(task(3, title="Old"))
    e.title_edit.setText("Changed")
    saves = []
    e.save_requested.connect(lambda *a: saves.append(a))

    e.discard_button.click()

    assert saves == []
    assert not e.isVisible()
    e.deleteLater()


def test_cancelling_an_untouched_task_reports_nothing(qapp):
    e = TaskEditor()
    e.open_task(task(3, offset=2, title="Old", description="d"))
    discarded = []
    e.discarded.connect(lambda t: discarded.append(t.id))
    e.cancel()
    assert discarded == [], "nothing was changed, so nothing was lost"
    e.deleteLater()


@pytest.mark.parametrize("field", ["title_edit", "description_edit"])
def test_pressing_escape_for_real_discards(qapp, field):
    """Driven through the key, not the method: the shortcut must actually fire
    from inside the text widgets, where key events do not reliably bubble."""
    e = TaskEditor()
    e.resize(340, 500)
    e.show()
    e.open_task(task(3, title="Old", description="body"))
    saves, discards = [], []
    e.save_requested.connect(lambda *a: saves.append(a))
    e.discarded.connect(lambda t: discards.append(t.id))

    widget = getattr(e, field)
    widget.setFocus()
    e.title_edit.setText("Changed")

    QTest.keyClick(widget, Qt.Key.Key_Escape)

    assert saves == []
    assert discards == [3]
    assert not e.isVisible()
    e.deleteLater()


@pytest.mark.parametrize("field", ["title_edit", "description_edit"])
def test_pressing_ctrl_enter_for_real_saves(qapp, field):
    e = TaskEditor()
    e.resize(340, 500)
    e.show()
    e.open_task(task(3, title="Old", description="body"))
    saves = []
    e.save_requested.connect(lambda *a: saves.append(a))

    widget = getattr(e, field)
    widget.setFocus()
    e.title_edit.setText("Changed")

    QTest.keyClick(widget, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)

    assert len(saves) == 1 and saves[0][1] == "Changed"
    assert not e.isVisible()
    e.deleteLater()


def test_ctrl_enter_still_saves(qapp):
    e = TaskEditor()
    e.open_task(task(3, title="Old"))
    e.title_edit.setText("New")
    saves = []
    e.save_requested.connect(lambda *a: saves.append(a))
    e.save_and_close()
    assert len(saves) == 1 and saves[0][1] == "New"
    e.deleteLater()


def test_clearing_the_title_keeps_the_old_one(qapp):
    """An empty title would render as an unreadable card."""
    e = TaskEditor()
    e.open_task(task(3, title="Keep me", description="d"))
    e.title_edit.setText("   ")
    e.description_edit.setPlainText("changed")
    saves = []
    e.save_requested.connect(lambda *a: saves.append(a))
    e.save_and_close()
    assert saves and saves[0][1] == "Keep me"
    e.deleteLater()


def test_editing_only_the_due_date_counts_as_a_change(qapp):
    e = TaskEditor()
    e.open_task(task(3, offset=2))
    e._due_widget._preset(5)
    assert e.has_changes()
    e.deleteLater()


def test_clearing_the_due_date_counts_as_a_change(qapp):
    e = TaskEditor()
    e.open_task(task(3, offset=2))
    e._due_widget.enabled_box.setChecked(False)
    assert e.has_changes()
    saves = []
    e.save_requested.connect(lambda *a: saves.append(a))
    e.save_and_close()
    assert saves and saves[0][3] is None
    e.deleteLater()


def test_editor_complete_button_closes_and_forwards(qapp):
    e = TaskEditor()
    e.open_task(task(3))
    seen = []
    e.toggle_done_requested.connect(lambda t: seen.append(t.id))
    e.done_button.click()
    assert seen == [3]
    assert not e.isVisible()
    e.deleteLater()


def test_editor_shows_reopen_for_a_completed_task(qapp):
    e = TaskEditor()
    e.open_task(task(3, done=True))
    assert e.done_button.text() == "Reopen"
    e.deleteLater()


def test_editor_delete_button_closes_and_forwards(qapp):
    e = TaskEditor()
    e.open_task(task(3))
    seen = []
    e.delete_requested.connect(lambda t: seen.append(t.id))
    e.delete_button.click()
    assert seen == [3]
    assert not e.isVisible()
    e.deleteLater()


def test_reopening_the_editor_on_another_task_swaps_the_contents(qapp):
    e = TaskEditor()
    e.open_task(task(1, offset=1, title="First"))
    e.open_task(task(2, offset=None, title="Second"))
    assert e.title_edit.text() == "Second"
    assert e.current_values()[2] is None, "stale due date must not leak across"
    assert not e.has_changes()
    e.deleteLater()


def test_switching_cards_discards_rather_than_silently_saving(qapp):
    """Only Save writes; clicking another card must not sneak an edit through."""
    e = TaskEditor()
    e.open_task(task(1, title="First"))
    e.title_edit.setText("Edited but abandoned")

    saves = []
    discarded = []
    e.save_requested.connect(lambda *a: saves.append(a))
    e.discarded.connect(lambda t: discarded.append(t.id))

    e.open_task(task(2, title="Second"))

    assert saves == []
    assert discarded == [1]
    assert e.title_edit.text() == "Second"
    assert e.task is not None and e.task.id == 2
    e.deleteLater()


# -- window integration ---------------------------------------------------


@pytest.fixture
def window(qapp):
    controller = FakeController()
    win = MainWindow(make_config(), controller, config_path=None)
    yield win, controller
    win.deleteLater()


def test_clicking_a_card_opens_the_editor_in_the_window(window):
    win, controller = window
    controller.tasks_loaded.emit([task(5, offset=0, title="Click me")])

    card = win.columns[Bucket.TODAY].cards()[0]
    card._press_pos = QPointF(5, 5).toPoint()
    card.mouseReleaseEvent(release_event())

    assert win.editor.task is not None and win.editor.task.id == 5
    assert win.editor.title_edit.text() == "Click me"


def test_saving_from_the_editor_reaches_the_controller(window):
    win, controller = window
    controller.tasks_loaded.emit([task(5, offset=0, title="Before")])
    win.editor.open_task(win._tasks[0])
    win.editor.title_edit.setText("After")
    win.editor.save_button.click()

    calls = controller.named("save_task")
    assert len(calls) == 1
    assert calls[0][1] == 5 and calls[0][2] == "After"


def test_saving_updates_the_card_immediately(window):
    win, controller = window
    controller.tasks_loaded.emit([task(5, offset=0, title="Before")])
    win.editor.open_task(win._tasks[0])
    win.editor.title_edit.setText("After")
    win.editor.save_button.click()

    titles = [c.task.title for c in win.columns[Bucket.TODAY].cards()]
    assert titles == ["After"], "optimistic render should not wait for the server"


def test_rescheduling_from_the_editor_moves_the_card(window):
    win, controller = window
    controller.tasks_loaded.emit([task(5, offset=0, title="Move me")])
    win.editor.open_task(win._tasks[0])
    win.editor._due_widget._preset(1)
    win.editor.save_button.click()

    assert win.columns[Bucket.TODAY].task_ids() == []
    assert win.columns[Bucket.TOMORROW].task_ids() == [5]


def test_escape_in_the_window_leaves_the_card_untouched(window):
    win, controller = window
    controller.tasks_loaded.emit([task(5, offset=0, title="Before")])
    win.editor.open_task(win._tasks[0])
    win.editor.title_edit.setText("Never saved")
    win.editor.cancel()

    assert controller.named("save_task") == []
    titles = [c.task.title for c in win.columns[Bucket.TODAY].cards()]
    assert titles == ["Before"], "the board must not show the abandoned edit"
    assert "Discarded changes" in win.status_label.text()


def test_editor_delete_goes_through_the_window(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    win, controller = window
    controller.tasks_loaded.emit([task(5, offset=0)])
    monkeypatch.setattr(
        QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes
    )
    win.editor.open_task(win._tasks[0])
    win.editor.delete_button.click()
    assert controller.named("delete_task") == [("delete_task", 5)]


# -- visible columns ------------------------------------------------------


def test_only_configured_columns_are_built(qapp):
    win = MainWindow(make_config(visible_columns=["today", "tomorrow"]),
                     FakeController())
    assert list(win.columns) == [Bucket.TODAY, Bucket.TOMORROW]
    win.deleteLater()


def test_columns_keep_display_order_regardless_of_config_order(qapp):
    win = MainWindow(make_config(visible_columns=["done", "today", "overdue"]),
                     FakeController())
    assert list(win.columns) == [Bucket.OVERDUE, Bucket.TODAY, Bucket.DONE]
    win.deleteLater()


def test_an_empty_column_list_falls_back_to_everything(qapp):
    """A board with no columns would look broken rather than configured."""
    win = MainWindow(make_config(visible_columns=[]), FakeController())
    assert list(win.columns) == list(BUCKET_ORDER)
    win.deleteLater()


def test_unknown_column_keys_are_ignored(qapp):
    win = MainWindow(make_config(visible_columns=["today", "nonsense"]),
                     FakeController())
    assert list(win.columns) == [Bucket.TODAY]
    win.deleteLater()


def test_tasks_in_hidden_columns_are_reported_not_silently_dropped(qapp):
    controller = FakeController()
    win = MainWindow(make_config(visible_columns=["today"]), controller)
    controller.tasks_loaded.emit([task(1, offset=0), task(2, offset=5),
                                  task(3, offset=20)])
    assert win.columns[Bucket.TODAY].task_ids() == [1]
    assert "2 in hidden columns" in win.status_label.text()
    win.deleteLater()


# -- font size ------------------------------------------------------------


def test_font_size_can_be_set_and_restored(qapp):
    original = qapp.font().pointSizeF()
    apply_font_size(17)
    assert qapp.font().pointSizeF() == pytest.approx(17.0)
    apply_font_size(0)
    assert qapp.font().pointSizeF() == pytest.approx(original)


def test_font_size_affects_card_height(qapp):
    apply_font_size(0)
    small = CardWidget(task(title="x" * 200), Bucket.TODAY, title_lines=2)
    small.resize(210, 100)
    small_h = small.title_label.heightForWidth(210)

    apply_font_size(22)
    big = CardWidget(task(title="x" * 200), Bucket.TODAY, title_lines=2)
    big.resize(210, 100)
    big_h = big.title_label.heightForWidth(210)
    apply_font_size(0)

    assert big_h > small_h
    small.deleteLater()
    big.deleteLater()

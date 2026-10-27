"""Covers the Qt paths the board tests don't reach: the icon fallback, the
drag highlight, and the dialogs."""

from __future__ import annotations

import importlib
from datetime import date, datetime, timedelta

import pytest

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"PySide6 is unusable here: {exc}", allow_module_level=True)

from PySide6.QtCore import QMimeData, QObject, QPointF, Qt, Signal  # noqa: E402
from PySide6.QtGui import QDragEnterEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402

from nextdeck.config import BoardConfig, Config, ServerConfig  # noqa: E402
from nextdeck.model import Bucket  # noqa: E402
from nextdeck.ui.add_dialog import (  # noqa: E402
    AddTaskDialog,
    DueDateDialog,
    DueDateWidget,
)
from nextdeck.ui.card import TASK_MIME  # noqa: E402
from nextdeck.ui.column import ColumnWidget  # noqa: E402
from nextdeck.ui.icon import _painted_icon, app_icon  # noqa: E402
from nextdeck.ui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


# -- icon -----------------------------------------------------------------


def test_app_icon_is_usable(qapp):
    icon = app_icon()
    assert not icon.isNull()


def test_painted_icon_fallback_covers_tray_sizes(qapp):
    """Used when Qt's SVG plugin is missing; a blank tray icon is worse."""
    icon = _painted_icon()
    assert not icon.isNull()
    sizes = {s.width() for s in icon.availableSizes()}
    assert {16, 24, 32, 48} <= sizes


# -- drag highlight -------------------------------------------------------


def _drag_enter(mime):
    return QDragEnterEvent(
        QPointF(5, 5),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_drag_enter_highlights_and_leave_clears(qapp):
    column = ColumnWidget(Bucket.TOMORROW)
    mime = QMimeData()
    mime.setData(TASK_MIME, b"7")

    event = _drag_enter(mime)
    column.dragEnterEvent(event)
    assert event.isAccepted()
    assert column._drag_active, "column should show it is a drop target"

    column.dragLeaveEvent(None)
    assert not column._drag_active
    column.deleteLater()


def test_drag_enter_rejects_foreign_payloads(qapp):
    column = ColumnWidget(Bucket.TOMORROW)
    mime = QMimeData()
    mime.setText("not a card")
    event = _drag_enter(mime)
    column.dragEnterEvent(event)
    assert not column._drag_active
    column.deleteLater()


def test_empty_column_shows_a_placeholder_not_a_card(qapp):
    column = ColumnWidget(Bucket.TODAY)
    column.set_tasks([])
    assert column.cards() == []
    assert "(0)" in column.header.text(), "the count is shown even at zero"
    column.deleteLater()


# -- due date widget -----------------------------------------------------


def test_due_date_widget_unchecked_means_no_date(qapp):
    w = DueDateWidget(None)
    assert not w.enabled_box.isChecked()
    assert w.value() is None
    assert not w.edit.isEnabled()
    w.deleteLater()


def test_due_date_widget_round_trips_an_existing_date(qapp):
    original = datetime(2026, 11, 3, 14, 30).astimezone()
    w = DueDateWidget(original)
    assert w.enabled_box.isChecked()
    got = w.value()
    assert got is not None
    assert (got.date(), got.hour, got.minute) == (original.date(), 14, 30)
    w.deleteLater()


@pytest.mark.parametrize("days", [0, 1, 7])
def test_due_date_presets_set_the_right_day_and_enable_the_field(qapp, days):
    w = DueDateWidget(None)
    w._preset(days)
    assert w.enabled_box.isChecked()
    got = w.value()
    assert got is not None and got.date() == date.today() + timedelta(days=days)
    w.deleteLater()


# -- add task dialog -----------------------------------------------------


def test_add_dialog_blocks_an_empty_title(qapp):
    d = AddTaskDialog("Work › ToDo")
    assert not d._ok_button.isEnabled()
    d.title_edit.setText("  ")
    assert not d._ok_button.isEnabled(), "whitespace is not a title"
    d.title_edit.setText("Real task")
    assert d._ok_button.isEnabled()
    d.deleteLater()


def test_add_dialog_returns_trimmed_values(qapp):
    d = AddTaskDialog()
    d.title_edit.setText("  Buy milk  ")
    d.description_edit.setPlainText("  2 litres  ")
    title, description, due = d.values()
    assert (title, description, due) == ("Buy milk", "2 litres", None)
    d.deleteLater()


def test_add_dialog_passes_a_due_date_through(qapp):
    d = AddTaskDialog()
    d.title_edit.setText("Dated")
    d.due_widget._preset(1)
    _, _, due = d.values()
    assert due is not None and due.date() == date.today() + timedelta(days=1)
    d.deleteLater()


def test_due_date_dialog_can_clear_a_date(qapp):
    d = DueDateDialog("Some task", datetime(2026, 11, 3, 9, 0).astimezone())
    assert d.value() is not None
    d.due_widget.enabled_box.setChecked(False)
    assert d.value() is None
    d.deleteLater()


# -- settings dialog -----------------------------------------------------


class StubController(QObject):
    connection_ok = Signal(list)
    stacks_loaded = Signal(list)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.tested = 0
        self.stack_fetches: list[int] = []

    def test_connection(self, config):
        self.tested += 1

    def fetch_stacks(self, config, board_id):
        self.stack_fetches.append(board_id)


def save_button(dialog):
    return dialog.buttons.button(QDialogButtonBox.StandardButton.Save)


def test_settings_cannot_be_saved_until_a_board_is_chosen(qapp):
    d = SettingsDialog(Config(), StubController())
    assert not save_button(d).isEnabled()
    d.url_edit.setText("https://cloud.example.org")
    d.user_edit.setText("luis")
    d.password_edit.setText("token")
    assert not save_button(d).isEnabled(), "credentials alone are not enough"
    d.deleteLater()


def test_settings_populates_pickers_from_the_server(qapp):
    controller = StubController()
    d = SettingsDialog(Config(), controller)
    d.url_edit.setText("https://cloud.example.org")
    d.user_edit.setText("luis")
    d.password_edit.setText("token")

    d._on_test()
    assert controller.tested == 1

    controller.connection_ok.emit([{"id": 9, "title": "Work"}])
    assert d.board_combo.currentData() == 9
    assert controller.stack_fetches == [9], "choosing a board fetches its stacks"

    controller.stacks_loaded.emit([{"id": 29, "title": "To Do"}])
    assert d.stack_combo.currentData() == 29
    assert save_button(d).isEnabled(), "now it is complete"

    d._on_save()
    cfg = d.result_config()
    assert cfg.board.id == 9 and cfg.board.title == "Work"
    assert cfg.board.default_stack_id == 29
    assert cfg.board.default_stack_title == "To Do"
    assert cfg.server.url == "https://cloud.example.org"
    assert cfg.is_complete()
    d.deleteLater()


def test_settings_reports_an_account_with_no_boards(qapp):
    controller = StubController()
    d = SettingsDialog(Config(), controller)
    controller.connection_ok.emit([])
    assert "no Deck boards" in d.status_label.text()
    assert not save_button(d).isEnabled()
    d.deleteLater()


def test_settings_surfaces_an_error(qapp):
    controller = StubController()
    d = SettingsDialog(Config(), controller)
    controller.error.emit("Nextcloud rejected the credentials")
    assert "rejected the credentials" in d.status_label.text()
    d.deleteLater()


def test_settings_seeds_combos_from_saved_config_without_a_round_trip(qapp):
    """Reopening settings should not require re-testing the connection."""
    cfg = Config(
        server=ServerConfig(url="https://x", username="u", app_password="p"),
        board=BoardConfig(id=3, default_stack_id=7, title="Work",
                          default_stack_title="ToDo"),
    )
    controller = StubController()
    d = SettingsDialog(cfg, controller)
    assert d.board_combo.currentData() == 3
    assert d.stack_combo.currentData() == 7
    assert save_button(d).isEnabled()
    assert controller.tested == 0
    d.deleteLater()


def test_settings_disconnects_from_the_controller_on_close(qapp):
    """The controller outlives the dialog; a later error must not reach it."""
    controller = StubController()
    d = SettingsDialog(Config(), controller)
    d.done(0)
    controller.error.emit("late error")  # must not raise
    d.deleteLater()


# -- the new appearance and column settings ------------------------------


def test_settings_round_trips_the_appearance_options(qapp):
    cfg = Config(
        server=ServerConfig(url="https://x", username="u", app_password="p"),
        board=BoardConfig(id=3, default_stack_id=7, title="W",
                          default_stack_title="S"),
    )
    cfg.ui.font_size_pt = 13
    cfg.ui.card_title_lines = 4
    cfg.ui.done_column_days = 30

    d = SettingsDialog(cfg, StubController())
    assert d.font_size_spin.value() == 13
    assert d.title_lines_spin.value() == 4
    assert d.done_days_spin.value() == 30

    d.font_size_spin.setValue(16)
    d.title_lines_spin.setValue(1)
    d._on_save()
    ui = d.result_config().ui
    assert (ui.font_size_pt, ui.card_title_lines) == (16, 1)
    d.deleteLater()


def test_font_size_zero_means_system_default(qapp):
    d = SettingsDialog(Config(), StubController())
    assert d.font_size_spin.value() == 0
    assert d.font_size_spin.specialValueText() == "system default"
    d.deleteLater()


def test_every_column_is_offered_and_checked_by_default(qapp):
    from nextdeck.model import ALL_BUCKET_KEYS

    d = SettingsDialog(Config(), StubController())
    assert set(d.column_boxes) == set(ALL_BUCKET_KEYS)
    assert sorted(d.selected_columns()) == sorted(ALL_BUCKET_KEYS)
    d.deleteLater()


def test_unchecking_a_column_is_saved(qapp):
    cfg = Config(
        server=ServerConfig(url="https://x", username="u", app_password="p"),
        board=BoardConfig(id=3, default_stack_id=7),
    )
    d = SettingsDialog(cfg, StubController())
    d.column_boxes["done"].setChecked(False)
    d._on_save()
    saved = d.result_config().ui.visible_columns
    assert "done" not in saved
    assert "today" in saved
    d.deleteLater()


def test_saving_is_blocked_with_no_columns_selected(qapp):
    cfg = Config(
        server=ServerConfig(url="https://x", username="u", app_password="p"),
        board=BoardConfig(id=3, default_stack_id=7),
    )
    d = SettingsDialog(cfg, StubController())
    assert save_button(d).isEnabled()
    for check in d.column_boxes.values():
        check.setChecked(False)
    assert not save_button(d).isEnabled()
    assert "at least one column" in d._columns_note.text()
    d.column_boxes["today"].setChecked(True)
    assert save_button(d).isEnabled()
    d.deleteLater()


def test_done_retention_no_longer_offers_zero(qapp):
    """Visibility is the column checkbox's job now, not a 0-day retention."""
    d = SettingsDialog(Config(), StubController())
    assert d.done_days_spin.minimum() == 1
    d.deleteLater()


# -- settings grouping ----------------------------------------------------


def _group_titles(dialog):
    from PySide6.QtWidgets import QGroupBox

    return [g.title() for g in dialog.findChildren(QGroupBox)]


def _owning_group(widget):
    from PySide6.QtWidgets import QGroupBox

    parent = widget.parent()
    while parent is not None and not isinstance(parent, QGroupBox):
        parent = parent.parent()
    return None if parent is None else parent.title()


def test_shrink_empty_columns_sits_with_the_other_behaviour_options(qapp):
    """It is a UX preference, not a choice of which columns exist."""
    d = SettingsDialog(Config(), StubController())
    assert "Behaviour and UX" in _group_titles(d)
    assert "Behaviour" not in _group_titles(d), "the group was renamed"
    assert _owning_group(d.compact_empty_box) == "Behaviour and UX"
    assert _owning_group(d.column_boxes["today"]) == "Columns to show"
    d.deleteLater()


def test_shrink_empty_columns_still_round_trips(qapp):
    cfg = Config(
        server=ServerConfig(url="https://x", username="u", app_password="p"),
        board=BoardConfig(id=3, default_stack_id=7),
    )
    cfg.ui.compact_empty_columns = False
    d = SettingsDialog(cfg, StubController())
    assert not d.compact_empty_box.isChecked()
    d.compact_empty_box.setChecked(True)
    d._on_save()
    assert d.result_config().ui.compact_empty_columns is True
    d.deleteLater()


def test_settings_are_split_across_two_tabs(qapp):
    """Server details on one tab, how the board looks on the other.

    Stacked in one column the four groups made the dialog taller than a
    laptop screen.
    """
    from PySide6.QtWidgets import QGroupBox

    d = SettingsDialog(Config(), StubController())
    titles = [
        [box.title() for box in d.tabs.widget(i).findChildren(QGroupBox)]
        for i in range(d.tabs.count())
    ]
    assert titles == [
        ["Nextcloud server", "Board"],
        ["Behaviour and UX", "Columns to show"],
    ]
    assert d.tabs.currentIndex() == 0, "a first run starts with the server details"


def test_save_and_status_are_shared_by_both_tabs(qapp):
    """Saving must not depend on which tab happens to be showing."""
    d = SettingsDialog(Config(), StubController())
    for i in range(d.tabs.count()):
        assert not d.tabs.widget(i).isAncestorOf(d.buttons)
        assert not d.tabs.widget(i).isAncestorOf(d.status_label)

"""Settings dialog — the GUI front end for config.yaml.

The board and stack pickers are populated from the server rather than typed by
hand, which is the whole reason "Test connection" exists: it doubles as the
credential check and the board lookup.
"""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import autostart
from .. import config as cfgmod
from ..config import Config
from .elided_label import WrappedLabel
from ..model import BUCKET_ORDER
from ..sync import SyncController


class SettingsDialog(QDialog):
    """Edits a copy of the config; the caller persists it on accept."""

    def __init__(
        self,
        config: Config,
        controller: SyncController,
        parent: QWidget | None = None,
        config_path: Path | None = None,
    ):
        super().__init__(parent)
        self._config = copy.deepcopy(config)
        self._controller = controller
        self._config_path = config_path

        self.setWindowTitle("nextdeck settings")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        # Two tabs rather than one long column: with every group stacked the
        # dialog needed most of a laptop screen's height. What you set once
        # (where the board is) is kept apart from what you tune (how it looks).
        self.tabs = QTabWidget()
        self.tabs.addTab(
            self._tab(self._build_server_group(), self._build_board_group()),
            "Server and board",
        )
        self.tabs.addTab(
            self._tab(self._build_ui_group(), self._build_columns_group()),
            "Behaviour and columns",
        )
        layout.addWidget(self.tabs)

        self.status_label = WrappedLabel("")
        layout.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._on_save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._controller.connection_ok.connect(self._on_boards)
        self._controller.stacks_loaded.connect(self._on_stacks)
        self._controller.error.connect(self._on_error)

        self._load_from_config()
        self._update_save_enabled()

    # -- construction -----------------------------------------------------

    @staticmethod
    def _tab(*groups: QGroupBox) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        for group in groups:
            box.addWidget(group)
        # Groups keep their natural height; the shorter tab takes up the
        # slack at the bottom instead of spreading its boxes apart.
        box.addStretch(1)
        return page

    def _build_server_group(self) -> QGroupBox:
        group = QGroupBox("Nextcloud server")
        form = QFormLayout(group)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://cloud.example.org")
        form.addRow("URL", self.url_edit)

        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("your username")
        form.addRow("Username", self.user_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("app password")
        form.addRow("App password", self.password_edit)

        note = WrappedLabel(
            "Create an app password under Nextcloud → Settings → Security. "
            "It is stored in plain text in config.yaml (readable only by you), "
            "so prefer an app password over your account password."
        )
        note.setEnabled(False)
        form.addRow(note)

        self.verify_box = QCheckBox("Verify the TLS certificate")
        form.addRow(self.verify_box)

        row = QHBoxLayout()
        self.test_button = QPushButton("Test connection")
        self.test_button.clicked.connect(self._on_test)
        row.addWidget(self.test_button)
        row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow(holder)

        for edit in (self.url_edit, self.user_edit, self.password_edit):
            edit.textChanged.connect(self._on_credentials_changed)
        return group

    def _build_board_group(self) -> QGroupBox:
        group = QGroupBox("Board")
        form = QFormLayout(group)

        self.board_combo = QComboBox()
        self.board_combo.setPlaceholderText("Test the connection to list boards")
        self.board_combo.currentIndexChanged.connect(self._on_board_selected)
        form.addRow("Board", self.board_combo)

        self.stack_combo = QComboBox()
        self.stack_combo.setPlaceholderText("Choose a board first")
        self.stack_combo.currentIndexChanged.connect(self._update_save_enabled)
        form.addRow("Add new tasks to", self.stack_combo)

        return group

    def _copy_toggle_command(self) -> None:
        QApplication.clipboard().setText(self.toggle_command_edit.text())

    def _build_ui_group(self) -> QGroupBox:
        group = QGroupBox("Behaviour and UX")
        form = QFormLayout(group)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 240)
        self.interval_spin.setSuffix(" min")
        form.addRow("Refresh every", self.interval_spin)

        self.refresh_on_show_box = QCheckBox("Also refresh when the window is shown")
        self.refresh_on_show_box.setToolTip(
            "Fetch the board whenever it comes back into view, as well as on "
            "the timer above."
        )
        form.addRow(self.refresh_on_show_box)

        self.done_days_spin = QSpinBox()
        self.done_days_spin.setRange(1, 365)
        self.done_days_spin.setSuffix(" days")
        self.done_days_spin.setToolTip(
            "How long a completed task stays in the Done column"
        )
        form.addRow("Keep completed for", self.done_days_spin)

        self.date_format_edit = QLineEdit()
        self.date_format_edit.setPlaceholderText("%a %d %b")
        form.addRow("Date format", self.date_format_edit)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(0, 32)
        self.font_size_spin.setSuffix(" pt")
        self.font_size_spin.setSpecialValueText("system default")
        self.font_size_spin.setToolTip("Applies to the whole window")
        form.addRow("Font size", self.font_size_spin)

        self.title_lines_spin = QSpinBox()
        self.title_lines_spin.setRange(1, 8)
        self.title_lines_spin.setToolTip(
            "Longer titles are shortened with an ellipsis; the full text stays "
            "in the card's tooltip"
        )
        form.addRow("Title lines per card", self.title_lines_spin)

        self.compact_empty_box = QCheckBox("Shrink columns that have no tasks")
        self.compact_empty_box.setToolTip(
            "Collapse an empty column to its header so the columns with tasks "
            "in them get the width instead. Empty columns stay drop targets."
        )
        form.addRow(self.compact_empty_box)

        self.toggle_shortcut_edit = QKeySequenceEdit()
        self.toggle_shortcut_edit.setToolTip(
            "Press the key you want. Clear the box to switch it off."
        )
        shortcut_row = QHBoxLayout()
        shortcut_row.setSpacing(4)
        shortcut_row.addWidget(self.toggle_shortcut_edit, 1)
        clear_shortcut = QPushButton("Clear")
        clear_shortcut.setToolTip("Use no key for this")
        clear_shortcut.clicked.connect(self.toggle_shortcut_edit.clear)
        shortcut_row.addWidget(clear_shortcut)
        form.addRow("Show / hide window", shortcut_row)

        self._shortcut_note = WrappedLabel(
            "Only reaches nextdeck while nextdeck has the keyboard, so this "
            "hides the window but cannot bring it back. For a key that works "
            "anywhere, bind this command in your desktop's keyboard settings:"
        )
        self._shortcut_note.setEnabled(False)
        form.addRow(self._shortcut_note)

        self.toggle_command_edit = QLineEdit(autostart.toggle_command(self._config_path))
        self.toggle_command_edit.setReadOnly(True)
        self.toggle_command_edit.setCursorPosition(0)
        self.toggle_command_edit.setToolTip(
            "Copy this into your desktop's keyboard settings"
        )
        command_row = QHBoxLayout()
        command_row.setSpacing(4)
        command_row.addWidget(self.toggle_command_edit, 1)
        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(self._copy_toggle_command)
        command_row.addWidget(copy_button)
        form.addRow(command_row)

        self.start_minimized_box = QCheckBox("Start minimised to the tray")
        form.addRow(self.start_minimized_box)

        self.autostart_box = QCheckBox("Start automatically on login")
        form.addRow(self.autostart_box)

        return group

    def _build_columns_group(self) -> QGroupBox:
        group = QGroupBox("Columns to show")
        box = QVBoxLayout(group)
        box.setSpacing(2)

        self.column_boxes: dict[str, QCheckBox] = {}
        row = QHBoxLayout()
        row.setSpacing(10)
        column = QVBoxLayout()
        column.setSpacing(2)
        for index, bucket in enumerate(BUCKET_ORDER):
            check = QCheckBox(bucket.title)
            check.toggled.connect(self._update_save_enabled)
            self.column_boxes[bucket.key] = check
            column.addWidget(check)
            # Two balanced columns of checkboxes rather than one tall list.
            if index == (len(BUCKET_ORDER) + 1) // 2 - 1:
                row.addLayout(column)
                column = QVBoxLayout()
                column.setSpacing(2)
        row.addLayout(column)
        row.addStretch(1)
        box.addLayout(row)

        self._columns_note = WrappedLabel("")
        self._columns_note.setEnabled(False)
        box.addWidget(self._columns_note)
        return group

    # -- state ------------------------------------------------------------

    def _load_from_config(self) -> None:
        server = self._config.server
        self.url_edit.setText(server.url)
        self.user_edit.setText(server.username)
        self.password_edit.setText(server.app_password)
        self.verify_box.setChecked(server.verify_ssl)

        ui = self._config.ui
        self.interval_spin.setValue(max(1, ui.refresh_interval_minutes))
        self.done_days_spin.setValue(min(365, max(1, ui.done_column_days)))
        self.font_size_spin.setValue(max(0, ui.font_size_pt))
        self.title_lines_spin.setValue(max(1, ui.card_title_lines))
        selected = set(ui.visible_columns or [])
        for key, check in self.column_boxes.items():
            check.setChecked(key in selected)
        self.date_format_edit.setText(ui.date_format)
        self.refresh_on_show_box.setChecked(ui.refresh_on_show)
        self.compact_empty_box.setChecked(ui.compact_empty_columns)
        self.toggle_shortcut_edit.setKeySequence(
            QKeySequence(ui.toggle_shortcut or "")
        )
        self.start_minimized_box.setChecked(ui.start_minimized)
        self.autostart_box.setChecked(self._config.autostart)

        board = self._config.board
        if board.id is not None:
            # Seed the combos with the saved selection so an unchanged visit to
            # this dialog does not require a round trip to the server.
            self.board_combo.addItem(board.title or f"Board {board.id}", board.id)
            self.board_combo.setCurrentIndex(0)
        if board.default_stack_id is not None:
            self.stack_combo.addItem(
                board.default_stack_title or f"Stack {board.default_stack_id}",
                board.default_stack_id,
            )
            self.stack_combo.setCurrentIndex(0)

    def _current_credentials(self) -> Config:
        probe = copy.deepcopy(self._config)
        probe.server.url = cfgmod.normalize_url(self.url_edit.text())
        probe.server.username = self.user_edit.text().strip()
        probe.server.app_password = self.password_edit.text()
        probe.server.verify_ssl = self.verify_box.isChecked()
        return probe

    def _on_credentials_changed(self) -> None:
        self._update_save_enabled()

    def selected_columns(self) -> list[str]:
        return [k for k, c in self.column_boxes.items() if c.isChecked()]

    def _update_save_enabled(self) -> None:
        any_column = bool(self.selected_columns())
        self._columns_note.setText(
            "" if any_column else "Pick at least one column to show."
        )
        ready = (
            any_column
            and
            bool(self.url_edit.text().strip())
            and bool(self.user_edit.text().strip())
            and bool(self.password_edit.text())
            and self.board_combo.currentData() is not None
            and self.stack_combo.currentData() is not None
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setEnabled(ready)

    # -- server round trips ----------------------------------------------

    def _on_test(self) -> None:
        probe = self._current_credentials()
        if not probe.server.is_complete():
            self.status_label.setText("Fill in the URL, username and app password.")
            return
        self.test_button.setEnabled(False)
        self.status_label.setText("Connecting…")
        self._controller.test_connection(probe)

    def _on_boards(self, boards: list) -> None:
        self.test_button.setEnabled(True)
        if not boards:
            self.status_label.setText(
                "Connected, but no Deck boards are shared with this account."
            )
            return

        previous = self.board_combo.currentData()
        self.board_combo.blockSignals(True)
        self.board_combo.clear()
        for board in boards:
            self.board_combo.addItem(board.get("title") or "(untitled)", board.get("id"))
        index = self.board_combo.findData(previous)
        self.board_combo.setCurrentIndex(index if index >= 0 else 0)
        self.board_combo.blockSignals(False)

        self.status_label.setText(
            f"Connected. Found {len(boards)} board{'s' if len(boards) != 1 else ''}."
        )
        self._on_board_selected()

    def _on_board_selected(self) -> None:
        board_id = self.board_combo.currentData()
        self._update_save_enabled()
        if board_id is None:
            return
        probe = self._current_credentials()
        if probe.server.is_complete():
            self._controller.fetch_stacks(probe, int(board_id))

    def _on_stacks(self, stacks: list) -> None:
        previous = self.stack_combo.currentData()
        self.stack_combo.blockSignals(True)
        self.stack_combo.clear()
        for stack in stacks:
            self.stack_combo.addItem(stack.get("title") or "(untitled)", stack.get("id"))
        index = self.stack_combo.findData(previous)
        self.stack_combo.setCurrentIndex(index if index >= 0 else 0)
        self.stack_combo.blockSignals(False)

        if not stacks:
            self.status_label.setText(
                "That board has no lists yet — create one in Deck first."
            )
            self._update_save_enabled()
            return

        self._update_save_enabled()

    def _on_error(self, message: str) -> None:
        self.test_button.setEnabled(True)
        self.status_label.setText(message)

    # -- result -----------------------------------------------------------

    def _on_save(self) -> None:
        self._config = self._current_credentials()

        board_id = self.board_combo.currentData()
        stack_id = self.stack_combo.currentData()
        self._config.board.id = int(board_id) if board_id is not None else None
        self._config.board.title = self.board_combo.currentText()
        self._config.board.default_stack_id = (
            int(stack_id) if stack_id is not None else None
        )
        self._config.board.default_stack_title = self.stack_combo.currentText()

        ui = self._config.ui
        ui.refresh_interval_minutes = self.interval_spin.value()
        ui.done_column_days = self.done_days_spin.value()
        ui.font_size_pt = self.font_size_spin.value()
        ui.card_title_lines = self.title_lines_spin.value()
        ui.visible_columns = [
            key for key, check in self.column_boxes.items() if check.isChecked()
        ]
        ui.date_format = self.date_format_edit.text().strip() or "%a %d %b"
        ui.refresh_on_show = self.refresh_on_show_box.isChecked()
        ui.compact_empty_columns = self.compact_empty_box.isChecked()
        ui.toggle_shortcut = self.toggle_shortcut_edit.keySequence().toString()
        ui.start_minimized = self.start_minimized_box.isChecked()
        self._config.autostart = self.autostart_box.isChecked()

        self.accept()

    def result_config(self) -> Config:
        return self._config

    def done(self, result: int) -> None:
        # The controller outlives this dialog, so drop our connections or the
        # next sync error would be delivered to a destroyed widget.
        for signal, slot in (
            (self._controller.connection_ok, self._on_boards),
            (self._controller.stacks_loaded, self._on_stacks),
            (self._controller.error, self._on_error),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().done(result)

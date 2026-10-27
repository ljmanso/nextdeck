"""The first start shows the window, whatever the tray preference says.

Someone who has just installed nextdeck — or copied a finished config.yaml in
from another machine — would otherwise see nothing at all: the app would go
straight to the tray and look like it had failed to start.
"""

from __future__ import annotations

from argparse import Namespace

import pytest

from nextdeck import state as statemod
from nextdeck.__main__ import _should_start_hidden
from nextdeck.config import BoardConfig, Config, ServerConfig


def flags(**overrides) -> Namespace:
    base = {"window": False, "tray": False, "toggle": False}
    base.update(overrides)
    return Namespace(**base)


def complete_config(start_minimized: bool = True) -> Config:
    config = Config(
        server=ServerConfig(url="https://x", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2),
    )
    config.ui.start_minimized = start_minimized
    return config


# -- the decision ---------------------------------------------------------


def test_the_first_run_shows_the_window_even_with_a_finished_config():
    """The case this exists for: a config copied in from another machine."""
    assert not _should_start_hidden(
        flags(), complete_config(), tray_available=True, first_run=True
    )


def test_later_runs_honour_the_preference():
    assert _should_start_hidden(
        flags(), complete_config(), tray_available=True, first_run=False
    )


def test_later_runs_still_show_the_window_when_told_to():
    assert not _should_start_hidden(
        flags(), complete_config(start_minimized=False),
        tray_available=True, first_run=False,
    )


def test_an_explicit_tray_flag_beats_the_first_run():
    """--tray is what autostart uses; an explicit request has to win."""
    assert _should_start_hidden(
        flags(tray=True), complete_config(), tray_available=True, first_run=True
    )


@pytest.mark.parametrize("flag", ["window", "toggle"])
def test_flags_that_ask_for_the_window_still_win(flag):
    assert not _should_start_hidden(
        flags(**{flag: True}), complete_config(),
        tray_available=True, first_run=False,
    )


def test_no_tray_means_never_hidden():
    assert not _should_start_hidden(
        flags(tray=True), complete_config(), tray_available=False, first_run=False
    )


def test_an_unconfigured_app_is_never_hidden():
    assert not _should_start_hidden(
        flags(), Config(), tray_available=True, first_run=False
    )


# -- the flag it reads ----------------------------------------------------


def test_a_missing_state_file_means_this_is_the_first_run(tmp_path):
    assert statemod.load(tmp_path / "nothing.yaml").has_run_before is False


def test_marking_makes_the_next_load_say_otherwise(tmp_path):
    path = tmp_path / "state.yaml"
    statemod.mark_has_run(path)
    assert statemod.load(path).has_run_before is True


def test_marking_keeps_the_saved_geometry(tmp_path):
    """It is a read-modify-write, not a fresh file."""
    path = tmp_path / "state.yaml"
    statemod.save(statemod.WindowState.from_geometry_bytes(b"\x01\x02\x03"), path)
    statemod.mark_has_run(path)

    state = statemod.load(path)
    assert state.has_run_before is True
    assert state.geometry_bytes() == b"\x01\x02\x03"


def test_marking_twice_is_harmless(tmp_path):
    path = tmp_path / "state.yaml"
    statemod.mark_has_run(path)
    statemod.mark_has_run(path)
    assert statemod.load(path).has_run_before is True


@pytest.mark.parametrize("value", ["yes", 1, None, "", "true"])
def test_only_a_real_true_counts_as_having_run(tmp_path, value):
    """A hand-edited or corrupt file should err towards showing the window."""
    path = tmp_path / "state.yaml"
    path.write_text(f"geometry: ''\nhas_run_before: {value!r}\n", encoding="utf-8")
    assert statemod.load(path).has_run_before is False


def test_saving_geometry_does_not_forget_the_flag(tmp_path, monkeypatch):
    """The window writes this file too, and must not reset what main() set."""
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QObject, Signal

    class Stub(QObject):
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

    from nextdeck.ui.window import MainWindow

    path = tmp_path / "state.yaml"
    statemod.mark_has_run(path)

    app = QApplication.instance() or QApplication([])
    win = MainWindow(complete_config(), Stub(), config_path=None, state_path=path)
    win.show()
    app.processEvents()
    win.save_geometry()

    state = statemod.load(path)
    assert state.has_run_before is True, "the flag survived a geometry save"
    assert state.geometry, "and the geometry was actually written"
    win.deleteLater()

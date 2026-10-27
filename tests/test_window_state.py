"""Remembering the window's size and position between runs.

The window is opened and dismissed many times a day, so reopening at a fixed
1250x720 every time is a small, constant irritation.
"""

from __future__ import annotations

import base64
import importlib
import os

import pytest

from nextdeck import state as statemod

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError:  # pragma: no cover - environment dependent
    # Unlike the other UI tests this module does not skip wholesale: the state
    # file itself is pure Python and worth testing without Qt.
    QT = False
else:
    QT = True

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

requires_qt = pytest.mark.skipif(not QT, reason="PySide6 is unusable here")


# -- the state file (no Qt needed) ----------------------------------------


def test_the_state_dir_honours_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdgstate"))
    assert statemod.state_path() == tmp_path / "xdgstate" / "nextdeck" / "state.yaml"


def test_state_lives_outside_the_config_file(tmp_path, monkeypatch):
    """The credential file must not be rewritten every time a window moves."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from nextdeck import config as cfgmod

    assert statemod.state_path() != cfgmod.config_path()


def test_a_missing_file_gives_defaults(tmp_path):
    assert statemod.load(tmp_path / "nothing.yaml").geometry == ""


def test_round_trip(tmp_path):
    path = tmp_path / "state.yaml"
    statemod.save(statemod.WindowState.from_geometry_bytes(b"\x01\x02\x03"), path)

    assert statemod.load(path).geometry_bytes() == b"\x01\x02\x03"


def test_the_file_is_readable_yaml(tmp_path):
    path = tmp_path / "state.yaml"
    statemod.save(statemod.WindowState.from_geometry_bytes(b"abc"), path)

    body = path.read_text()
    assert "geometry:" in body
    assert base64.b64encode(b"abc").decode() in body


def test_corrupt_yaml_is_ignored(tmp_path):
    path = tmp_path / "state.yaml"
    path.write_text("geometry: [this is: not valid")

    assert statemod.load(path).geometry == ""


def test_a_non_mapping_file_is_ignored(tmp_path):
    path = tmp_path / "state.yaml"
    path.write_text("- just\n- a list\n")

    assert statemod.load(path).geometry == ""


def test_a_non_string_geometry_is_ignored(tmp_path):
    path = tmp_path / "state.yaml"
    path.write_text("geometry: 12345\n")

    assert statemod.load(path).geometry == ""


@pytest.mark.parametrize(
    "value",
    [
        "not base64 at all!!",
        # Decodes fine if stray characters are quietly discarded, which would
        # hand restoreGeometry bytes that were never what we wrote.
        "aGVs*bG8h",
        "",
    ],
)
def test_geometry_that_is_not_clean_base64_is_ignored(tmp_path, value):
    path = tmp_path / "state.yaml"
    path.write_text(f"geometry: '{value}'\n")

    assert statemod.load(path).geometry_bytes() == b""


def test_saving_to_an_impossible_place_does_not_raise(tmp_path):
    """Losing the window size is a nuisance; failing to quit is not."""
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the directory should be")

    result = statemod.save(
        statemod.WindowState.from_geometry_bytes(b"x"), blocked / "state.yaml"
    )

    assert result is None


def test_saving_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "state.yaml"
    statemod.save(statemod.WindowState.from_geometry_bytes(b"x"), path)

    assert [p.name for p in tmp_path.iterdir()] == ["state.yaml"]


# -- the window actually using it -----------------------------------------


if QT:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication

    from nextdeck.config import BoardConfig, Config, ServerConfig, UIConfig
    from nextdeck.ui.window import MainWindow

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


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def make_window(state_file):
    config = Config(
        server=ServerConfig(url="https://x.example", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2, title="Work"),
        ui=UIConfig(),
    )
    return MainWindow(config, StubController(), config_path=None, state_path=state_file)


@requires_qt
def test_a_fresh_install_opens_at_the_default_size(qapp, tmp_path):
    win = make_window(tmp_path / "state.yaml")
    assert win.width() == 1250 and win.height() == 720
    win.deleteLater()


#: The offscreen platform gives us an 800x800 screen, and restoreGeometry
#: refuses to put a window outside the screen it is restoring onto, so test
#: sizes have to fit inside it.
SMALL = (700, 500)
SMALLER = (640, 460)


@requires_qt
def test_the_size_survives_a_restart(qapp, tmp_path):
    state = tmp_path / "state.yaml"
    first = make_window(state)
    first.show()
    first.resize(*SMALL)
    qapp.processEvents()
    first.close()
    first.deleteLater()

    second = make_window(state)

    assert (second.width(), second.height()) == SMALL
    second.deleteLater()


@requires_qt
def test_the_position_survives_a_restart(qapp, tmp_path):
    state = tmp_path / "state.yaml"
    first = make_window(state)
    first.show()
    first.resize(*SMALL)
    first.move(60, 40)
    qapp.processEvents()
    first.close()
    first.deleteLater()

    second = make_window(state)
    second.show()
    qapp.processEvents()

    assert (second.x(), second.y()) == (60, 40)
    second.deleteLater()


@requires_qt
def test_a_window_bigger_than_the_screen_is_brought_back_onto_it(qapp, tmp_path):
    """Unplugging the big monitor must not strand the window off-screen."""
    state = tmp_path / "state.yaml"
    first = make_window(state)
    first.show()
    first.resize(3000, 2000)
    qapp.processEvents()
    first.close()
    first.deleteLater()

    second = make_window(state)
    available = qapp.primaryScreen().availableGeometry()

    assert second.width() <= available.width()
    assert second.height() <= available.height()
    second.deleteLater()


@requires_qt
def test_closing_to_the_tray_records_the_geometry(qapp, tmp_path):
    """Hiding to the tray is the usual way the window goes away."""
    state = tmp_path / "state.yaml"
    win = make_window(state)
    win._tray_available = lambda: True
    win.show()
    win.resize(*SMALL)
    qapp.processEvents()

    win.close()

    assert not win.isVisible(), "hidden, not closed"
    assert statemod.load(state).geometry_bytes(), "nothing was recorded"
    win.deleteLater()


@requires_qt
def test_quitting_records_the_geometry(qapp, tmp_path):
    state = tmp_path / "state.yaml"
    win = make_window(state)
    win.show()
    win.resize(*SMALL)
    qapp.processEvents()

    win.prepare_quit()

    assert statemod.load(state).geometry_bytes()
    win.deleteLater()


@requires_qt
def test_a_later_close_overwrites_an_earlier_one(qapp, tmp_path):
    state = tmp_path / "state.yaml"
    win = make_window(state)
    win._tray_available = lambda: True
    win.show()
    win.resize(*SMALL)
    qapp.processEvents()
    win.close()

    win.show()
    win.resize(*SMALLER)
    qapp.processEvents()
    win.close()
    win.deleteLater()

    reopened = make_window(state)
    assert (reopened.width(), reopened.height()) == SMALLER
    reopened.deleteLater()


@requires_qt
def test_unusable_saved_geometry_falls_back_to_the_default(qapp, tmp_path):
    """A blob from another Qt, or plain nonsense, must not break startup."""
    state = tmp_path / "state.yaml"
    state.write_text(
        "geometry: " + base64.b64encode(b"not a Qt geometry blob").decode() + "\n"
    )

    win = make_window(state)

    assert win.width() == 1250 and win.height() == 720
    win.deleteLater()

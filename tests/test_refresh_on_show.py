"""Refreshing when the window comes back into view.

The window spends most of its life hidden in the tray, so the moment it is
opened is when stale data is most likely — and most visible.
"""

from __future__ import annotations

import importlib
import os

import pytest

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"PySide6 is unusable here: {exc}", allow_module_level=True)

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nextdeck.config import BoardConfig, Config, ServerConfig, UIConfig  # noqa: E402
from nextdeck.ui import window as winmod  # noqa: E402
from nextdeck.ui.window import MainWindow  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class CountingController(QObject):
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
        self.refreshes = 0

    def refresh(self):
        self.refreshes += 1

    def stop(self):
        pass


def make_window(qapp, complete: bool = True, **ui_kw):
    server = (
        ServerConfig(url="https://x.example", username="u", app_password="p")
        if complete
        else ServerConfig(url="", username="", app_password="")
    )
    board = BoardConfig(id=1, default_stack_id=2) if complete else BoardConfig()
    config = Config(server=server, board=board, ui=UIConfig(**ui_kw))
    controller = CountingController()
    win = MainWindow(config, controller, config_path=None)
    return win, controller


def synced(win) -> None:
    """Pretend a first sync has already come back."""
    win._on_tasks_loaded([])


def cool_down(win) -> None:
    """Step past the anti-flicker cooldown without actually sleeping."""
    win._last_show_refresh -= winmod.SHOW_REFRESH_COOLDOWN_S + 1


def test_showing_the_window_refreshes(qapp):
    win, controller = make_window(qapp)
    synced(win)
    before = controller.refreshes

    win.show()
    qapp.processEvents()

    assert controller.refreshes == before + 1
    win.deleteLater()


def test_hiding_and_showing_again_refreshes_each_time(qapp):
    win, controller = make_window(qapp)
    synced(win)
    win.show()
    qapp.processEvents()
    after_first = controller.refreshes

    for _ in range(3):
        win.hide()
        cool_down(win)
        win.show()
        qapp.processEvents()

    assert controller.refreshes == after_first + 3
    win.deleteLater()


def test_hiding_does_not_refresh(qapp):
    win, controller = make_window(qapp)
    synced(win)
    win.show()
    qapp.processEvents()
    after_show = controller.refreshes

    win.hide()
    qapp.processEvents()

    assert controller.refreshes == after_show
    win.deleteLater()


def test_the_option_can_be_switched_off(qapp):
    win, controller = make_window(qapp, refresh_on_show=False)
    synced(win)
    before = controller.refreshes

    win.show()
    qapp.processEvents()
    win.hide()
    win.show()
    qapp.processEvents()

    assert controller.refreshes == before
    win.deleteLater()


def test_flicking_the_window_in_and_out_does_not_hammer_the_server(qapp):
    """showEvent and show_and_raise can both fire for a single toggle."""
    win, controller = make_window(qapp)
    synced(win)
    before = controller.refreshes

    for _ in range(5):
        win.toggle_visibility()
        qapp.processEvents()

    assert controller.refreshes == before + 1, "the cooldown should collapse these"
    win.deleteLater()


def test_the_very_first_show_leaves_the_initial_refresh_alone(qapp):
    """Startup fires its own first refresh; showing must not duplicate it."""
    win, controller = make_window(qapp)

    win.show()
    qapp.processEvents()

    assert controller.refreshes == 0
    win.deleteLater()


def test_a_show_after_a_failed_first_sync_retries(qapp):
    win, controller = make_window(qapp)
    win._on_error("server unreachable")
    before = controller.refreshes

    win.show()
    qapp.processEvents()

    assert controller.refreshes == before + 1
    win.deleteLater()


def test_an_unconfigured_window_does_not_try_to_refresh(qapp):
    """First run shows the window with no server to ask."""
    win, controller = make_window(qapp, complete=False)
    synced(win)

    win.show()
    qapp.processEvents()

    assert controller.refreshes == 0
    win.deleteLater()


def test_raising_an_already_visible_window_refreshes(qapp):
    """The path a real window manager takes when restoring from minimised.

    Qt counts a minimised window as visible, so no showEvent arrives when it
    is restored on a real desktop — only show_and_raise's own call does. The
    offscreen plugin does not model minimising, so this drives the same shape
    directly: raising a window that is already visible.
    """
    win, controller = make_window(qapp)
    synced(win)
    win.show()
    qapp.processEvents()
    assert win.isVisible()
    cool_down(win)
    before = controller.refreshes

    win.show_and_raise()
    qapp.processEvents()

    assert controller.refreshes == before + 1
    win.deleteLater()


def test_the_timer_is_unaffected(qapp):
    """The option adds to the timed check, it does not replace it."""
    win, _ = make_window(qapp, refresh_on_show=True, refresh_interval_minutes=10)
    assert win._refresh_timer.isActive()
    assert win._refresh_timer.interval() == 10 * 60_000
    win.deleteLater()

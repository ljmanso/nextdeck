"""Threading contract for SyncController.

The worker runs on its own thread. Anything it reports must reach the GUI
thread before a consumer sees it, including a plain function or lambda — Qt
cannot queue a delivery for a callable with no QObject receiver, so without the
explicit relay such a callback runs on the worker thread, where touching a
widget is undefined behaviour.
"""

from __future__ import annotations

import importlib
import threading

import pytest

try:
    importlib.import_module("PySide6.QtCore")
except ImportError as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"PySide6 is unusable here: {exc}", allow_module_level=True)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nextdeck.config import BoardConfig, Config, ServerConfig  # noqa: E402
from nextdeck.sync import SyncController, SyncWorker  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def unreachable_config() -> Config:
    # Port 1 refuses immediately, so the worker fails fast with no network.
    return Config(
        server=ServerConfig(
            url="http://127.0.0.1:1", username="u", app_password="p"
        ),
        board=BoardConfig(id=1, default_stack_id=2),
    )


def test_controller_owns_its_signals_rather_than_reexposing_the_workers():
    assert SyncController.error is not SyncWorker.error
    assert SyncController.tasks_loaded is not SyncWorker.tasks_loaded


def test_results_reach_a_plain_callback_on_the_main_thread(qapp):
    controller = SyncController(unreachable_config())
    seen: dict[str, object] = {}

    # Deliberately a closure, not a QObject slot: the footgun case.
    def on_error(message: str) -> None:
        seen["thread"] = threading.get_ident()
        seen["message"] = message
        qapp.quit()

    controller.error.connect(on_error)
    controller.refresh()

    guard = QTimer()
    guard.singleShot(20000, qapp.quit)
    qapp.exec()
    controller.stop()

    assert "message" in seen, "the worker never reported the failure"
    assert seen["thread"] == threading.main_thread().ident, (
        "callback ran on the worker thread; the queued relay is not working"
    )
    assert "Could not reach the server" in str(seen["message"])


def test_stop_shuts_the_worker_thread_down(qapp):
    controller = SyncController(unreachable_config())
    controller.stop()
    assert not controller._thread.isRunning()

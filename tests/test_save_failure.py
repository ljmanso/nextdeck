"""A save that does not reach the server must say so.

Every other write is one click and trivially redone. A save carries typed
text, and the optimistic render is rolled back when it fails, so the only
thing standing between the user and a silently lost paragraph is this report.
"""

from __future__ import annotations

import importlib
import os
import threading
from datetime import date, datetime, timedelta

import pytest

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"PySide6 is unusable here: {exc}", allow_module_level=True)

from PySide6.QtCore import QObject, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from nextdeck.api import DeckError  # noqa: E402
from nextdeck.config import BoardConfig, Config, ServerConfig, UIConfig  # noqa: E402
from nextdeck.model import Bucket, Task  # noqa: E402
from nextdeck.sync import SyncController, SyncWorker  # noqa: E402
from nextdeck.ui.window import MainWindow  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TODAY = date.today()


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def task(task_id: int = 1, title: str = "Write the report") -> Task:
    due = datetime.combine(TODAY, datetime.min.time()).replace(hour=9).astimezone()
    return Task(
        id=task_id,
        title=title,
        board_id=1,
        stack_id=2,
        stack_title="ToDo",
        duedate=due,
        done=None,
        description="original notes",
        raw={"id": task_id, "stackId": 2, "title": title},
    )


# -- the worker reports which task failed ---------------------------------


class BrokenClient:
    def update_task(self, *a, **kw):
        raise DeckError("Nextcloud rejected the credentials")

    def close(self):
        pass


class WorkingClient:
    def __init__(self):
        self.calls = 0

    def update_task(self, task, **changes):
        self.calls += 1
        return {"id": task.id}

    def get_stacks(self, board_id):
        return []

    def close(self):
        pass


def worker_with(client) -> SyncWorker:
    config = Config(
        server=ServerConfig(url="https://x.example", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2),
    )
    worker = SyncWorker(config)
    worker._client = client
    return worker


def test_a_failed_save_names_the_task_and_the_reason(qapp):
    worker = worker_with(BrokenClient())
    failures: list[tuple] = []
    worker.save_failed.connect(lambda t, reason: failures.append((t, reason)))

    subject = task()
    worker.save_task(subject, "new title", "new body", None)

    assert len(failures) == 1
    failed_task, reason = failures[0]
    assert failed_task is subject, "the view has to know which card was lost"
    assert "credentials" in reason


def test_a_failed_save_still_reports_the_generic_error(qapp):
    """The generic error is what rolls the optimistic render back."""
    worker = worker_with(BrokenClient())
    errors: list[str] = []
    worker.error.connect(errors.append)

    worker.save_task(task(), "new title", "new body", None)

    assert errors and "credentials" in errors[0]


def test_a_successful_save_reports_no_failure(qapp):
    worker = worker_with(WorkingClient())
    failures: list[tuple] = []
    worker.save_failed.connect(lambda t, reason: failures.append((t, reason)))

    worker.save_task(task(), "new title", "new body", None)

    assert failures == []


def test_other_writes_do_not_raise_a_save_failure(qapp):
    """Completing or deleting is one click; only saves get the loud treatment."""
    worker = worker_with(BrokenClient())
    failures: list[tuple] = []
    worker.save_failed.connect(lambda t, reason: failures.append((t, reason)))

    worker.set_done(task(), True)
    worker.delete_task(task())

    assert failures == []


def test_the_failure_reaches_the_gui_thread(qapp):
    """A plain callback must not be run on the worker thread."""
    config = Config(
        # Port 1 refuses immediately, so this fails fast with no network.
        server=ServerConfig(url="http://127.0.0.1:1", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2),
    )
    controller = SyncController(config)
    seen: dict[str, object] = {}

    def on_failed(t, reason):
        seen["thread"] = threading.get_ident()
        seen["title"] = t.title
        qapp.quit()

    controller.save_failed.connect(on_failed)
    controller.save_task(task(), "new title", "new body", None)

    guard = QTimer()
    guard.singleShot(20000, qapp.quit)
    qapp.exec()
    controller.stop()

    assert seen.get("title") == "Write the report"
    assert seen["thread"] == threading.main_thread().ident


def test_the_controller_does_not_reexpose_the_workers_signal():
    assert SyncController.save_failed is not SyncWorker.save_failed


# -- the window says so out loud ------------------------------------------


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

    def save_task(self, *a):
        pass

    def stop(self):
        pass


@pytest.fixture
def window(qapp):
    config = Config(
        server=ServerConfig(url="https://x.example", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2, title="Work"),
        ui=UIConfig(),
    )
    controller = StubController()
    win = MainWindow(config, controller, config_path=None)
    yield win, controller
    win.deleteLater()


@pytest.fixture
def shown_dialogs(monkeypatch):
    """Capture message boxes instead of blocking on them."""
    shown: list[QMessageBox] = []

    def fake_exec(self):
        shown.append(self)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return shown


def test_a_failed_save_raises_a_dialog_naming_the_card(window, shown_dialogs):
    win, controller = window
    subject = task(title="Write the report")
    controller.tasks_loaded.emit([subject])

    controller.save_failed.emit(subject, "Nextcloud rejected the credentials")

    assert len(shown_dialogs) == 1, "a status line alone is too quiet for lost text"
    box = shown_dialogs[0]
    assert "Write the report" in box.text()
    assert "credentials" in box.informativeText()
    assert box.icon() == QMessageBox.Icon.Warning


def failed_save(win, controller, subject, title, description, due, reason="boom"):
    """Drive a full editor save that the server then refuses."""
    win._on_editor_save(subject, title, description, due)
    controller.error.emit(reason)
    controller.save_failed.emit(subject, reason)


def test_the_typed_text_is_handed_back_in_the_editor(window, shown_dialogs):
    """The whole point: the work must still be on screen to copy out."""
    win, controller = window
    subject = task(title="Write the report")
    controller.tasks_loaded.emit([subject])

    failed_save(win, controller, subject, "edited title", "a paragraph of notes",
                subject.duedate)

    assert not win.editor.isHidden(), "the panel must be open"
    assert win.editor.task is not None and win.editor.task.id == subject.id
    assert win.editor.title_edit.text() == "edited title"
    assert win.editor.description_edit.toPlainText() == "a paragraph of notes"


def test_the_attempted_due_date_is_handed_back_too(window, shown_dialogs):
    win, controller = window
    subject = task()
    controller.tasks_loaded.emit([subject])
    moved = subject.duedate + timedelta(days=3)

    failed_save(win, controller, subject, subject.title, subject.description, moved)

    value = win.editor._due_widget.value()
    assert value is not None
    assert value.date() == moved.date(), "not the server's date"


def test_a_cleared_due_date_is_handed_back_as_cleared(window, shown_dialogs):
    """`None` must mean "they cleared it", not "fall back to the task"."""
    win, controller = window
    subject = task()
    controller.tasks_loaded.emit([subject])

    failed_save(win, controller, subject, subject.title, subject.description, None)

    assert win.editor._due_widget.value() is None


def test_the_dialog_says_where_the_changes_are(window, shown_dialogs):
    win, controller = window
    subject = task()
    controller.tasks_loaded.emit([subject])

    failed_save(win, controller, subject, "edited", "notes", subject.duedate,
                reason="Not found on the server")

    body = shown_dialogs[0].informativeText().lower()
    assert "still in the editor" in body
    assert "escape" in body, "and how to drop them"


def test_escape_still_discards_the_handed_back_edit(window, shown_dialogs):
    win, controller = window
    subject = task()
    controller.tasks_loaded.emit([subject])
    failed_save(win, controller, subject, "edited", "notes", subject.duedate)

    win.editor.cancel()

    assert win.editor.isHidden()
    assert win.columns[Bucket.TODAY].cards()[0].task.title == subject.title


def test_saving_again_resends_the_same_values(window, shown_dialogs):
    win, controller = window
    subject = task()
    controller.tasks_loaded.emit([subject])
    failed_save(win, controller, subject, "edited title", "notes", subject.duedate)

    sent: list[tuple] = []
    win.editor.save_requested.connect(
        lambda t, ti, d, due: sent.append((t.id, ti, d))
    )
    win.editor.save_and_close()

    assert sent == [(subject.id, "edited title", "notes")]


def test_a_failure_with_nothing_attempted_does_not_open_the_editor(
    window, shown_dialogs
):
    """A stale failure should not conjure an editor out of nowhere."""
    win, controller = window
    subject = task()
    controller.tasks_loaded.emit([subject])

    controller.save_failed.emit(subject, "Not found on the server")

    assert win.editor.isHidden()
    assert "not applied" in shown_dialogs[0].informativeText().lower()


def test_the_status_line_also_records_it(window, shown_dialogs):
    win, controller = window
    subject = task(title="Write the report")
    controller.tasks_loaded.emit([subject])

    controller.save_failed.emit(subject, "Not found on the server")

    assert "Write the report" in win.status_label.text()
    assert "Not saved" in win.status_label.text()
    # The generic error flashes first; the specific one has to win.
    assert "Write the report" in win.statusBar().currentMessage()


def test_the_card_still_shows_the_server_version(window, shown_dialogs):
    """The rollback and the report have to agree with each other."""
    win, controller = window
    subject = task(title="Write the report")
    controller.tasks_loaded.emit([subject])

    # What the editor does: render optimistically, then the write fails.
    win._on_editor_save(subject, "edited title", "edited body", subject.duedate)
    assert win.columns[Bucket.TODAY].cards()[0].task.title == "edited title"

    controller.error.emit("Nextcloud rejected the credentials")
    controller.save_failed.emit(subject, "Nextcloud rejected the credentials")

    card = win.columns[Bucket.TODAY].cards()[0]
    assert card.task.title == "Write the report", "rolled back to server truth"
    assert len(shown_dialogs) == 1


def test_an_untitled_task_still_produces_a_readable_message(window, shown_dialogs):
    win, controller = window
    subject = task(title="")
    controller.tasks_loaded.emit([subject])

    controller.save_failed.emit(subject, "boom")

    assert "(untitled)" in shown_dialogs[0].text()

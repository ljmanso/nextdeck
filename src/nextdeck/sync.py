"""Background synchronisation.

All HTTP happens on a worker thread so the board never freezes while talking to
a remote Nextcloud. The GUI only ever touches `SyncController`, whose public
methods are safe to call from the main thread: each one emits a signal that Qt
queues onto the worker thread.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from .api import DeckClient, DeckError
from .config import Config
from .model import Task, tasks_from_stacks

log = logging.getLogger(__name__)


class SyncWorker(QObject):
    """Lives on the worker thread. Never touch its attributes from the GUI."""

    tasks_loaded = Signal(list)
    error = Signal(str)
    busy_changed = Signal(bool)
    #: Emitted after a successful write, carrying a short status message.
    mutated = Signal(str)
    #: (task, reason) when an editor's save did not reach the server. Saves get
    #: their own signal because they are the one write carrying text the user
    #: cannot trivially retype, so the view has to name what was lost.
    save_failed = Signal(object, str)
    #: The board's label set, for the editor to offer.
    labels_loaded = Signal(list)
    boards_loaded = Signal(list)
    stacks_loaded = Signal(list)
    connection_ok = Signal(list)

    def __init__(self, config: Config):
        super().__init__()
        self._config = config
        self._client: DeckClient | None = None

    # -- client lifecycle -------------------------------------------------

    def _ensure_client(self) -> DeckClient:
        if self._client is None:
            server = self._config.server
            if not server.is_complete():
                raise DeckError("nextdeck is not configured yet")
            self._client = DeckClient(
                server.url, server.username, server.app_password, server.verify_ssl
            )
        return self._client

    @Slot(object)
    def set_config(self, config: Config) -> None:
        """Swap in new settings, discarding any client built from the old ones."""
        self._config = config
        self._close_client()

    def _close_client(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @Slot()
    def shutdown(self) -> None:
        self._close_client()

    # -- operations -------------------------------------------------------

    def _run(self, work, success_message: str | None = None, on_failure=None):
        """Run `work`, reporting busy state and turning DeckError into a signal.

        `on_failure` gets the reason as well, for callers that need to say more
        than the generic error line does.
        """
        self.busy_changed.emit(True)
        try:
            result = work()
        except DeckError as exc:
            self.error.emit(str(exc))
            if on_failure is not None:
                on_failure(str(exc))
            return None
        except Exception as exc:  # pragma: no cover - last-resort guard
            log.exception("unexpected sync failure")
            reason = f"Unexpected error: {exc}"
            self.error.emit(reason)
            if on_failure is not None:
                on_failure(reason)
            return None
        finally:
            self.busy_changed.emit(False)

        if success_message is not None:
            self.mutated.emit(success_message)
        return result

    @Slot()
    def refresh(self) -> None:
        board_id = self._config.board.id
        if board_id is None:
            return

        def work():
            client = self._ensure_client()
            return tasks_from_stacks(client.get_stacks(board_id), board_id)

        tasks = self._run(work)
        if tasks is not None:
            self.tasks_loaded.emit(tasks)

    @Slot(str, str, object)
    def create_task(
        self, title: str, description: str, duedate: datetime | None
    ) -> None:
        board = self._config.board
        if board.id is None or board.default_stack_id is None:
            self.error.emit("No board is configured to add the task to")
            return

        def work():
            return self._ensure_client().create_card(
                board.id, board.default_stack_id, title, description, duedate
            )

        if self._run(work, f"Added “{title}”") is not None:
            self.refresh()

    @Slot(object, bool)
    def set_done(self, task: Task, done: bool) -> None:
        verb = "Completed" if done else "Reopened"

        def work():
            return self._ensure_client().set_done(task, done)

        if self._run(work, f"{verb} “{task.title}”") is not None:
            self.refresh()

    @Slot(object, object)
    def set_duedate(self, task: Task, duedate: datetime | None) -> None:
        def work():
            return self._ensure_client().set_duedate(task, duedate)

        message = "Cleared the due date" if duedate is None else "Rescheduled"
        if self._run(work, f"{message} for “{task.title}”") is not None:
            self.refresh()

    @Slot()
    def fetch_labels(self) -> None:
        """Load the board's label set. Quiet about failing: labels are a
        convenience, and the board itself has already reported any outage."""
        board_id = self._config.board.id
        if not board_id:
            return
        try:
            board = self._ensure_client().get_board(board_id)
        except Exception as exc:  # noqa: BLE001 - labels are never fatal
            log.warning("could not load the board's labels: %s", exc)
            return
        from .model import labels_from_board

        self.labels_loaded.emit(labels_from_board(board))

    @Slot(object, str, str, object, object)
    def save_task(
        self,
        task: Task,
        title: str,
        description: str,
        duedate: datetime | None,
        label_ids: list[int] | None = None,
    ) -> None:
        """Apply an editor's worth of changes.

        The card fields go in one request; labels need their own routes, so
        only the ones that actually changed are touched.
        """

        def work():
            from .model import format_datetime

            client = self._ensure_client()
            updated = client.update_task(
                task,
                title=title,
                description=description,
                duedate=format_datetime(duedate),
            )
            if label_ids is not None:
                wanted = set(label_ids)
                current = set(task.label_ids)
                for label_id in sorted(wanted - current):
                    client.assign_label(task, label_id)
                for label_id in sorted(current - wanted):
                    client.remove_label(task, label_id)
            return updated

        if self._run(
            work,
            f"Saved “{title}”",
            on_failure=lambda reason: self.save_failed.emit(task, reason),
        ) is not None:
            self.refresh()

    @Slot(object, object)
    def reopen_with_duedate(self, task: Task, duedate: datetime | None) -> None:
        """Un-complete a task and set its due date in one request."""

        def work():
            from .model import format_datetime

            return self._ensure_client().update_task(
                task, done=None, duedate=format_datetime(duedate)
            )

        if self._run(work, f"Reopened “{task.title}”") is not None:
            self.refresh()

    @Slot(object)
    def delete_task(self, task: Task) -> None:
        def work():
            self._ensure_client().delete_card(task)
            return True

        if self._run(work, f"Deleted “{task.title}”") is not None:
            self.refresh()

    # -- settings dialog helpers -----------------------------------------

    @Slot(object)
    def test_connection(self, config: Config) -> None:
        """Validate credentials from the settings dialog and list boards.

        Uses a throwaway client so the live session is untouched if the
        credentials being tried turn out to be wrong.
        """
        self.busy_changed.emit(True)
        try:
            server = config.server
            client = DeckClient(
                server.url, server.username, server.app_password, server.verify_ssl
            )
            try:
                boards = client.get_boards()
            finally:
                client.close()
        except DeckError as exc:
            self.error.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover
            self.error.emit(f"Unexpected error: {exc}")
            return
        finally:
            self.busy_changed.emit(False)
        self.connection_ok.emit(boards)

    @Slot(object, int)
    def fetch_stacks(self, config: Config, board_id: int) -> None:
        """List stacks for a board so the default-stack picker can be filled."""
        self.busy_changed.emit(True)
        try:
            server = config.server
            client = DeckClient(
                server.url, server.username, server.app_password, server.verify_ssl
            )
            try:
                stacks = client.get_stacks(board_id)
            finally:
                client.close()
        except DeckError as exc:
            self.error.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover
            self.error.emit(f"Unexpected error: {exc}")
            return
        finally:
            self.busy_changed.emit(False)
        self.stacks_loaded.emit(stacks)


class SyncController(QObject):
    """Main-thread handle on the worker. Call these methods from the GUI.

    The worker's own signals are deliberately *not* exposed. They are emitted
    on the worker thread, and Qt only queues a delivery when the receiver is a
    QObject it can find — connect a plain function or lambda to one and the
    callback runs on the worker thread, where touching a widget is undefined
    behaviour. So each worker signal is relayed through an explicit
    QueuedConnection onto this object's thread and re-emitted from here, which
    makes these signals safe for any callable to connect to.
    """

    # Outbound, always emitted on this object's (the GUI's) thread.
    tasks_loaded = Signal(list)
    error = Signal(str)
    busy_changed = Signal(bool)
    mutated = Signal(str)
    save_failed = Signal(object, str)
    #: The board's label set, for the editor to offer.
    labels_loaded = Signal(list)
    connection_ok = Signal(list)
    stacks_loaded = Signal(list)

    # Internal request signals, connected to the worker's slots so that Qt
    # queues the call onto the worker thread.
    _refresh_requested = Signal()
    _create_requested = Signal(str, str, object)
    _done_requested = Signal(object, bool)
    _duedate_requested = Signal(object, object)
    _save_requested = Signal(object, str, str, object, object)
    _labels_requested = Signal()
    _reopen_requested = Signal(object, object)
    _delete_requested = Signal(object)
    _config_changed = Signal(object)
    _test_requested = Signal(object)
    _stacks_requested = Signal(object, int)
    _shutdown_requested = Signal()

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(parent)
        self._thread = QThread()
        self._thread.setObjectName("nextdeck-sync")
        self.worker = SyncWorker(config)
        self.worker.moveToThread(self._thread)

        self._refresh_requested.connect(self.worker.refresh)
        self._create_requested.connect(self.worker.create_task)
        self._done_requested.connect(self.worker.set_done)
        self._duedate_requested.connect(self.worker.set_duedate)
        self._save_requested.connect(self.worker.save_task)
        self._labels_requested.connect(self.worker.fetch_labels)
        self._reopen_requested.connect(self.worker.reopen_with_duedate)
        self._delete_requested.connect(self.worker.delete_task)
        self._config_changed.connect(self.worker.set_config)
        self._test_requested.connect(self.worker.test_connection)
        self._stacks_requested.connect(self.worker.fetch_stacks)
        self._shutdown_requested.connect(self.worker.shutdown)

        # Hop every result back onto this thread before anything sees it.
        queued = Qt.ConnectionType.QueuedConnection
        self.worker.tasks_loaded.connect(self._relay_tasks, queued)
        self.worker.error.connect(self._relay_error, queued)
        self.worker.busy_changed.connect(self._relay_busy, queued)
        self.worker.mutated.connect(self._relay_mutated, queued)
        self.worker.save_failed.connect(self._relay_save_failed, queued)
        self.worker.labels_loaded.connect(self._relay_labels, queued)
        self.worker.connection_ok.connect(self._relay_connection_ok, queued)
        self.worker.stacks_loaded.connect(self._relay_stacks, queued)

        self._thread.start()

    # -- relays (run on this object's thread) -----------------------------

    @Slot(list)
    def _relay_tasks(self, tasks: list) -> None:
        self.tasks_loaded.emit(tasks)

    @Slot(str)
    def _relay_error(self, message: str) -> None:
        self.error.emit(message)

    @Slot(bool)
    def _relay_busy(self, busy: bool) -> None:
        self.busy_changed.emit(busy)

    @Slot(str)
    def _relay_mutated(self, message: str) -> None:
        self.mutated.emit(message)

    @Slot(object, str)
    def _relay_save_failed(self, task, reason: str) -> None:
        self.save_failed.emit(task, reason)

    @Slot(list)
    def _relay_labels(self, labels: list) -> None:
        self.labels_loaded.emit(labels)

    @Slot(list)
    def _relay_connection_ok(self, boards: list) -> None:
        self.connection_ok.emit(boards)

    @Slot(list)
    def _relay_stacks(self, stacks: list) -> None:
        self.stacks_loaded.emit(stacks)

    # -- requests ---------------------------------------------------------

    def refresh(self) -> None:
        self._refresh_requested.emit()

    def create_task(
        self, title: str, description: str = "", duedate: datetime | None = None
    ) -> None:
        self._create_requested.emit(title, description, duedate)

    def set_done(self, task: Task, done: bool) -> None:
        self._done_requested.emit(task, done)

    def set_duedate(self, task: Task, duedate: datetime | None) -> None:
        self._duedate_requested.emit(task, duedate)

    @Slot(object, str, str, object, object)
    def save_task(
        self,
        task: Task,
        title: str,
        description: str,
        duedate: datetime | None,
        label_ids: list[int] | None = None,
    ) -> None:
        self._save_requested.emit(task, title, description, duedate, label_ids)

    def fetch_labels(self) -> None:
        self._labels_requested.emit()

    def reopen_with_duedate(self, task: Task, duedate: datetime | None) -> None:
        self._reopen_requested.emit(task, duedate)

    def delete_task(self, task: Task) -> None:
        self._delete_requested.emit(task)

    def apply_config(self, config: Config) -> None:
        self._config_changed.emit(config)

    def test_connection(self, config: Config) -> None:
        self._test_requested.emit(config)

    def fetch_stacks(self, config: Config, board_id: int) -> None:
        self._stacks_requested.emit(config, board_id)

    def stop(self) -> None:
        """Shut the worker thread down cleanly."""
        self._shutdown_requested.emit()
        self._thread.quit()
        self._thread.wait(5000)

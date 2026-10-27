"""One running nextdeck per user, per config.

The app lives in the tray, so a second launch — from the application menu, a
dock, or a terminal — would otherwise leave two tray icons, two refresh timers
and two windows writing the same board. Instead the newcomer hands its request
to the instance already running and exits, which turns the menu entry into a
"show me the board" button.

Keyed by config path as well as user, so `--config work.yaml` and
`--config home.yaml` are deliberately separate instances.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# The names and the socket itself live in a module that imports no Qt, so that
# a hotkey can reach a running instance without paying for a Qt import first.
from .ipc import SHOW_REQUEST, TOGGLE_REQUEST, server_name

log = logging.getLogger(__name__)

#: Long enough for a loaded machine, short enough not to delay a cold start.
CONNECT_TIMEOUT_MS = 500

__all__ = [
    "CONNECT_TIMEOUT_MS",
    "SHOW_REQUEST",
    "TOGGLE_REQUEST",
    "SingleInstance",
    "server_name",
]


class SingleInstance(QObject):
    """Claims the name, or finds whoever already holds it."""

    #: Emitted when another launch asked this instance to show itself.
    show_requested = Signal()
    #: Emitted when another launch asked it to show or hide the window.
    toggle_requested = Signal()

    def __init__(self, config_path: Path | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.name = server_name(config_path)
        self.config_path = config_path
        self._server: QLocalServer | None = None

    # -- the newcomer's side ----------------------------------------------

    def ping_existing(self, request: bytes = SHOW_REQUEST) -> bool:
        """Ask a running instance to show itself. True if one answered."""
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        socket.write(request)
        socket.flush()
        socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        socket.disconnectFromServer()
        return True

    # -- the holder's side -------------------------------------------------

    def claim(self) -> bool:
        """Become the instance others defer to. True if the name is ours."""
        server = QLocalServer(self)
        # A crash leaves the socket file behind and listen() then fails even
        # though nobody is home. ping_existing() has already established that
        # nothing answers, so a leftover is safe to clear.
        QLocalServer.removeServer(self.name)
        if not server.listen(self.name):
            log.warning(
                "could not listen on %s: %s", self.name, server.errorString()
            )
            return False
        server.newConnection.connect(self._on_connection)
        self._server = server
        # Where Qt put the socket is Qt's business; writing it down is what
        # lets a hotkey find it again without importing Qt to ask.
        from . import ipc

        ipc.remember_socket(server.fullServerName(), self.config_path)
        return True

    def _on_connection(self) -> None:
        if self._server is None:
            return
        while True:
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            socket.readyRead.connect(lambda s=socket: self._on_ready(s))
            socket.disconnected.connect(socket.deleteLater)

    def _on_ready(self, socket: QLocalSocket) -> None:
        request = bytes(socket.readAll().data()).strip()
        if request == TOGGLE_REQUEST.strip():
            self.toggle_requested.emit()
        elif request == SHOW_REQUEST.strip():
            # Anything unrecognised is ignored rather than treated as "show":
            # a future request must not make an old instance pop up a window.
            self.show_requested.emit()

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            QLocalServer.removeServer(self.name)
            self._server = None
            from . import ipc

            ipc.forget_socket(self.config_path)

"""Reaching a running nextdeck without starting Qt.

A desktop hotkey runs `nextdeck --toggle`, and that process exists only to
write seven bytes to a socket the running instance is already listening on.
Doing it through Qt means importing PySide6 and the whole UI package first,
which measured 734 ms on an idle machine — three quarters of a second of
nothing happening after a key press, which reads as the *app* being slow
rather than the launcher that started it.

Nothing about sending those seven bytes needs Qt, so this module does it with
the standard library alone, and `__main__` reaches for Qt only when nothing
answers — by which point there is an application to start anyway and the
startup cost is the point rather than the problem.

The socket is Qt's, though, so where it lives is Qt's business and not
something to hard-code: the running instance writes its own resolved path
down (`remember_socket`), and this reads that. The guesses are only there for
an instance started by a version that did not write it yet, and getting them
wrong costs nothing but the slow path that used to be the only one.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
from pathlib import Path

log = logging.getLogger(__name__)

#: What a newcomer sends to ask the running instance to show itself.
SHOW_REQUEST = b"show\n"
#: `--toggle` instead asks it to hide the window if it is already up, which is
#: what a desktop-wide hotkey needs: one key that both summons and dismisses.
TOGGLE_REQUEST = b"toggle\n"

#: A socket on this machine either answers at once or is not there at all, so
#: this only has to cover a badly loaded machine, not a network round trip.
CONNECT_TIMEOUT_S = 1.0


def server_name(config_path: Path | None) -> str:
    """A socket name unique to this user and config file."""
    target = str(Path(config_path).expanduser().resolve()) if config_path else "default"
    digest = hashlib.sha256(target.encode("utf-8")).hexdigest()[:12]
    try:
        uid = os.getuid()
    except AttributeError:  # pragma: no cover - not POSIX
        uid = 0
    return f"nextdeck-{uid}-{digest}"


def record_path(config_path: Path | None = None) -> Path:
    """Where the running instance writes the socket's real location.

    `$XDG_RUNTIME_DIR` is the right home for this: it is per-session, cleared
    when the session ends, and a stale record left by a crash is harmless
    anyway — connecting to it fails and the slow path takes over.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime) if runtime else Path("/tmp")
    return base / f"{server_name(config_path)}.socket"


def remember_socket(full_name: str, config_path: Path | None = None) -> None:
    """Write down where the socket actually is. Never raises."""
    if not full_name:
        return
    path = record_path(config_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_NOFOLLOW so a symlink planted at a predictable name in a shared
        # /tmp — which is where this lands when XDG_RUNTIME_DIR is unset —
        # cannot redirect the write somewhere else, and 0600 because nobody
        # else has any business reading it.
        # O_NONBLOCK as well, because opening a FIFO for writing waits for a
        # reader: a named pipe sitting at this path, planted or left over,
        # would otherwise hang a startup rather than cost a slow toggle.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_NONBLOCK
        handle = os.open(path, flags, 0o600)
        try:
            os.write(handle, full_name.encode("utf-8"))
        finally:
            os.close(handle)
    except OSError as exc:
        # Costs a slow toggle, nothing more. Not worth failing a startup for.
        log.debug("could not record the socket path in %s: %s", path, exc)


def _read_record(path: Path) -> str:
    """The recorded socket path, or "" if there is nothing trustworthy there.

    Refuses a symlink and a file belonging to anyone else: this path is
    predictable and may sit in a directory other people can write to, and the
    value is about to be used as a socket to connect to. O_NONBLOCK matters
    as much as the checks do — opening a FIFO for reading blocks until
    somebody writes to it, so a named pipe here would hang the key press
    instead of failing it. (A pipe or a directory then reads as empty or
    fails, so neither needs a check of its own.)
    """
    try:
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        info = os.fstat(handle)
        if info.st_uid != os.geteuid():
            log.debug("ignoring %s: it belongs to someone else", path)
            return ""
        return os.read(handle, 4096).decode("utf-8", "replace").strip()
    except OSError:
        return ""
    finally:
        os.close(handle)


def forget_socket(config_path: Path | None = None) -> None:
    """Remove the record on a clean exit. Never raises."""
    try:
        record_path(config_path).unlink(missing_ok=True)
    except OSError as exc:
        log.debug("could not remove the socket record: %s", exc)


def socket_paths(config_path: Path | None = None) -> list[Path]:
    """Every place the socket might be, best answer first.

    The recorded path is the authoritative one — the instance that is
    listening put it there. The rest reproduce where Qt places an abstract
    name on Unix, for an instance that predates the record.
    """
    name = server_name(config_path)
    paths: list[Path] = []
    recorded = _read_record(record_path(config_path))
    if recorded:
        paths.append(Path(recorded))
    for base in (os.environ.get("XDG_RUNTIME_DIR"), os.environ.get("TMPDIR"), "/tmp"):
        if not base:
            continue
        candidate = Path(base) / name
        if candidate not in paths:
            paths.append(candidate)
    return paths


def send(request: bytes, config_path: Path | None = None) -> bool:
    """Hand `request` to a running instance. True if one took it.

    False means no instance answered — not that sending failed. The caller's
    answer to both is the same: start the application properly.
    """
    for path in socket_paths(config_path):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(CONNECT_TIMEOUT_S)
                client.connect(str(path))
                client.sendall(request)
            return True
        except OSError:
            # Missing, stale, or nobody home: try the next place it could be.
            continue
    return False

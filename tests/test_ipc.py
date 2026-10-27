"""Reaching a running instance with the standard library alone.

This exists for one reason: a hotkey press should not pay for a PySide6
import. The tests that matter are the ones that would catch it being wrong in
a way the user feels — a request that silently goes nowhere, or a slow path
taken when the fast one would have worked — plus the handful that keep the
guessing honest when the socket is not where it was recorded.
"""

from __future__ import annotations

import os
import pathlib
import socket
import threading

import pytest

from nextdeck import ipc


@pytest.fixture(autouse=True)
def runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.delenv("TMPDIR", raising=False)
    return tmp_path


class Listener:
    """A plain AF_UNIX server, standing in for the Qt one."""

    def __init__(self, path):
        self.path = str(path)
        self.received: list[bytes] = []
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.path)
        self._server.listen(1)
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        try:
            conn, _ = self._server.accept()
        except OSError:
            return
        with conn:
            self.received.append(conn.recv(64))

    def close(self):
        self._server.close()
        self._thread.join(timeout=2)


@pytest.fixture
def listener(tmp_path):
    made = []

    def make(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        served = Listener(path)
        made.append(served)
        return served

    yield make
    for served in made:
        served.close()


# -- the name ------------------------------------------------------------


def test_the_name_is_per_config_and_per_user(tmp_path):
    assert ipc.server_name(tmp_path / "work.yaml") != ipc.server_name(
        tmp_path / "home.yaml"
    )
    assert str(os.getuid()) in ipc.server_name(None)


def test_the_name_does_not_depend_on_how_the_path_was_spelled(tmp_path):
    (tmp_path / "sub").mkdir()
    direct = tmp_path / "config.yaml"
    roundabout = tmp_path / "sub" / ".." / "config.yaml"
    assert ipc.server_name(direct) == ipc.server_name(roundabout)


# -- sending -------------------------------------------------------------


def test_nothing_listening_is_reported_rather_than_raised(tmp_path):
    assert ipc.send(ipc.TOGGLE_REQUEST, tmp_path / "config.yaml") is False


def test_a_request_reaches_a_listener_at_the_recorded_path(tmp_path, listener):
    config = tmp_path / "config.yaml"
    where = tmp_path / "elsewhere" / "socket"
    served = listener(where)
    ipc.remember_socket(str(where), config)

    assert ipc.send(ipc.TOGGLE_REQUEST, config) is True
    served.close()
    assert served.received == [ipc.TOGGLE_REQUEST]


def test_the_recorded_path_is_preferred_over_a_guess(tmp_path, listener):
    """The instance that is listening knows where it is; we only guess."""
    config = tmp_path / "config.yaml"
    recorded = listener(tmp_path / "recorded" / "socket")
    guessed = listener(ipc.record_path(config).parent / ipc.server_name(config))
    ipc.remember_socket(str(recorded.path), config)

    assert ipc.send(ipc.SHOW_REQUEST, config) is True
    recorded.close()
    guessed.close()
    assert recorded.received == [ipc.SHOW_REQUEST]
    assert guessed.received == [], "the guess should not have been needed"


def test_a_stale_record_falls_through_to_a_guess(tmp_path, listener):
    """A crash leaves a record pointing at a socket nobody is holding.

    The request still has to arrive: the guesses are what a user who has
    never had a clean exit depends on.
    """
    config = tmp_path / "config.yaml"
    ipc.remember_socket(str(tmp_path / "gone" / "socket"), config)
    guessed = listener(ipc.record_path(config).parent / ipc.server_name(config))

    assert ipc.send(ipc.SHOW_REQUEST, config) is True
    guessed.close()
    assert guessed.received == [ipc.SHOW_REQUEST]


def test_a_socket_file_left_behind_by_a_crash_is_not_mistaken_for_a_listener(
    tmp_path,
):
    config = tmp_path / "config.yaml"
    dead = ipc.record_path(config).parent / ipc.server_name(config)
    dead.parent.mkdir(parents=True, exist_ok=True)
    left = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    left.bind(str(dead))
    left.close()  # bound, never listening: exactly what a crash leaves

    assert ipc.send(ipc.SHOW_REQUEST, config) is False


def test_tmp_is_tried_when_there_is_no_runtime_directory(
    tmp_path, listener, monkeypatch
):
    config = tmp_path / "config.yaml"
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "temp"))
    served = listener(tmp_path / "temp" / ipc.server_name(config))

    assert ipc.send(ipc.SHOW_REQUEST, config) is True
    served.close()
    assert served.received == [ipc.SHOW_REQUEST]


# -- the record ----------------------------------------------------------


def test_the_record_is_written_and_removed(tmp_path):
    config = tmp_path / "config.yaml"
    ipc.remember_socket("/tmp/somewhere", config)
    assert ipc.record_path(config).read_text(encoding="utf-8") == "/tmp/somewhere"
    ipc.forget_socket(config)
    assert not ipc.record_path(config).exists()


def test_forgetting_what_was_never_recorded_is_fine(tmp_path):
    ipc.forget_socket(tmp_path / "config.yaml")


def test_an_empty_name_is_not_recorded(tmp_path):
    """fullServerName() can come back empty; a record of "" is worse than none."""
    config = tmp_path / "config.yaml"
    ipc.remember_socket("", config)
    assert not ipc.record_path(config).exists()


def test_the_record_is_private_to_its_owner(tmp_path):
    config = tmp_path / "config.yaml"
    ipc.remember_socket("/tmp/somewhere", config)
    assert ipc.record_path(config).stat().st_mode & 0o777 == 0o600


def test_a_record_that_is_a_symlink_is_refused(tmp_path):
    """The name is predictable and /tmp is shared; the value becomes a socket.

    Following a symlink here would let anyone who can write that directory
    choose where nextdeck connects and what it hands over.
    """
    config = tmp_path / "config.yaml"
    record = ipc.record_path(config)
    record.parent.mkdir(parents=True, exist_ok=True)
    planted = tmp_path / "planted"
    planted.write_text("/tmp/attacker-socket", encoding="utf-8")
    record.symlink_to(planted)

    paths = ipc.socket_paths(config)
    assert all("attacker" not in str(path) for path in paths)
    assert paths == [record.parent / ipc.server_name(config), _tmp_guess(config)]


def test_a_record_written_by_us_is_followed(tmp_path):
    """The other half of the test above: a real record is still used."""
    config = tmp_path / "config.yaml"
    ipc.remember_socket("/tmp/our-socket", config)
    assert ipc.socket_paths(config)[0] == pathlib.Path("/tmp/our-socket")


def test_a_symlinked_record_is_not_written_through_either(tmp_path):
    config = tmp_path / "config.yaml"
    record = ipc.record_path(config)
    record.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "someone-elses-file"
    target.write_text("untouched", encoding="utf-8")
    record.symlink_to(target)

    ipc.remember_socket("/tmp/our-socket", config)
    assert target.read_text(encoding="utf-8") == "untouched"


def _tmp_guess(config):
    return pathlib.Path("/tmp") / ipc.server_name(config)


def test_a_record_belonging_to_someone_else_is_refused(tmp_path, monkeypatch):
    """A name in a shared /tmp can be created by whoever gets there first.

    O_NOFOLLOW stops a symlink, but not a plain file planted by another user
    before nextdeck ever ran, whose contents would then choose where this
    connects. Faked by moving our own uid, since a test cannot own a file as
    somebody else.
    """
    config = tmp_path / "config.yaml"
    ipc.remember_socket("/tmp/not-ours", config)
    # Read before patching: ipc.os *is* the os module, so the replacement
    # would otherwise call itself.
    somebody_else = os.geteuid() + 1
    monkeypatch.setattr(ipc.os, "geteuid", lambda: somebody_else)

    paths = ipc.socket_paths(config)
    assert all("not-ours" not in str(path) for path in paths)


def test_a_record_that_is_a_named_pipe_is_refused_without_hanging(tmp_path):
    """Opening a FIFO waits for the other end, and a key press cannot wait.

    This test passing at all is half the point: a blocking open here would
    hang it rather than fail it.
    """
    config = tmp_path / "config.yaml"
    record = ipc.record_path(config)
    record.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(record)

    assert all(path != record for path in ipc.socket_paths(config))
    assert ipc.send(ipc.SHOW_REQUEST, config) is False


def test_a_named_pipe_does_not_hang_a_startup_either(tmp_path):
    config = tmp_path / "config.yaml"
    record = ipc.record_path(config)
    record.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(record)

    ipc.remember_socket("/tmp/our-socket", config)


def test_a_record_that_is_a_directory_is_refused(tmp_path):
    config = tmp_path / "config.yaml"
    record = ipc.record_path(config)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.mkdir()
    assert all(path != record for path in ipc.socket_paths(config))


def test_an_unwritable_runtime_directory_does_not_break_a_startup(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "nope" / "deeper"))
    monkeypatch.setattr(ipc.Path, "mkdir", _refuse)
    ipc.remember_socket("/tmp/somewhere", tmp_path / "config.yaml")


def _refuse(*args, **kwargs):
    raise OSError("read-only file system")

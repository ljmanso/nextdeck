"""One instance per user and config.

The app is tray-resident and now has a menu entry, so a second launch is easy
to trigger by accident. It has to wake the running copy, not join it.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

try:
    importlib.import_module("PySide6.QtNetwork")
except ImportError as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"PySide6.QtNetwork is unusable here: {exc}", allow_module_level=True)

from PySide6.QtWidgets import QApplication  # noqa: E402

from nextdeck.single_instance import SingleInstance, server_name  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def holder(qapp, tmp_path):
    """An instance that has claimed a name nothing else is using."""
    config = tmp_path / "config.yaml"
    instance = SingleInstance(config)
    assert instance.claim()
    yield instance, config
    instance.close()


def settle(qapp, times: int = 20) -> None:
    for _ in range(times):
        qapp.processEvents()


# -- naming ---------------------------------------------------------------


def test_different_configs_are_different_instances(tmp_path):
    """`--config work.yaml` and `--config home.yaml` must coexist."""
    assert server_name(tmp_path / "work.yaml") != server_name(tmp_path / "home.yaml")


def test_the_same_config_is_the_same_instance(tmp_path):
    one = tmp_path / "config.yaml"
    assert server_name(one) == server_name(Path(str(one)))


def test_the_name_is_per_user(tmp_path):
    assert str(os.getuid()) in server_name(tmp_path / "config.yaml")


def test_the_default_config_has_a_name_of_its_own(tmp_path):
    assert server_name(None) != server_name(tmp_path / "config.yaml")


# -- claiming and deferring -----------------------------------------------


def test_nothing_answers_before_anyone_claims(qapp, tmp_path):
    lonely = SingleInstance(tmp_path / "config.yaml")
    assert lonely.ping_existing() is False


def test_a_second_launch_finds_the_first(qapp, holder):
    _, config = holder
    newcomer = SingleInstance(config)
    assert newcomer.ping_existing() is True


def test_a_second_launch_makes_the_first_show_itself(qapp, holder):
    instance, config = holder
    shown: list[int] = []
    instance.show_requested.connect(lambda: shown.append(1))

    SingleInstance(config).ping_existing()
    settle(qapp)

    assert shown == [1], "the running window was never asked to appear"


def test_several_launches_each_raise_the_window(qapp, holder):
    instance, config = holder
    shown: list[int] = []
    instance.show_requested.connect(lambda: shown.append(1))

    for _ in range(3):
        SingleInstance(config).ping_existing()
        settle(qapp)

    assert shown == [1, 1, 1]


def test_an_instance_for_another_config_is_not_disturbed(qapp, holder, tmp_path):
    instance, _ = holder
    shown: list[int] = []
    instance.show_requested.connect(lambda: shown.append(1))

    other = SingleInstance(tmp_path / "other.yaml")
    assert other.ping_existing() is False
    settle(qapp)

    assert shown == [], "a different config must not wake this one"


def test_the_name_is_free_again_after_closing(qapp, tmp_path):
    config = tmp_path / "config.yaml"
    first = SingleInstance(config)
    assert first.claim()
    first.close()

    second = SingleInstance(config)
    assert second.ping_existing() is False, "the old socket should not answer"
    assert second.claim(), "and the name should be claimable again"
    second.close()


def test_a_stale_socket_does_not_block_a_fresh_claim(qapp, tmp_path):
    """A crash leaves the socket file behind; the next start must survive it.

    `close()` tidies the file up, so a kill -9 has to be staged by putting the
    leftover back: bind() refuses a path that already exists, which is exactly
    what strands a user whose previous run was killed.
    """
    config = tmp_path / "config.yaml"
    abandoned = SingleInstance(config)
    assert abandoned.claim()
    leftover = Path(abandoned._server.fullServerName())
    abandoned._server.close()
    abandoned._server = None
    leftover.touch()
    assert leftover.exists(), "the stale socket was not staged"

    revived = SingleInstance(config)
    assert revived.claim(), "a leftover socket must not keep nextdeck from starting"
    revived.close()


# -- the stdlib client against the real Qt server -------------------------
#
# The fast path exists because importing Qt to send seven bytes cost 734 ms.
# These are the tests that stop it being fast and wrong: everything else
# about it can be right while the bytes never arrive.


def test_the_stdlib_client_reaches_the_qt_server(holder, qapp):
    """A plain AF_UNIX socket must reach QLocalServer, Qt on one end only."""
    from nextdeck import ipc

    instance, config = holder
    seen: list[str] = []
    instance.toggle_requested.connect(lambda: seen.append("toggle"))
    instance.show_requested.connect(lambda: seen.append("show"))

    assert ipc.send(ipc.TOGGLE_REQUEST, config) is True
    _settle(qapp, lambda: seen)
    assert seen == ["toggle"]

    assert ipc.send(ipc.SHOW_REQUEST, config) is True
    _settle(qapp, lambda: len(seen) > 1)
    assert seen == ["toggle", "show"]


def test_claiming_records_where_the_socket_really_is(holder):
    """Qt chooses the location; the record is how the client finds it again.

    Without this the client is guessing, and a guess that is wrong on some
    distribution is a hotkey that silently takes the slow path forever.
    """
    from nextdeck import ipc

    instance, config = holder
    assert ipc.record_path(config).read_text(encoding="utf-8") == (
        instance._server.fullServerName()
    )


def test_a_clean_exit_takes_the_record_with_it(qapp, tmp_path):
    from nextdeck import ipc

    config = tmp_path / "config.yaml"
    instance = SingleInstance(config)
    assert instance.claim()
    assert ipc.record_path(config).exists()
    instance.close()
    assert not ipc.record_path(config).exists()
    assert ipc.send(ipc.SHOW_REQUEST, config) is False


def test_the_stdlib_client_ignores_an_instance_for_another_config(qapp, tmp_path):
    from nextdeck import ipc

    mine = tmp_path / "mine.yaml"
    theirs = tmp_path / "theirs.yaml"
    instance = SingleInstance(mine)
    assert instance.claim()
    try:
        assert ipc.send(ipc.SHOW_REQUEST, theirs) is False
    finally:
        instance.close()


def _settle(app, done):
    """Let Qt deliver the connection and the bytes behind it.

    Stops as soon as the request has arrived rather than always waiting out
    the deadline — the deadline is only there so a failure fails instead of
    hanging.
    """
    from PySide6.QtCore import QDeadlineTimer, QEventLoop

    deadline = QDeadlineTimer(2000)
    while not deadline.hasExpired() and not done():
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)

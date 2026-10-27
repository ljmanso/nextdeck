"""Showing and hiding the window from a key.

Two halves, because Qt cannot grab a key system-wide: an in-app shortcut that
only reaches nextdeck while nextdeck has the keyboard, and `nextdeck --toggle`,
which a desktop-level binding can call from anywhere.
"""

from __future__ import annotations

import importlib
import os
import pathlib
from argparse import Namespace

import pytest

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"PySide6 is unusable here: {exc}", allow_module_level=True)

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nextdeck import config as cfgmod  # noqa: E402
from nextdeck import hotkey  # noqa: E402
from nextdeck.config import BoardConfig, Config, ServerConfig, UIConfig  # noqa: E402
from nextdeck.single_instance import (  # noqa: E402
    SHOW_REQUEST,
    TOGGLE_REQUEST,
    SingleInstance,
)
from nextdeck.ui.shortcuts_dialog import groups  # noqa: E402
from nextdeck.ui.window import MainWindow  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


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

    def apply_config(self, config):
        pass

    def fetch_labels(self):
        pass

    def stop(self):
        pass


def make_window(qapp, tmp_path, shortcut="F10"):
    config = Config(
        server=ServerConfig(url="https://x.example", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2, title="Work"),
        ui=UIConfig(toggle_shortcut=shortcut),
    )
    win = MainWindow(
        config, StubController(), config_path=None, state_path=tmp_path / "s.yaml"
    )
    win.show()
    qapp.processEvents()
    return win


# -- the default ----------------------------------------------------------


def test_there_is_no_binding_until_the_user_picks_one(tmp_path):
    """Grabbing a key nobody asked for would be taking it from another app."""
    assert UIConfig().toggle_shortcut == ""
    path = tmp_path / "config.yaml"
    cfgmod.save(Config(), path)
    assert cfgmod.load(path).ui.toggle_shortcut == ""


def test_a_chosen_binding_survives_a_round_trip(tmp_path):
    path = tmp_path / "config.yaml"
    config = Config()
    config.ui.toggle_shortcut = "Ctrl+Shift+D"
    cfgmod.save(config, path)
    assert cfgmod.load(path).ui.toggle_shortcut == "Ctrl+Shift+D"


# -- the in-app shortcut --------------------------------------------------


def test_the_key_hides_a_visible_window(qapp, tmp_path):
    win = make_window(qapp, tmp_path)
    assert win.isVisible()

    QTest.keyClick(win, Qt.Key.Key_F10)
    qapp.processEvents()
    assert not win.isVisible()
    win.deleteLater()


def test_the_binding_follows_the_setting(qapp, tmp_path):
    win = make_window(qapp, tmp_path, shortcut="Ctrl+Shift+D")
    assert win._toggle_shortcut.key() == QKeySequence("Ctrl+Shift+D")

    QTest.keyClick(win, Qt.Key.Key_F10)
    qapp.processEvents()
    assert win.isVisible(), "F10 is not bound any more"

    QTest.keyClick(win, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier
                   | Qt.KeyboardModifier.ShiftModifier)
    qapp.processEvents()
    assert not win.isVisible()
    win.deleteLater()


def test_an_empty_setting_turns_the_key_off(qapp, tmp_path):
    win = make_window(qapp, tmp_path, shortcut="")
    assert not win._toggle_shortcut.isEnabled()

    QTest.keyClick(win, Qt.Key.Key_F10)
    qapp.processEvents()
    assert win.isVisible(), "nothing should be bound"
    win.deleteLater()


def test_nonsense_in_the_file_disables_it_rather_than_binding_something(
    qapp, tmp_path
):
    win = make_window(qapp, tmp_path, shortcut="not a key")
    assert not win._toggle_shortcut.isEnabled()
    win.deleteLater()


def test_rebinding_at_runtime_takes_effect(qapp, tmp_path):
    win = make_window(qapp, tmp_path)
    win.apply_toggle_shortcut("Ctrl+Alt+K")
    QTest.keyClick(win, Qt.Key.Key_F10)
    qapp.processEvents()
    assert win.isVisible(), "the old key should be gone"

    QTest.keyClick(win, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier
                   | Qt.KeyboardModifier.AltModifier)
    qapp.processEvents()
    assert not win.isVisible()
    win.deleteLater()


def test_the_key_works_from_inside_the_search_box(qapp, tmp_path):
    """ApplicationShortcut, so it is not swallowed by whatever has focus."""
    win = make_window(qapp, tmp_path)
    win.focus_search()
    qapp.processEvents()
    QTest.keyClick(win.search_edit, Qt.Key.Key_F10)
    qapp.processEvents()
    assert not win.isVisible()
    win.deleteLater()


# -- the cheat sheet ------------------------------------------------------


def test_the_cheat_sheet_names_the_configured_key():
    rows = dict(row for _, group in groups("Ctrl+Shift+D") for row in group)
    assert "Ctrl + Shift + D" in rows, "spaced like every other row"


def test_the_cheat_sheet_leaves_it_out_when_switched_off():
    listed = [row for _, group in groups("") for row in group]
    assert all("Show or hide the window" != d for _, d in listed)


def test_the_cheat_sheet_covers_every_shortcut_the_readme_lists():
    """The two drifted apart once already: Ctrl+F was only in the README."""
    import re

    readme = (pathlib.Path(__file__).resolve().parents[1] / "README.md").read_text()
    section = readme.split("## Keyboard", 1)[1].split("### Showing", 1)[0]
    documented = {
        key.strip().strip("`").replace(" ", "")
        for row in re.findall(r"^\| (`[^|]+) \|", section, re.M)
        for key in row.split("`  `")
    }
    # "F10" so the configurable row is present under its default.
    listed = {
        key.replace(" ", "") for _, group in groups("F10") for key, _ in group
    }
    # Only the plain single-key rows are comparable; the arrow rows are
    # written differently on purpose.
    plain = {k for k in documented if k.startswith(("Ctrl", "F")) and "←" not in k}
    assert plain <= listed, f"in the README but not the cheat sheet: {plain - listed}"


def test_rebinding_drops_the_cached_cheat_sheet(qapp, tmp_path):
    win = make_window(qapp, tmp_path)
    win.show_shortcuts()
    qapp.processEvents()
    assert win._shortcuts_dialog is not None

    win.apply_toggle_shortcut("Ctrl+Alt+K")
    assert win._shortcuts_dialog is None, "it would still name the old key"
    win.deleteLater()


# -- the out-of-process half ---------------------------------------------


def test_a_toggle_request_is_told_apart_from_a_show_request(qapp, tmp_path):
    holder = SingleInstance(tmp_path / "config.yaml")
    assert holder.claim()
    seen: list[str] = []
    holder.show_requested.connect(lambda: seen.append("show"))
    holder.toggle_requested.connect(lambda: seen.append("toggle"))

    newcomer = SingleInstance(tmp_path / "config.yaml")
    assert newcomer.ping_existing(TOGGLE_REQUEST)
    for _ in range(10):
        qapp.processEvents()
    assert seen == ["toggle"]

    assert newcomer.ping_existing(SHOW_REQUEST)
    for _ in range(10):
        qapp.processEvents()
    assert seen == ["toggle", "show"]
    holder.close()


def test_a_plain_ping_still_means_show(qapp, tmp_path):
    """The default keeps every existing caller working."""
    holder = SingleInstance(tmp_path / "config.yaml")
    assert holder.claim()
    seen: list[str] = []
    holder.show_requested.connect(lambda: seen.append("show"))
    holder.toggle_requested.connect(lambda: seen.append("toggle"))

    assert SingleInstance(tmp_path / "config.yaml").ping_existing()
    for _ in range(10):
        qapp.processEvents()
    assert seen == ["show"]
    holder.close()


def test_an_unknown_request_is_ignored(qapp, tmp_path):
    """A newer nextdeck must not make an older one pop up a window."""
    holder = SingleInstance(tmp_path / "config.yaml")
    assert holder.claim()
    seen: list[str] = []
    holder.show_requested.connect(lambda: seen.append("show"))
    holder.toggle_requested.connect(lambda: seen.append("toggle"))

    assert SingleInstance(tmp_path / "config.yaml").ping_existing(b"flibble\n")
    for _ in range(10):
        qapp.processEvents()
    assert seen == []
    holder.close()


# -- the settings dialog --------------------------------------------------


def test_settings_round_trips_the_binding(qapp):
    from nextdeck.ui.settings_dialog import SettingsDialog

    config = Config(
        server=ServerConfig(url="https://x", username="u", app_password="p"),
        board=BoardConfig(id=3, default_stack_id=7),
    )
    config.ui.toggle_shortcut = "F10"
    dialog = SettingsDialog(config, StubController())
    assert dialog.toggle_shortcut_edit.keySequence() == QKeySequence("F10")

    dialog.toggle_shortcut_edit.setKeySequence(QKeySequence("Ctrl+Shift+D"))
    dialog._on_save()
    assert dialog.result_config().ui.toggle_shortcut == "Ctrl+Shift+D"
    dialog.deleteLater()


def test_clearing_the_box_switches_the_key_off(qapp):
    from nextdeck.ui.settings_dialog import SettingsDialog

    config = Config(
        server=ServerConfig(url="https://x", username="u", app_password="p"),
        board=BoardConfig(id=3, default_stack_id=7),
    )
    dialog = SettingsDialog(config, StubController())
    dialog.toggle_shortcut_edit.clear()
    dialog._on_save()
    assert dialog.result_config().ui.toggle_shortcut == ""
    dialog.deleteLater()


def test_the_settings_note_says_the_key_cannot_summon_the_window(qapp):
    from nextdeck.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(Config(), StubController())
    note = dialog._shortcut_note.text()
    assert "cannot bring it back" in note
    assert "keyboard settings" in note
    dialog.deleteLater()


def test_the_settings_dialog_offers_an_absolute_toggle_command(qapp):
    """A desktop keybinding runs with a minimal PATH, so a bare name fails."""
    from nextdeck.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(Config(), StubController())
    command = dialog.toggle_command_edit.text()
    assert command.startswith("/"), command
    assert command.endswith(" --toggle"), command
    assert dialog.toggle_command_edit.isReadOnly()
    dialog.deleteLater()


def test_the_toggle_command_carries_the_config_file(qapp, tmp_path):
    """The socket is keyed by config path, so --config has to come along."""
    from nextdeck.ui.settings_dialog import SettingsDialog

    path = tmp_path / "work.yaml"
    dialog = SettingsDialog(Config(), StubController(), config_path=path)
    assert f"--config {path}" in dialog.toggle_command_edit.text()
    dialog.deleteLater()


def test_copying_the_command_puts_it_on_the_clipboard(qapp):
    from nextdeck.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(Config(), StubController())
    QApplication.clipboard().clear()
    dialog._copy_toggle_command()
    assert QApplication.clipboard().text() == dialog.toggle_command_edit.text()
    dialog.deleteLater()


# -- registering with the desktop, on confirmation ------------------------


class FakeDesktop(hotkey.Hotkey):
    """Stands in for GNOME or KDE in the settings-save path.

    Subclasses the real base class on purpose: a hand-rolled stand-in fell
    behind the moment the base grew a method, and a double that is missing a
    method the caller uses fails as a crash in an unrelated test.
    """

    def __init__(self, name="GNOME", installed="", succeeds=True, live=True):
        self.name = name
        self._installed = installed
        self._succeeds = succeeds
        self._live = live
        self.installs: list[tuple[str, str]] = []
        self.removals = 0

    def install(self, sequence, command):
        self.installs.append((sequence, command))
        if self._succeeds:
            self._installed = sequence
        return self._succeeds

    def remove(self):
        self.removals += 1
        self._installed = ""
        return True

    def installed_sequence(self):
        return self._installed

    def went_live(self):
        return self._live

    def session_has_it(self):
        return bool(self._installed) if self._live else False


@pytest.fixture
def hotkey_flow(qapp, tmp_path, monkeypatch):
    """A window, a fake desktop, and a recorded answer to the question."""
    from nextdeck import hotkey as hotkeymod
    from PySide6.QtWidgets import QMessageBox

    win = make_window(qapp, tmp_path, shortcut="")
    desk = FakeDesktop()
    monkeypatch.setattr(hotkeymod, "backend", lambda *a, **k: desk)
    asked: list[str] = []

    def answer(yes):
        def question(parent, title, text, *args, **kwargs):
            asked.append(text)
            return (
                QMessageBox.StandardButton.Yes
                if yes
                else QMessageBox.StandardButton.No
            )

        monkeypatch.setattr(QMessageBox, "question", staticmethod(question))

    warned: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda parent, title, text, *a, **k: warned.append(text)),
    )
    # Patched even where no test expects one: a real modal here would hang the
    # suite rather than fail it, which has cost an afternoon once already.
    desk.informed = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda parent, title, text, *a, **k: desk.informed.append(text)),
    )
    yield win, desk, asked, warned, answer
    win.deleteLater()


def test_choosing_a_key_asks_before_touching_the_desktop(hotkey_flow):
    win, desk, asked, _warned, answer = hotkey_flow
    answer(True)

    win.sync_desktop_hotkey("", "F10")
    assert len(asked) == 1, "it must ask, not just do it"
    assert "GNOME" in asked[0]
    assert desk.installs and desk.installs[0][0] == "F10"
    assert desk.installs[0][1].endswith("--toggle")


def test_declining_leaves_the_desktop_alone(hotkey_flow):
    win, desk, asked, _warned, answer = hotkey_flow
    answer(False)

    win.sync_desktop_hotkey("", "F10")
    assert asked, "it still asks"
    assert desk.installs == [], "but writes nothing"
    assert "only while nextdeck is in front" in win.status_label.text()


def test_a_failed_registration_tells_the_user_the_command(hotkey_flow):
    win, desk, _asked, warned, answer = hotkey_flow
    desk._succeeds = False
    answer(True)

    win.sync_desktop_hotkey("", "F10")
    assert warned, "a silent failure would leave them thinking it worked"
    assert "--toggle" in warned[0], "the fallback command has to be in the message"


def test_clearing_the_key_removes_the_binding_without_asking(hotkey_flow):
    win, desk, asked, _warned, answer = hotkey_flow
    answer(True)
    win.sync_desktop_hotkey("", "F10")
    asked.clear()

    win.sync_desktop_hotkey("F10", "")
    assert desk.removals == 1
    assert asked == [], "undoing our own change needs no confirmation"
    assert "Removed" in win.status_label.text()


def test_clearing_a_key_that_was_never_registered_does_nothing(hotkey_flow):
    win, desk, _asked, _warned, answer = hotkey_flow
    win.sync_desktop_hotkey("F10", "")
    assert desk.removals == 0


def test_an_unsupported_desktop_is_not_asked_about(qapp, tmp_path, monkeypatch):
    """No GNOME or KDE to offer, so there is nothing to confirm."""
    from nextdeck import hotkey as hotkeymod
    from PySide6.QtWidgets import QMessageBox

    win = make_window(qapp, tmp_path, shortcut="")
    monkeypatch.setattr(hotkeymod, "backend", lambda *a, **k: hotkeymod.Hotkey())

    # Both are patched: an unpatched one opens a real modal dialog, which
    # hangs the run instead of failing and hides what actually went wrong.
    seen = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: seen.append("question")
                     or QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: seen.append("warning"))
    )
    win.sync_desktop_hotkey("", "F10")
    assert seen == []
    win.deleteLater()


def test_the_question_names_the_exact_command(hotkey_flow):
    win, _desk, asked, _warned, answer = hotkey_flow
    answer(True)
    win.sync_desktop_hotkey("", "Ctrl+Shift+D")
    assert "--toggle" in asked[0]
    assert "Ctrl+Shift+D" in asked[0]


# -- the cheat sheet's note ----------------------------------------------


def test_the_cheat_sheet_explains_the_other_desktops():
    from nextdeck.ui.shortcuts_dialog import toggle_note

    note = toggle_note("F10")
    assert "GNOME" in note and "KDE" in note
    assert "--toggle" in note, "other desktops need the command named"


def test_the_cheat_sheet_note_says_when_no_key_is_set():
    from nextdeck.ui.shortcuts_dialog import toggle_note

    assert "No show/hide key is set" in toggle_note("")


def test_saving_settings_is_what_triggers_the_sync(qapp, tmp_path, monkeypatch):
    """The sync is wired to the save, not only callable on its own."""
    from nextdeck.ui import window as windowmod

    win = make_window(qapp, tmp_path, shortcut="")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        type(win), "sync_desktop_hotkey",
        lambda self, before, after: calls.append((before, after)),
    )

    changed = Config(
        server=win.config.server, board=win.config.board, ui=win.config.ui
    )
    changed.ui = type(win.config.ui)(**{**vars(win.config.ui), "toggle_shortcut": "F10"})

    class FakeDialog:
        DialogCode = windowmod.SettingsDialog.DialogCode

        def __init__(self, *a, **k):
            pass

        def exec(self):
            return self.DialogCode.Accepted

        def result_config(self):
            return changed

    monkeypatch.setattr(windowmod, "SettingsDialog", FakeDialog)
    win.open_settings()
    assert calls == [("", "F10")]
    win.deleteLater()


def test_settings_always_reconciles_even_when_the_key_is_unchanged(
    qapp, tmp_path, monkeypatch
):
    """A key can be in config.yaml and unregistered, which is not a change.

    Deciding whether to act belongs inside sync_desktop_hotkey, which checks
    what the desktop actually has; gating the call on the value changing is
    what let a configured key stay unregistered forever.
    """
    from nextdeck.ui import window as windowmod

    win = make_window(qapp, tmp_path, shortcut="F10")
    calls = []
    monkeypatch.setattr(
        type(win), "sync_desktop_hotkey",
        lambda self, before, after: calls.append((before, after)),
    )
    same = win.config

    class FakeDialog:
        DialogCode = windowmod.SettingsDialog.DialogCode

        def __init__(self, *a, **k):
            pass

        def exec(self):
            return self.DialogCode.Accepted

        def result_config(self):
            return same

    monkeypatch.setattr(windowmod, "SettingsDialog", FakeDialog)
    win.open_settings()
    assert calls == [("F10", "F10")]
    win.deleteLater()


def test_the_cheat_sheet_dialog_actually_shows_the_note(qapp):
    """The note is the only place a Hyprland or sway user is told what to do."""
    from nextdeck.ui.shortcuts_dialog import ShortcutsDialog, toggle_note

    dialog = ShortcutsDialog(None, "F10")
    assert dialog.note.text() == toggle_note("F10")
    assert dialog.note.isVisibleTo(dialog)
    dialog.deleteLater()


def test_the_window_passes_its_key_to_the_cheat_sheet(qapp, tmp_path):
    win = make_window(qapp, tmp_path, shortcut="Ctrl+Alt+K")
    win.show_shortcuts()
    qapp.processEvents()
    assert "Ctrl+Alt+K" in win._shortcuts_dialog.note.text()
    win.deleteLater()


# -- a key that was never registered --------------------------------------
#
# The state a real user landed in: a key sitting in config.yaml that the
# desktop knows nothing about, because it was set before nextdeck could
# register one. Watching only for the key to *change* skipped it forever.


def test_a_configured_but_unregistered_key_is_offered(hotkey_flow):
    win, desk, asked, _warned, answer = hotkey_flow
    answer(True)

    # Same key before and after: nothing changed, but nothing is registered.
    win.sync_desktop_hotkey("F9", "F9")
    assert asked, "an unregistered key has to be offered, changed or not"
    assert desk.installs == [("F9", desk.installs[0][1])]


def test_an_already_registered_key_is_not_offered_again(hotkey_flow):
    win, desk, asked, _warned, answer = hotkey_flow
    answer(True)
    desk._installed = "F9"

    win.sync_desktop_hotkey("F9", "F9")
    assert asked == [], "it is already set up; asking again would be noise"
    assert desk.installs == []


def test_a_key_registered_as_something_else_is_offered(hotkey_flow):
    """Edited by hand in config.yaml, or changed on another machine."""
    win, desk, asked, _warned, answer = hotkey_flow
    answer(True)
    desk._installed = "F10"

    win.sync_desktop_hotkey("F9", "F9")
    assert asked, "the desktop and the config disagree"
    assert desk.installs[0][0] == "F9"


def test_saying_no_is_remembered_and_not_re_asked(hotkey_flow):
    win, desk, asked, _warned, answer = hotkey_flow
    answer(False)

    win.sync_desktop_hotkey("", "F9")
    assert len(asked) == 1
    asked.clear()

    # Saving settings again must not nag about the same key.
    win.sync_desktop_hotkey("F9", "F9")
    assert asked == [], "they already said no to this one"


def test_saying_no_does_not_silence_a_different_key(hotkey_flow):
    win, desk, asked, _warned, answer = hotkey_flow
    answer(False)
    win.sync_desktop_hotkey("", "F9")
    asked.clear()

    answer(True)
    win.sync_desktop_hotkey("F9", "Ctrl+Alt+K")
    assert asked, "a different key is a different question"
    assert desk.installs[0][0] == "Ctrl+Alt+K"


def test_registering_clears_an_earlier_refusal(hotkey_flow):
    """Otherwise an old "no" would keep suppressing a key they since chose."""
    from nextdeck import state as statemod

    win, desk, asked, _warned, answer = hotkey_flow
    answer(False)
    win.sync_desktop_hotkey("", "F9")
    assert statemod.load(win.state_path).declined_hotkey == "F9"

    # Picking a different key is a fresh question, and accepting it should
    # leave nothing behind to haunt the next save.
    answer(True)
    win.sync_desktop_hotkey("F9", "Ctrl+Alt+K")
    assert desk.installs[-1][0] == "Ctrl+Alt+K"
    assert statemod.load(win.state_path).declined_hotkey == ""


def test_a_declined_key_is_still_registrable_from_the_command_line(
    hotkey_flow, monkeypatch, capsys
):
    """--register-hotkey is the way back after saying no."""
    from nextdeck.__main__ import _register_hotkey
    from nextdeck import hotkey as hotkeymod
    from nextdeck import state as statemod

    win, desk, _asked, _warned, answer = hotkey_flow
    answer(False)
    win.sync_desktop_hotkey("", "F9")
    assert statemod.load(win.state_path).declined_hotkey == "F9"

    monkeypatch.setattr(hotkeymod, "backend", lambda *a, **k: desk)
    config = Config()
    config.ui.toggle_shortcut = "F9"
    assert _register_hotkey(Namespace(config=None), config) == 0
    assert desk.installs[-1][0] == "F9"
    assert "Registered F9" in capsys.readouterr().out


def test_clearing_the_key_removes_a_binding_set_on_another_run(hotkey_flow):
    """`before` is not a reliable witness; what is registered is."""
    win, desk, _asked, _warned, answer = hotkey_flow
    desk._installed = "F9"
    win.sync_desktop_hotkey("", "")
    assert desk.removals == 1


# -- registered but not yet live ------------------------------------------


def test_a_binding_that_needs_a_relogin_says_so(hotkey_flow):
    """KDE Plasma 6: the file is right and the key is dead until a re-login.

    Everything a user can check says "registered", so the only way they find
    out is by being told.
    """
    win, desk, _asked, _warned, answer = hotkey_flow
    desk.name = "KDE Plasma"
    desk._live = False
    answer(True)

    win.sync_desktop_hotkey("", "F9")

    assert desk.installs, "it still registers it — the file is what persists"
    assert desk.informed, "and it says the key will not work yet"
    assert "log out" in desk.informed[0].lower()
    assert "F9" in desk.informed[0]


def test_a_binding_that_is_live_says_nothing_extra(hotkey_flow):
    win, desk, _asked, _warned, answer = hotkey_flow
    answer(True)
    win.sync_desktop_hotkey("", "F9")
    assert desk.installs
    assert desk.informed == [], "nothing to warn about; do not nag"


def test_a_failed_registration_does_not_also_claim_a_relogin_helps(hotkey_flow):
    win, desk, _asked, warned, answer = hotkey_flow
    desk._succeeds = False
    desk._live = False
    answer(True)
    win.sync_desktop_hotkey("", "F9")
    assert warned, "a failure is a warning"
    assert desk.informed == []


def test_register_hotkey_warns_when_the_session_cannot_load_it(
    hotkey_flow, monkeypatch, capsys
):
    from nextdeck.__main__ import _register_hotkey

    _win, desk, _asked, _warned, _answer = hotkey_flow
    desk.name = "KDE Plasma"
    desk._live = False
    monkeypatch.setattr(hotkey, "backend", lambda *a, **k: desk)
    config = Config()
    config.ui.toggle_shortcut = "F9"

    assert _register_hotkey(Namespace(config=None), config) == 0
    out = capsys.readouterr().out
    assert "Registered F9" in out
    assert "log out and back in" in out


def test_register_hotkey_is_quiet_when_the_key_is_live(
    hotkey_flow, monkeypatch, capsys
):
    from nextdeck.__main__ import _register_hotkey

    _win, desk, _asked, _warned, _answer = hotkey_flow
    monkeypatch.setattr(hotkey, "backend", lambda *a, **k: desk)
    config = Config()
    config.ui.toggle_shortcut = "F9"

    assert _register_hotkey(Namespace(config=None), config) == 0
    out = capsys.readouterr().out
    assert "log out" not in out.lower()


def test_check_hotkey_points_at_a_relogin_when_the_session_lacks_it(
    hotkey_flow, monkeypatch, capsys
):
    """The verdict has to name the one fix, not list every possibility."""
    from nextdeck.__main__ import _report_hotkey

    _win, desk, _asked, _warned, _answer = hotkey_flow
    desk.name = "KDE Plasma"
    desk._installed = "F9"
    desk._live = False
    monkeypatch.setattr(hotkey, "backend", lambda *a, **k: desk)
    monkeypatch.setattr(hotkey, "diagnose", lambda path=None: [])
    config = Config()
    config.ui.toggle_shortcut = "F9"

    assert _report_hotkey(Namespace(config=None), config) == 0
    out = capsys.readouterr().out
    assert "Log out and back in" in out
    assert "not loaded it" in out


def test_check_hotkey_blames_a_conflict_when_the_session_has_it(
    hotkey_flow, monkeypatch, capsys
):
    from nextdeck.__main__ import _report_hotkey

    _win, desk, _asked, _warned, _answer = hotkey_flow
    desk.name = "KDE Plasma"
    desk._installed = "F9"
    monkeypatch.setattr(hotkey, "backend", lambda *a, **k: desk)
    monkeypatch.setattr(hotkey, "diagnose", lambda path=None: [])
    config = Config()
    config.ui.toggle_shortcut = "F9"

    assert _report_hotkey(Namespace(config=None), config) == 0
    out = capsys.readouterr().out
    assert "something else is taking it first" in out
    assert "Log out" not in out


# -- the hotkey path does not wait for Qt ---------------------------------


def test_a_toggle_is_sent_before_anything_heavy_is_touched(monkeypatch, capsys):
    """The whole point of the fast path: measured at 734 ms before it existed.

    Proved by making the next step after it blow up. If `main` reads the
    config — let alone imports the UI — the request has already cost more
    than the key press is worth.
    """
    from nextdeck import __main__ as mainmod
    from nextdeck import ipc

    sent: list[tuple[bytes, object]] = []
    monkeypatch.setattr(
        ipc, "send", lambda request, path=None: sent.append((request, path)) or True
    )
    monkeypatch.setattr(
        cfgmod, "load", lambda *a, **k: pytest.fail("loaded the config anyway")
    )

    assert mainmod.main(["--toggle"]) == 0
    assert sent == [(ipc.TOGGLE_REQUEST, None)]


def test_a_plain_launch_takes_the_same_shortcut(monkeypatch):
    """Opening from the menu or a dock is the same hand-off, so it is as fast."""
    from nextdeck import __main__ as mainmod
    from nextdeck import ipc

    sent: list[bytes] = []
    monkeypatch.setattr(
        ipc, "send", lambda request, path=None: sent.append(request) or True
    )
    monkeypatch.setattr(
        cfgmod, "load", lambda *a, **k: pytest.fail("loaded the config anyway")
    )

    assert mainmod.main([]) == 0
    assert sent == [ipc.SHOW_REQUEST]


def test_the_config_path_is_carried_so_the_right_instance_is_woken(monkeypatch):
    from nextdeck import __main__ as mainmod
    from nextdeck import ipc

    sent: list[object] = []
    monkeypatch.setattr(
        ipc, "send", lambda request, path=None: sent.append(path) or True
    )
    monkeypatch.setattr(cfgmod, "load", lambda *a, **k: pytest.fail("too far"))

    assert mainmod.main(["--toggle", "--config", "/tmp/work.yaml"]) == 0
    assert sent == [pathlib.Path("/tmp/work.yaml")]


def test_replace_does_not_hand_over_to_the_running_instance(monkeypatch):
    """--replace means "I am the instance now", so there is nobody to ask."""
    from nextdeck import __main__ as mainmod
    from nextdeck import ipc

    monkeypatch.setattr(
        ipc, "send", lambda *a, **k: pytest.fail("asked the old instance anyway")
    )
    stopped = RuntimeError("far enough")

    def no_further(*args, **kwargs):
        raise stopped

    monkeypatch.setattr(cfgmod, "load", no_further)
    with pytest.raises(RuntimeError):
        mainmod.main(["--replace"])


def test_the_hotkey_commands_do_not_hand_over_either(monkeypatch):
    """--check-hotkey reports on *this* machine's setup and must not be a toggle.

    It sends its own probe later, deliberately and visibly; a silent one here
    would toggle the window before the report had said anything.
    """
    from nextdeck import __main__ as mainmod
    from nextdeck import ipc

    monkeypatch.setattr(ipc, "send", lambda *a, **k: pytest.fail("sent a request"))
    monkeypatch.setattr(mainmod, "_report_hotkey", lambda args, config: 7)
    monkeypatch.setattr(mainmod, "_register_hotkey", lambda args, config: 9)

    assert mainmod.main(["--check-hotkey"]) == 7
    assert mainmod.main(["--register-hotkey"]) == 9

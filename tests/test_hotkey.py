"""Registering the show/hide key with GNOME or KDE.

Neither desktop is installed in the environment this was written in, so the
GNOME half runs against a fake `gsettings` that answers in the real tool's
output formats, and the KDE half — which is only file manipulation — runs
against real files. What cannot be checked here is whether a live session
picks the binding up; see the README.
"""

from __future__ import annotations

import subprocess

import pytest

from nextdeck import hotkey
from nextdeck.hotkey import (
    GNOME_CUSTOM_SCHEMA,
    GNOME_MEDIA_KEYS,
    GNOME_SLOT_PREFIX,
    KDE_DESKTOP_FILENAME,
    GnomeHotkey,
    Hotkey,
    KdeHotkey,
    backend,
    to_gnome_accelerator,
    to_kde_shortcut,
)


# -- translating a Qt key sequence ----------------------------------------


@pytest.mark.parametrize(
    "sequence, expected",
    [
        ("F10", "F10"),
        ("f10", "F10"),
        ("Ctrl+Shift+D", "<Control><Shift>d"),
        ("Meta+X", "<Super>x"),
        ("Ctrl+Alt+K", "<Control><Alt>k"),
        ("Ctrl+Space", "<Control>space"),
        ("Esc", "Escape"),
        ("Ctrl+Up", "<Control>Up"),
        ("", ""),
    ],
)
def test_gnome_accelerators(sequence, expected):
    assert to_gnome_accelerator(sequence) == expected


def test_a_letter_is_lowercased_for_gnome():
    """GTK looks keys up by name, and "D" is a different keyval from "d" —
    binding the capital would quietly demand Shift as well."""
    assert to_gnome_accelerator("Ctrl+D") == "<Control>d"


@pytest.mark.parametrize("sequence", ["F10", "Ctrl+Shift+D", "Meta+X"])
def test_kde_spells_shortcuts_the_way_qt_does(sequence):
    assert to_kde_shortcut(sequence) == sequence


# -- picking a backend ----------------------------------------------------


@pytest.mark.parametrize(
    "desktop, expected",
    [
        ("KDE", KdeHotkey),
        ("plasma", KdeHotkey),
        ("GNOME", GnomeHotkey),
        ("ubuntu:GNOME", GnomeHotkey),
        ("X-Cinnamon", GnomeHotkey),
        ("Hyprland", Hotkey),
        ("sway", Hotkey),
        ("", Hotkey),
    ],
)
def test_backend_for_a_desktop(desktop, expected):
    assert type(backend(desktop)) is expected


def test_the_desktop_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert type(backend()) is KdeHotkey
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    assert type(backend()) is GnomeHotkey


def test_an_unsupported_desktop_does_nothing_rather_than_failing(tmp_path):
    """It has to be safe to call: the save path runs this unconditionally."""
    plain = Hotkey()
    assert plain.name == ""
    assert plain.install("F10", "/usr/bin/nextdeck --toggle") is False
    assert plain.remove() is False
    assert plain.installed_sequence() == ""


# -- GNOME, against a fake gsettings --------------------------------------


class FakeGsettings:
    """Enough of gsettings to be wrong in the same places the real one is."""

    def __init__(self, slots=None):
        self.store: dict[tuple[str, str], str] = {}
        self.calls: list[list[str]] = []
        self.fail_on: set[str] = set()
        self.store[(GNOME_MEDIA_KEYS, "custom-keybindings")] = str(slots or [])

    def __call__(self, args):
        self.calls.append(args)
        assert args[0] == "gsettings"
        verb = args[1]
        if verb == "get":
            schema, key = args[2], args[3]
            raw = self.store.get((schema, key))
            if raw is None:
                return subprocess.CompletedProcess(args, 1, "", "No such key")
            if schema == GNOME_MEDIA_KEYS:
                # The real tool prints an empty array with its type attached.
                out = "@as []" if raw == "[]" else raw
            else:
                out = f"'{raw}'"
            return subprocess.CompletedProcess(args, 0, out + "\n", "")
        if verb == "set":
            schema, key, value = args[2], args[3], args[4]
            if key in self.fail_on:
                return subprocess.CompletedProcess(args, 1, "", f"cannot set {key}")
            self.store[(schema, key)] = value
            return subprocess.CompletedProcess(args, 0, "", "")
        if verb == "reset-recursively":
            schema = args[2]
            for entry in [k for k in self.store if k[0] == schema]:
                del self.store[entry]
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected gsettings verb: {verb}")

    def slots(self):
        import ast

        return ast.literal_eval(self.store[(GNOME_MEDIA_KEYS, "custom-keybindings")])

    def field(self, slot, key):
        return self.store.get((f"{GNOME_CUSTOM_SCHEMA}:{slot}", key))


@pytest.fixture
def gnome(monkeypatch):
    fake = FakeGsettings()
    monkeypatch.setattr(hotkey, "_run", fake)
    monkeypatch.setattr(hotkey.shutil, "which", lambda name: f"/usr/bin/{name}")
    return fake


COMMAND = "/usr/bin/nextdeck --toggle"


def test_gnome_install_fills_in_a_slot_and_lists_it(gnome):
    assert GnomeHotkey().install("Ctrl+Shift+D", COMMAND)

    slot = f"{GNOME_SLOT_PREFIX}custom0/"
    assert gnome.slots() == [slot]
    assert gnome.field(slot, "name") == "nextdeck"
    assert gnome.field(slot, "command") == COMMAND
    assert gnome.field(slot, "binding") == "<Control><Shift>d"


def test_gnome_lists_the_slot_only_after_filling_it_in(gnome):
    """A listed but empty slot shows up in GNOME's panel as a broken row."""
    GnomeHotkey().install("F10", COMMAND)
    verbs = [
        (call[2], call[3])
        for call in gnome.calls
        if call[1] == "set"
    ]
    listed = verbs.index((GNOME_MEDIA_KEYS, "custom-keybindings"))
    binding = verbs.index((f"{GNOME_CUSTOM_SCHEMA}:{GNOME_SLOT_PREFIX}custom0/", "binding"))
    assert binding < listed


def test_gnome_keeps_shortcuts_the_user_already_had(monkeypatch):
    theirs = f"{GNOME_SLOT_PREFIX}custom0/"
    fake = FakeGsettings(slots=[theirs])
    fake.store[(f"{GNOME_CUSTOM_SCHEMA}:{theirs}", "name")] = "Terminal"
    monkeypatch.setattr(hotkey, "_run", fake)
    monkeypatch.setattr(hotkey.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert GnomeHotkey().install("F10", COMMAND)
    ours = f"{GNOME_SLOT_PREFIX}custom1/"
    assert fake.slots() == [theirs, ours], "their shortcut must survive"
    assert fake.field(theirs, "name") == "Terminal"


def test_gnome_rebinding_reuses_its_own_slot(gnome):
    GnomeHotkey().install("F10", COMMAND)
    assert GnomeHotkey().install("Ctrl+Alt+K", COMMAND)

    assert len(gnome.slots()) == 1, "a second slot would be a duplicate shortcut"
    slot = gnome.slots()[0]
    assert gnome.field(slot, "binding") == "<Control><Alt>k"


def test_gnome_reports_the_binding_it_installed(gnome):
    assert GnomeHotkey().installed_sequence() == ""
    GnomeHotkey().install("F10", COMMAND)
    assert GnomeHotkey().installed_sequence() == "F10"


def test_gnome_remove_delists_and_clears(gnome):
    GnomeHotkey().install("F10", COMMAND)
    assert GnomeHotkey().remove()
    assert gnome.slots() == []
    assert gnome.field(f"{GNOME_SLOT_PREFIX}custom0/", "binding") is None


def test_gnome_remove_leaves_other_shortcuts_listed(monkeypatch):
    theirs = f"{GNOME_SLOT_PREFIX}custom0/"
    fake = FakeGsettings(slots=[theirs])
    fake.store[(f"{GNOME_CUSTOM_SCHEMA}:{theirs}", "name")] = "Terminal"
    monkeypatch.setattr(hotkey, "_run", fake)
    monkeypatch.setattr(hotkey.shutil, "which", lambda name: f"/usr/bin/{name}")

    GnomeHotkey().install("F10", COMMAND)
    GnomeHotkey().remove()
    assert fake.slots() == [theirs]


def test_gnome_remove_is_fine_when_nothing_is_installed(gnome):
    assert GnomeHotkey().remove()


def test_gnome_reports_failure_rather_than_pretending(gnome):
    gnome.fail_on.add("binding")
    assert GnomeHotkey().install("F10", COMMAND) is False


def test_gnome_without_gsettings_installed(monkeypatch):
    monkeypatch.setattr(hotkey.shutil, "which", lambda name: None)
    assert GnomeHotkey().install("F10", COMMAND) is False
    assert GnomeHotkey().installed_sequence() == ""


def test_gnome_survives_gsettings_blowing_up(monkeypatch):
    def explode(args):
        raise OSError("boom")

    monkeypatch.setattr(hotkey.shutil, "which", lambda name: "/usr/bin/gsettings")
    monkeypatch.setattr(hotkey, "_run", explode)
    assert GnomeHotkey().install("F10", COMMAND) is False


def test_gnome_refuses_an_empty_key(gnome):
    assert GnomeHotkey().install("", COMMAND) is False
    assert gnome.slots() == []


# -- KDE, against real files ----------------------------------------------


@pytest.fixture
def kde(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(hotkey.shutil, "which", lambda name: None)
    return KdeHotkey()


def test_kde_writes_a_desktop_file_and_a_shortcut(kde):
    assert kde.install("F10", COMMAND)

    entry = kde.desktop_path().read_text(encoding="utf-8")
    assert f"Exec={COMMAND}" in entry
    # Not hidden: Plasma 6's shortcuts page cannot find a NoDisplay entry and
    # errors out instead of listing it (KDE bug 504232), which leaves the
    # user with a binding they can neither see nor manage.
    assert "NoDisplay=true" not in entry
    assert "X-KDE-GlobalAccel-CommandShortcut=true" in entry

    shortcuts = kde.shortcuts_path().read_text(encoding="utf-8")
    assert f"[{KDE_DESKTOP_FILENAME}]" in shortcuts
    assert "_launch=F10,none," in shortcuts


def test_kde_reports_the_binding_it_installed(kde):
    assert kde.installed_sequence() == ""
    kde.install("Ctrl+Shift+D", COMMAND)
    assert kde.installed_sequence() == "Ctrl+Shift+D"


def test_kde_rebinding_replaces_rather_than_repeats(kde):
    kde.install("F10", COMMAND)
    kde.install("Meta+X", COMMAND)
    shortcuts = kde.shortcuts_path().read_text(encoding="utf-8")
    assert shortcuts.count(f"[{KDE_DESKTOP_FILENAME}]") == 1
    assert "F10" not in shortcuts
    assert kde.installed_sequence() == "Meta+X"


def test_kde_leaves_every_other_shortcut_exactly_as_it_was(kde):
    """This file holds all of the user's shortcuts. Losing one is unforgivable."""
    existing = (
        "[kwin]\n"
        "_k_friendly_name=KWin\n"
        "Switch Window Down=Meta+Alt+Down,Meta+Alt+Down,Switch to Window Below\n"
        "Expose=Ctrl+F9,Ctrl+F9,Toggle Present Windows (Current desktop)\n"
        "\n"
        "[org.kde.konsole.desktop]\n"
        "_k_friendly_name=Konsole\n"
        "_launch=Ctrl+Alt+T,none,Konsole\n"
    )
    kde.shortcuts_path().parent.mkdir(parents=True, exist_ok=True)
    kde.shortcuts_path().write_text(existing, encoding="utf-8")

    kde.install("F10", COMMAND)
    after = kde.shortcuts_path().read_text(encoding="utf-8")
    assert existing in after, "the original content must survive untouched"

    kde.remove()
    assert kde.shortcuts_path().read_text(encoding="utf-8").rstrip("\n") == (
        existing.rstrip("\n")
    )


def test_kde_remove_takes_the_desktop_file_with_it(kde):
    kde.install("F10", COMMAND)
    assert kde.desktop_path().exists()
    assert kde.remove()
    assert not kde.desktop_path().exists()
    assert f"[{KDE_DESKTOP_FILENAME}]" not in kde.shortcuts_path().read_text(
        encoding="utf-8"
    )


def test_kde_remove_is_fine_with_nothing_installed(kde):
    assert kde.remove()


def test_kde_refuses_an_empty_key(kde):
    assert kde.install("", COMMAND) is False
    assert not kde.desktop_path().exists()


def test_kde_survives_an_unwritable_config_directory(kde, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(hotkey.Path, "write_text", refuse)
    assert kde.install("F10", COMMAND) is False


def test_kde_reindexes_when_the_tool_is_there(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(
        hotkey.shutil, "which", lambda name: "/usr/bin/kbuildsycoca6"
        if name == "kbuildsycoca6" else None
    )
    ran: list[list[str]] = []
    monkeypatch.setattr(
        hotkey, "_run",
        lambda args: ran.append(args) or subprocess.CompletedProcess(args, 0, "", ""),
    )
    assert KdeHotkey().install("F10", COMMAND)
    assert ran == [["kbuildsycoca6"]], "Plasma has to be told the entry exists"


def test_kde_asks_kwin_to_reload_after_writing(kde, monkeypatch):
    """The written file is not the copy that decides a key press.

    On a Plasma 6 Wayland session kglobalaccel is part of KWin, so the systemd
    unit nextdeck stops around the write is not where the live shortcuts are.
    Without this call the binding is correct on disk and dead until re-login.
    """
    monkeypatch.setattr(
        hotkey.shutil,
        "which",
        lambda name: "/usr/bin/qdbus6" if name == "qdbus6" else None,
    )
    ran: list[list[str]] = []
    monkeypatch.setattr(
        hotkey, "_run",
        lambda args: ran.append(args) or subprocess.CompletedProcess(args, 0, "", ""),
    )
    assert kde.install("F10", COMMAND)
    assert ["qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"] in ran


def test_kde_removal_asks_kwin_to_reload_too(kde, monkeypatch):
    kde.install("F10", COMMAND)
    monkeypatch.setattr(
        hotkey.shutil,
        "which",
        lambda name: "/usr/bin/qdbus6" if name == "qdbus6" else None,
    )
    ran: list[list[str]] = []
    monkeypatch.setattr(
        hotkey, "_run",
        lambda args: ran.append(args) or subprocess.CompletedProcess(args, 0, "", ""),
    )
    assert kde.remove()
    assert ["qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"] in ran


def test_a_reload_that_cannot_be_made_does_not_fail_the_install(kde):
    """No qdbus, no gdbus, no KWin. The file is still written."""
    assert hotkey._kwin_reconfigure() is False
    assert kde.install("F10", COMMAND)
    assert kde.installed_sequence() == "F10"


def test_gdbus_is_used_when_no_qdbus_is_installed(monkeypatch):
    monkeypatch.setattr(
        hotkey.shutil,
        "which",
        lambda name: "/usr/bin/gdbus" if name == "gdbus" else None,
    )
    ran: list[list[str]] = []
    monkeypatch.setattr(
        hotkey, "_run",
        lambda args: ran.append(args) or subprocess.CompletedProcess(args, 0, "", ""),
    )
    assert hotkey._kwin_reconfigure() is True
    assert ran[0][:3] == ["gdbus", "call", "--session"]
    assert "org.kde.KWin.reconfigure" in ran[0]


def test_a_failed_dbus_call_is_not_a_reload(monkeypatch):
    monkeypatch.setattr(
        hotkey.shutil,
        "which",
        lambda name: "/usr/bin/qdbus6" if name == "qdbus6" else None,
    )
    monkeypatch.setattr(
        hotkey, "_run",
        lambda args: subprocess.CompletedProcess(args, 1, "", "no such service"),
    )
    assert hotkey._kwin_reconfigure() is False


def test_a_dbus_tool_that_blows_up_is_survived(monkeypatch):
    monkeypatch.setattr(
        hotkey.shutil,
        "which",
        lambda name: "/usr/bin/qdbus6" if name == "qdbus6" else None,
    )

    def explode(args):
        raise OSError("boom")

    monkeypatch.setattr(hotkey, "_run", explode)
    assert hotkey._kwin_reconfigure() is False


# -- is the live session actually holding the shortcut? -------------------


def _dbus_answering(monkeypatch, stdout: str, returncode: int = 0):
    monkeypatch.setattr(
        hotkey.shutil,
        "which",
        lambda name: "/usr/bin/qdbus6" if name == "qdbus6" else None,
    )
    monkeypatch.setattr(
        hotkey, "_run",
        lambda args: subprocess.CompletedProcess(args, returncode, stdout, ""),
    )


def test_kglobalaccel_knowing_the_component(monkeypatch):
    _dbus_answering(
        monkeypatch,
        "/component/kwin\n/component/nextdeck-toggle_desktop\n",
    )
    assert hotkey._kglobalaccel_knows_us() is True


def test_kglobalaccel_not_knowing_the_component(monkeypatch):
    """Written to the file, never read by the session — the bug we chased."""
    _dbus_answering(monkeypatch, "/component/kwin\n/component/plasmashell\n")
    assert hotkey._kglobalaccel_knows_us() is False


def test_kglobalaccel_that_cannot_be_asked_says_so(monkeypatch):
    """None, not False: "we could not look" is not "it is not there"."""
    monkeypatch.setattr(hotkey.shutil, "which", lambda name: None)
    assert hotkey._kglobalaccel_knows_us() is None
    _dbus_answering(monkeypatch, "", returncode=1)
    assert hotkey._kglobalaccel_knows_us() is None


def _systemctl_answering(monkeypatch, stdout: str):
    monkeypatch.setattr(
        hotkey.shutil,
        "which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    monkeypatch.setattr(
        hotkey, "_run",
        lambda args: subprocess.CompletedProcess(args, 0, stdout, ""),
    )


def test_the_daemon_unit_state_is_read(monkeypatch):
    _systemctl_answering(monkeypatch, "LoadState=loaded\nActiveState=active\n")
    assert hotkey._unit_state() == ("loaded", "active")


def test_a_missing_daemon_unit_is_reported_as_such(monkeypatch):
    """Plasma 6 has no such unit; stopping it around the write does nothing."""
    _systemctl_answering(monkeypatch, "LoadState=not-found\nActiveState=inactive\n")
    assert hotkey._unit_state() == ("not-found", "inactive")


def test_the_daemon_unit_without_systemctl(monkeypatch):
    monkeypatch.setattr(hotkey.shutil, "which", lambda name: None)
    assert hotkey._unit_state() == ("", "")


def test_a_systemctl_that_blows_up_is_survived(monkeypatch):
    monkeypatch.setattr(hotkey.shutil, "which", lambda name: "/usr/bin/systemctl")

    def explode(args):
        raise subprocess.TimeoutExpired(args, 10)

    monkeypatch.setattr(hotkey, "_run", explode)
    assert hotkey._unit_state() == ("", "")


# -- who else wants this key? ---------------------------------------------


KGLOBALSHORTCUTSRC = """[kwin]
_k_friendly_name=KWin
Overview=Meta+W,Meta+W,Toggle Overview
Suspend=F9,none,Suspend

[nextdeck-toggle.desktop]
_k_friendly_name=F9
_launch=F9,none,nextdeck: show or hide

[org_kde_powerdevil]
_k_friendly_name=Power Management
Hibernate=Meta+Shift+H,none,Hibernate
"""


def test_a_key_claimed_twice_is_found():
    assert hotkey._sections_bound_to(KGLOBALSHORTCUTSRC, "F9") == [
        "kwin",
        KDE_DESKTOP_FILENAME,
    ]


def test_a_key_nothing_else_wants():
    assert hotkey._sections_bound_to(KGLOBALSHORTCUTSRC, "Meta+Shift+H") == [
        "org_kde_powerdevil"
    ]


def test_the_friendly_name_is_not_mistaken_for_a_binding():
    """`_k_friendly_name=F9` is a label, not a key. It must not count."""
    text = "[a.desktop]\n_k_friendly_name=F9\n_launch=F10,none,a\n"
    assert hotkey._sections_bound_to(text, "F9") == []


def test_a_default_binding_is_not_the_live_one():
    """The second field is the default a key was changed from, not the key."""
    text = "[kwin]\nSomething=none,F9,Something\n"
    assert hotkey._sections_bound_to(text, "F9") == []


def test_a_key_is_matched_however_it_is_cased():
    assert hotkey._sections_bound_to("[kwin]\nX=f9,none,X\n", "F9") == ["kwin"]


def test_no_key_claims_nothing():
    assert hotkey._sections_bound_to(KGLOBALSHORTCUTSRC, "") == []


# -- the section editor underneath ----------------------------------------


def test_a_section_can_be_added_to_an_empty_file():
    out = hotkey._replace_section("", "mine", ["a=1"])
    assert out == "[mine]\na=1\n"


def test_a_section_is_added_after_a_blank_line():
    out = hotkey._replace_section("[other]\nx=1\n", "mine", ["a=1"])
    assert out == "[other]\nx=1\n\n[mine]\na=1\n"


def test_a_missing_section_removed_changes_nothing():
    text = "[other]\nx=1\n"
    assert hotkey._replace_section(text, "mine", None) == text


def test_a_section_in_the_middle_is_replaced_in_place():
    text = "[a]\n1=1\n\n[mine]\nold=1\n\n[z]\n2=2\n"
    out = hotkey._replace_section(text, "mine", ["new=1"])
    assert out == "[a]\n1=1\n\n[mine]\nnew=1\n[z]\n2=2\n"
    assert "old=1" not in out


def test_gnome_reads_an_empty_shortcut_list(monkeypatch):
    """gsettings prints an empty array as "@as []", not as "[]"."""
    calls = []

    def fake(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "@as []\n", "")

    monkeypatch.setattr(hotkey, "_run", fake)
    monkeypatch.setattr(hotkey.shutil, "which", lambda name: "/usr/bin/gsettings")
    assert GnomeHotkey()._slots() == []


def test_gnome_reads_a_populated_shortcut_list(monkeypatch):
    slots = [f"{GNOME_SLOT_PREFIX}custom0/", f"{GNOME_SLOT_PREFIX}custom3/"]

    monkeypatch.setattr(
        hotkey, "_run",
        lambda args: subprocess.CompletedProcess(args, 0, str(slots) + "\n", ""),
    )
    monkeypatch.setattr(hotkey.shutil, "which", lambda name: "/usr/bin/gsettings")
    assert GnomeHotkey()._slots() == slots


# -- holding the shortcut daemon still -------------------------------------


class FakeTools:
    """Records which desktop tools were run, in order."""

    def __init__(self, available=("systemctl", "kbuildsycoca6"), fail=()):
        self.available = set(available)
        self.fail = set(fail)
        self.calls: list[list[str]] = []

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.available else None

    def run(self, args):
        self.calls.append(args)
        code = 1 if args[0] in self.fail else 0
        return subprocess.CompletedProcess(args, code, "", "")

    def verbs(self):
        return [
            args[2] if args[0] == "systemctl" else args[0] for args in self.calls
        ]


@pytest.fixture
def kde_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    tools = FakeTools()
    monkeypatch.setattr(hotkey.shutil, "which", tools.which)
    monkeypatch.setattr(hotkey, "_run", tools.run)
    return tools


def test_kde_stops_the_daemon_before_writing_and_starts_it_after(kde_tools):
    """It rewrites the whole file from memory, so it must not be running."""
    assert KdeHotkey().install("F10", COMMAND)
    assert kde_tools.verbs() == ["stop", "kbuildsycoca6", "start"]
    assert kde_tools.calls[0] == [
        "systemctl", "--user", "stop", hotkey.KGLOBALACCEL_UNIT
    ]


def test_kde_restarts_the_daemon_after_removing_too(kde_tools):
    KdeHotkey().install("F10", COMMAND)
    kde_tools.calls.clear()
    assert KdeHotkey().remove()
    assert kde_tools.verbs() == ["stop", "kbuildsycoca6", "start"]


def test_the_daemon_is_restarted_even_when_the_write_fails(kde_tools, monkeypatch):
    """Leaving a user with no global shortcuts at all would be unforgivable.

    The failure has to be the shortcuts file, not the desktop entry: that one
    is written before the daemon is stopped, so there is nothing to restart.
    """
    real = hotkey.Path.write_text

    def fail_on_shortcuts(self, *args, **kwargs):
        if hotkey.KDE_SHORTCUTS_FILE in self.name:
            raise OSError("read-only file system")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(hotkey.Path, "write_text", fail_on_shortcuts)
    assert KdeHotkey().install("F10", COMMAND) is False
    assert "start" in kde_tools.verbs(), "the daemon must come back up"


def test_a_failed_desktop_entry_never_stops_the_daemon(kde_tools, monkeypatch):
    """It is written first, so that failure costs the user nothing at all."""
    monkeypatch.setattr(
        hotkey.Path, "write_text",
        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")),
    )
    assert KdeHotkey().install("F10", COMMAND) is False
    assert kde_tools.calls == [], "the daemon was never touched"


def test_the_write_still_happens_without_systemd(tmp_path, monkeypatch):
    """No systemd user session: worse, because it needs a re-login. Not fatal."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    tools = FakeTools(available=())
    monkeypatch.setattr(hotkey.shutil, "which", tools.which)
    monkeypatch.setattr(hotkey, "_run", tools.run)

    assert KdeHotkey().install("F10", COMMAND)
    assert tools.calls == []
    assert KdeHotkey().installed_sequence() == "F10"


def test_a_daemon_that_will_not_stop_is_not_then_started(tmp_path, monkeypatch):
    """Starting a daemon we never stopped could restart one the user had off."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    tools = FakeTools(fail=("systemctl",))
    monkeypatch.setattr(hotkey.shutil, "which", tools.which)
    monkeypatch.setattr(hotkey, "_run", tools.run)

    assert KdeHotkey().install("F10", COMMAND)
    assert tools.verbs().count("start") == 0
    assert KdeHotkey().installed_sequence() == "F10", "the write still happened"


# -- the diagnostic --------------------------------------------------------


def test_diagnose_reports_nothing_bound(kde_tools, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    report = "\n".join(hotkey.diagnose())
    assert "KDE Plasma" in report
    assert "MISSING" in report
    assert "NONE" in report


def test_diagnose_reports_what_is_bound(kde_tools, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    KdeHotkey().install("Ctrl+Shift+D", COMMAND)
    report = "\n".join(hotkey.diagnose())
    assert "Ctrl+Shift+D" in report
    assert "_launch=Ctrl+Shift+D,none," in report, "the raw entry helps debugging"
    assert "MISSING" not in report


def test_diagnose_names_the_command_to_bind(kde_tools, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    report = "\n".join(hotkey.diagnose(tmp_path / "work.yaml"))
    assert "--toggle" in report
    assert f"--config {tmp_path / 'work.yaml'}" in report


def test_diagnose_on_an_unsupported_desktop_says_so(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "Hyprland")
    report = "\n".join(hotkey.diagnose())
    assert "unsupported" in report
    assert "--toggle" in report, "it still has to say what to bind"


def test_diagnose_covers_gnome_too(monkeypatch, gnome):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    GnomeHotkey().install("F10", COMMAND)
    report = "\n".join(hotkey.diagnose())
    assert "GNOME" in report
    assert "F10" in report


# -- the --check-hotkey command -------------------------------------------


def test_check_hotkey_is_a_flag(tmp_path):
    from nextdeck.__main__ import parse_args

    assert parse_args(["--check-hotkey"]).check_hotkey is True
    assert parse_args([]).check_hotkey is False


@pytest.fixture(scope="session")
def widget_app():
    """A real QApplication, built before anything builds a bare one.

    `--check-hotkey` creates a QCoreApplication when no application exists —
    it is a command-line report and has no business needing a display. A
    QCoreApplication cannot host widgets, though, and Qt will not let one be
    replaced, so leaving one behind aborts the first widget test in whichever
    file runs next. Making the real thing first keeps this file harmless to
    its neighbours.
    """
    QApplication = pytest.importorskip("PySide6.QtWidgets").QApplication
    return QApplication.instance() or QApplication([])


def test_check_hotkey_reports_and_exits_without_building_a_window(
    widget_app, tmp_path, monkeypatch, capsys
):
    """It has to run while nextdeck is hidden, so it must not start a second one."""
    from nextdeck.__main__ import main

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    config = tmp_path / "config.yaml"
    config.write_text(
        "server: {url: 'https://x', username: u, app_password: p}\n"
        "board: {id: 1, default_stack_id: 2}\n"
        "ui: {toggle_shortcut: 'F10'}\n",
        encoding="utf-8",
    )

    built = []
    import nextdeck.ui.window as windowmod

    monkeypatch.setattr(
        windowmod, "MainWindow",
        lambda *a, **k: built.append(a) or pytest.fail("built a window"),
    )

    assert main(["--check-hotkey", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "KDE Plasma" in out
    assert "--toggle" in out
    assert "Key in config.yaml" in out and "F10" in out
    assert built == []


# -- --register-hotkey ----------------------------------------------------


def test_register_hotkey_is_a_flag():
    from nextdeck.__main__ import parse_args

    assert parse_args(["--register-hotkey"]).register_hotkey is True
    assert parse_args([]).register_hotkey is False


def _register(config, monkeypatch, desk=None, config_path=None):
    from argparse import Namespace
    from nextdeck.__main__ import _register_hotkey

    if desk is not None:
        monkeypatch.setattr(hotkey, "backend", lambda *a, **k: desk)
    return _register_hotkey(Namespace(config=config_path), config)


def test_register_hotkey_installs_the_configured_key(kde_tools, monkeypatch, capsys):
    from nextdeck.config import Config

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    config = Config()
    config.ui.toggle_shortcut = "F9"

    assert _register(config, monkeypatch) == 0
    assert KdeHotkey().installed_sequence() == "F9"
    assert "Registered F9 with KDE Plasma" in capsys.readouterr().out


def test_register_hotkey_refuses_when_no_key_is_set(kde_tools, monkeypatch, capsys):
    from nextdeck.config import Config

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert _register(Config(), monkeypatch) == 1
    assert "No show/hide key is set" in capsys.readouterr().out
    assert KdeHotkey().installed_sequence() == ""


def test_register_hotkey_prints_the_command_on_an_unsupported_desktop(
    monkeypatch, capsys
):
    from nextdeck.config import Config

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "Hyprland")
    config = Config()
    config.ui.toggle_shortcut = "F9"

    assert _register(config, monkeypatch) == 1
    out = capsys.readouterr().out
    assert "--toggle" in out and "F9" in out


def test_register_hotkey_reports_a_desktop_that_refuses(monkeypatch, capsys):
    from nextdeck.config import Config

    class Refuses(Hotkey):
        name = "KDE Plasma"

        def install(self, sequence, command):
            return False

    config = Config()
    config.ui.toggle_shortcut = "F9"
    assert _register(config, monkeypatch, desk=Refuses()) == 1
    assert "would not take the shortcut" in capsys.readouterr().out


def test_register_hotkey_carries_the_config_path(kde_tools, monkeypatch, tmp_path):
    from nextdeck.config import Config

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    config = Config()
    config.ui.toggle_shortcut = "F9"
    path = tmp_path / "work.yaml"

    assert _register(config, monkeypatch, config_path=path) == 0
    entry = KdeHotkey().desktop_path().read_text(encoding="utf-8")
    assert f"--config {path}" in entry


# -- is the binding live, or only written? --------------------------------


def test_kde_without_a_daemon_to_restart_is_not_live(kde):
    """Plasma 6: no such unit, so the key waits for the next login.

    The user's own session proved this: `systemctl --user stop
    plasma-kglobalaccel.service` answers "Unit not loaded".
    """
    assert kde.install("F9", COMMAND)
    assert kde.went_live() is False


def test_kde_with_a_daemon_it_restarted_is_live(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(
        hotkey.shutil,
        "which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    monkeypatch.setattr(
        hotkey, "_run",
        lambda args: subprocess.CompletedProcess(args, 0, "", ""),
    )
    kde = KdeHotkey()
    assert kde.install("F9", COMMAND)
    assert kde.went_live() is True


def test_kde_is_not_live_before_anything_is_installed(kde):
    assert kde.went_live() is False


def test_a_reconfigure_alone_is_not_treated_as_live(kde, monkeypatch):
    """Unverified levers do not get to claim success.

    KWin takes a reconfigure happily and there is no evidence it re-reads the
    shortcut file, so counting it would send the user looking for a conflict
    that is not there — which is exactly the dead end this came out of.
    """
    monkeypatch.setattr(
        hotkey.shutil,
        "which",
        lambda name: "/usr/bin/qdbus6" if name == "qdbus6" else None,
    )
    monkeypatch.setattr(
        hotkey, "_run",
        lambda args: subprocess.CompletedProcess(args, 0, "", ""),
    )
    assert kde.install("F9", COMMAND)
    assert kde.went_live() is False


def test_gnome_is_live_as_soon_as_it_is_written(gnome):
    """GSettings is watched; there is nothing to restart."""
    desk = GnomeHotkey()
    assert desk.install("F9", COMMAND)
    assert desk.went_live() is True


def test_gnome_reports_what_the_session_holds(gnome):
    desk = GnomeHotkey()
    assert desk.session_has_it() is False
    desk.install("F9", COMMAND)
    assert desk.session_has_it() is True


def test_an_unsupported_desktop_knows_nothing_either_way():
    """None, not False. Nothing was asked, so nothing can be concluded."""
    assert Hotkey().session_has_it() is None
    assert Hotkey().went_live() is False

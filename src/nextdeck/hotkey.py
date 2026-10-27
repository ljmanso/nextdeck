"""Registering the show/hide key with the desktop that owns the keyboard.

Qt only sees a key while nextdeck has focus, so an in-app shortcut can dismiss
the window but never summon it back out of the tray — by then the key is going
to whatever is in front. The desktop *can* grab a key globally, so this module
asks it to, pointing the binding at `nextdeck --toggle`.

Two desktops are handled, because they are the two that keep shortcuts
somewhere a program can write:

* **GNOME** keeps custom shortcuts in GSettings: a list of slot paths, each
  slot a relocatable schema holding a name, a command and a binding.
* **KDE Plasma** pairs a `.desktop` file with a section in
  `kglobalshortcutsrc` named after it.

Anything else gets instructions instead — `autostart.toggle_command()` is the
command to bind by hand.

Nothing here raises. A desktop that will not take a shortcut is a reason to
tell the user to bind it themselves, never a reason to fail saving settings.
"""

from __future__ import annotations

import ast
import logging
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

from . import autostart

log = logging.getLogger(__name__)

#: Long enough for a loaded session, short enough not to hang the dialog.
COMMAND_TIMEOUT_S = 10

#: What we call the binding wherever the desktop shows it a user.
FRIENDLY_NAME = "nextdeck: show or hide"

#: How nextdeck recognises its own binding again, in GNOME's `name` field.
GNOME_MARKER = "nextdeck"
GNOME_MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
GNOME_CUSTOM_SCHEMA = f"{GNOME_MEDIA_KEYS}.custom-keybinding"
GNOME_SLOT_PREFIX = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
#: Enough slots to find a free one past anything a person has set up by hand.
GNOME_SLOT_LIMIT = 64

KDE_DESKTOP_FILENAME = "nextdeck-toggle.desktop"
KDE_SHORTCUTS_FILE = "kglobalshortcutsrc"

#: Tried in order; the first one installed is used to talk to KWin.
QDBUS_TOOLS = ("qdbus6", "qdbus-qt6", "qdbus")

#: Qt spells modifiers the way KDE does, but not the way GTK does.
GNOME_MODIFIERS = {
    "ctrl": "<Control>",
    "shift": "<Shift>",
    "alt": "<Alt>",
    "meta": "<Super>",
}
#: Qt's name for a key, where GTK's differs.
GNOME_KEYS = {
    "esc": "Escape",
    "space": "space",
    "del": "Delete",
    "ins": "Insert",
    "pgup": "Page_Up",
    "pgdown": "Page_Down",
    "enter": "Return",
    "backspace": "BackSpace",
    "print": "Print",
}


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Run a desktop tool. Separated out so tests can watch what we send."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_S,
        check=False,
    )


# -- key translation -------------------------------------------------------


def to_gnome_accelerator(sequence: str) -> str:
    """Qt's "Ctrl+Shift+D" as GTK's "<Control><Shift>d".

    GTK looks a key up by name and the names are case sensitive, so a bare
    "D" would mean shift-d and a shortcut written Ctrl+D would quietly need
    Ctrl+Shift+D to fire.
    """
    if not sequence:
        return ""
    *modifiers, key = [part for part in sequence.split("+") if part]
    prefix = "".join(GNOME_MODIFIERS.get(m.strip().lower(), "") for m in modifiers)
    key = key.strip()
    lowered = key.lower()
    if lowered in GNOME_KEYS:
        return prefix + GNOME_KEYS[lowered]
    if len(key) == 1:
        return prefix + lowered
    if lowered.startswith("f") and lowered[1:].isdigit():
        return prefix + key.upper()
    return prefix + key


def to_kde_shortcut(sequence: str) -> str:
    """KDE spells a shortcut the way Qt does, so this is a pass-through."""
    return sequence


# -- the desktops ----------------------------------------------------------


class Hotkey:
    """What every backend can do. The base is the "no desktop support" case."""

    #: Shown to the user, e.g. "Also register F10 with GNOME?"
    name = ""

    def install(self, sequence: str, command: str) -> bool:
        return False

    def remove(self) -> bool:
        return False

    def installed_sequence(self) -> str:
        """The binding nextdeck has registered, or "" if there is none."""
        return ""

    def session_has_it(self) -> bool | None:
        """Whether the live session is holding nextdeck's binding right now.

        True or False when the session could be asked, None when it could
        not — and the two must not be collapsed, because "we could not look"
        is not "it is not there". This is the difference between a shortcut
        that is written and one that will actually fire.
        """
        return None

    def went_live(self) -> bool:
        """Whether the last `install` reached the *running* session.

        False means the binding is written and correct but will not fire until
        the session loads it, which in practice means logging out and back in.
        It is worth reporting separately because "registered but dead" is by
        far the most confusing state this feature has: every file is right,
        every check passes, and the key does nothing.
        """
        return False


class GnomeHotkey(Hotkey):
    name = "GNOME"

    def _gsettings(self, *args: str) -> tuple[bool, str]:
        if not shutil.which("gsettings"):
            return False, "gsettings is not installed"
        try:
            done = _run(["gsettings", *args])
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        if done.returncode != 0:
            return False, (done.stderr or "").strip()
        return True, (done.stdout or "").strip()

    def _slots(self) -> list[str]:
        ok, raw = self._gsettings("get", GNOME_MEDIA_KEYS, "custom-keybindings")
        if not ok:
            return []
        # GSettings prints a populated array in a Python-ish shape, which is
        # why this is literal_eval and not JSON. An empty one comes back as
        # "@as []" with its type attached, which does not parse — and lands on
        # the same empty list, which is the right answer for it anyway.
        try:
            value = ast.literal_eval(raw.strip())
        except (ValueError, SyntaxError):
            return []
        return [str(item) for item in value] if isinstance(value, (list, tuple)) else []

    def _get(self, slot: str, key: str) -> str:
        ok, raw = self._gsettings("get", f"{GNOME_CUSTOM_SCHEMA}:{slot}", key)
        if not ok:
            return ""
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
            return raw[1:-1]
        return raw

    def _our_slot(self) -> str:
        for slot in self._slots():
            if self._get(slot, "name") == GNOME_MARKER:
                return slot
        return ""

    def _free_slot(self, taken: list[str]) -> str:
        for index in range(GNOME_SLOT_LIMIT):
            slot = f"{GNOME_SLOT_PREFIX}custom{index}/"
            if slot not in taken:
                return slot
        return ""

    def installed_sequence(self) -> str:
        slot = self._our_slot()
        return self._get(slot, "binding") if slot else ""

    def session_has_it(self) -> bool | None:
        return bool(self.installed_sequence())

    def went_live(self) -> bool:
        """GSettings is watched, so a write is in effect as it is made.

        Nothing to restart and nothing to log out of — which is why the GNOME
        half of this has never needed the KDE half's apology.
        """
        return True

    def install(self, sequence: str, command: str) -> bool:
        accelerator = to_gnome_accelerator(sequence)
        if not accelerator:
            return False
        slots = self._slots()
        slot = self._our_slot() or self._free_slot(slots)
        if not slot:
            log.warning("no free GNOME custom shortcut slot")
            return False

        target = f"{GNOME_CUSTOM_SCHEMA}:{slot}"
        for key, value in (
            ("name", GNOME_MARKER),
            ("command", command),
            ("binding", accelerator),
        ):
            ok, error = self._gsettings("set", target, key, value)
            if not ok:
                log.warning("could not set %s on %s: %s", key, slot, error)
                return False

        if slot not in slots:
            # Written last: a slot listed before its fields are filled in shows
            # up in the GNOME settings panel as a blank, broken shortcut.
            ok, error = self._gsettings(
                "set", GNOME_MEDIA_KEYS, "custom-keybindings", str(slots + [slot])
            )
            if not ok:
                log.warning("could not register the shortcut slot: %s", error)
                return False
        return True

    def remove(self) -> bool:
        slot = self._our_slot()
        if not slot:
            return True
        remaining = [item for item in self._slots() if item != slot]
        ok, error = self._gsettings(
            "set", GNOME_MEDIA_KEYS, "custom-keybindings", str(remaining)
        )
        if not ok:
            log.warning("could not drop the shortcut slot: %s", error)
            return False
        # Reset after de-listing, so a failure here leaves an unlisted slot
        # rather than a listed one pointing at nothing.
        self._gsettings("reset-recursively", f"{GNOME_CUSTOM_SCHEMA}:{slot}")
        return True


class KdeHotkey(Hotkey):
    name = "KDE Plasma"

    #: Set by install(); see went_live().
    _live = False

    def desktop_path(self) -> Path:
        return autostart.applications_dir() / KDE_DESKTOP_FILENAME

    def shortcuts_path(self) -> Path:
        config = os.environ.get("XDG_CONFIG_HOME")
        base = Path(config) if config else Path.home() / ".config"
        return base / KDE_SHORTCUTS_FILE

    def _desktop_contents(self, command: str) -> str:
        return "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                f"Name={FRIENDLY_NAME}",
                f"Exec={command}",
                # Deliberately *not* NoDisplay. Plasma 6's shortcuts page
                # cannot find an entry hidden that way and reports "error
                # while communicating with the global shortcuts service"
                # (KDE bug 504232), which leaves a shortcut that is written
                # correctly and never fires. The cost is one extra entry in
                # the application menu, which is the lesser problem.
                "NoDisplay=false",
                "StartupNotify=false",
                # Marks the entry as a command rather than an application, so
                # Plasma lists it under Commands, where someone looking for it
                # would expect to find it.
                "X-KDE-GlobalAccel-CommandShortcut=true",
                "",
            ]
        )

    def installed_sequence(self) -> str:
        body = _read_section(_read_text(self.shortcuts_path()), KDE_DESKTOP_FILENAME)
        for line in body:
            if line.startswith("_launch="):
                # "_launch=F10,none,description" — the first field is the key.
                return line.split("=", 1)[1].split(",")[0]
        return ""

    def install(self, sequence: str, command: str) -> bool:
        shortcut = to_kde_shortcut(sequence)
        if not shortcut:
            return False
        desktop = self.desktop_path()
        try:
            desktop.parent.mkdir(parents=True, exist_ok=True)
            desktop.write_text(self._desktop_contents(command), encoding="utf-8")
        except OSError as exc:
            log.warning("could not write %s: %s", desktop, exc)
            return False

        body = [
            f"_k_friendly_name={FRIENDLY_NAME}",
            # Key, the alternate binding (none), then the label Plasma shows.
            f"_launch={shortcut},none,{FRIENDLY_NAME}",
        ]
        # The daemon holds every shortcut in memory and writes the file back
        # out again, so a section added underneath it is simply discarded the
        # next time it saves. Stop it, write, start it again.
        with _kglobalaccel_stopped() as stopped:
            if not self._write_section(body):
                return False
            _reindex()
        # Best effort, and deliberately not counted as success below. On a
        # Plasma 6 Wayland session kglobalaccel is part of KWin rather than a
        # process of its own, and KWin has no documented way to re-read the
        # shortcut file; a reconfigure may do it on some versions, which is
        # worth a cheap call but not worth telling the user it worked.
        _kwin_reconfigure()
        # Restarting a daemon that exists is the one lever that definitely
        # loads the new section. Where there is no such daemon — Plasma 6 —
        # the binding waits for the next login, and the user needs to hear so.
        self._live = stopped
        return True

    def session_has_it(self) -> bool | None:
        return _kglobalaccel_knows_us()

    def went_live(self) -> bool:
        return self._live

    def remove(self) -> bool:
        self.desktop_path().unlink(missing_ok=True)
        with _kglobalaccel_stopped():
            removed = self._write_section(None)
            _reindex()
        _kwin_reconfigure()
        return removed

    def _write_section(self, body: list[str] | None) -> bool:
        path = self.shortcuts_path()
        try:
            updated = _replace_section(
                _read_text(path), KDE_DESKTOP_FILENAME, body
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            # Written through a temporary file: this is the file holding every
            # shortcut the user has, and a half-written one would lose them.
            temporary = path.with_name(path.name + ".nextdeck-new")
            temporary.write_text(updated, encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            log.warning("could not update %s: %s", path, exc)
            return False
        return True


KGLOBALACCEL_UNIT = "plasma-kglobalaccel.service"


def _systemctl(action: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        done = _run(["systemctl", "--user", action, KGLOBALACCEL_UNIT])
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("systemctl %s failed: %s", action, exc)
        return False
    return done.returncode == 0


def _unit_state() -> tuple[str, str]:
    """`(LoadState, ActiveState)` of the shortcut daemon unit.

    Empty strings when systemd cannot be asked. "not-found" as the load state
    is the interesting answer: it means the stop-and-start dance around the
    file write did nothing at all, because there is no such unit here.
    """
    if not shutil.which("systemctl"):
        return "", ""
    try:
        done = _run(
            [
                "systemctl",
                "--user",
                "show",
                "-p",
                "LoadState",
                "-p",
                "ActiveState",
                KGLOBALACCEL_UNIT,
            ]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("systemctl show failed: %s", exc)
        return "", ""
    fields = {}
    for line in done.stdout.splitlines():
        if "=" in line:
            name, _, value = line.partition("=")
            fields[name.strip()] = value.strip()
    return fields.get("LoadState", ""), fields.get("ActiveState", "")


def _qdbus_call(service: str, path: str, method: str) -> str | None:
    """One DBus method call through whichever qdbus or gdbus is installed.

    Returns the output, or None when the call could not be made at all — the
    two are worth telling apart, because a call that fails says the service is
    not there while no tool says only that we could not look.
    """
    for tool in QDBUS_TOOLS:
        if not shutil.which(tool):
            continue
        try:
            done = _run([tool, service, path, method])
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("%s %s failed: %s", tool, method, exc)
            continue
        return done.stdout if done.returncode == 0 else None
    if shutil.which("gdbus"):
        try:
            done = _run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    service,
                    "--object-path",
                    path,
                    "--method",
                    method,
                ]
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("gdbus %s failed: %s", method, exc)
            return None
        return done.stdout if done.returncode == 0 else None
    return None


def _kwin_reconfigure() -> bool:
    """Ask KWin to re-read its configuration, global shortcuts included.

    On Plasma 6 — and on Wayland in particular — kglobalaccel is part of KWin
    rather than a process of its own, so restarting the systemd unit leaves
    the shortcuts KWin holds in memory exactly as they were. This is the only
    way to make an edited `kglobalshortcutsrc` take effect short of logging
    out and back in, and it is best effort: on a session without KWin the
    call simply fails, which costs nothing.
    """
    return _qdbus_call("org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure") is not None


def _kglobalaccel_knows_us() -> bool | None:
    """Whether kglobalaccel has nextdeck's component loaded.

    True or False when kglobalaccel answered, None when it could not be
    asked. This is the difference between a shortcut that is written and
    live and one that is only written: the file is what we can change, but
    the component list is what actually decides the key press.
    """
    out = _qdbus_call(
        "org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel.allComponents"
    )
    if out is None:
        return None
    # Component paths, one per line, with dots replaced: the entry for
    # "nextdeck-toggle.desktop" comes back as ".../nextdeck-toggle_desktop".
    return "nextdeck-toggle" in out


@contextmanager
def _kglobalaccel_stopped():
    """Hold the shortcut daemon still while kglobalshortcutsrc is rewritten.

    kglobalaccel keeps every shortcut in memory and writes the whole file back
    when it saves, so a section added while it runs is lost again. Stopping it
    first is what makes the edit stick, and starting it again is what makes the
    new shortcut live without a re-login.

    If it cannot be stopped — no systemd user session, a Plasma 5 layout, a
    unit by another name — the write still happens and is picked up at the
    next login. That is worse, not fatal.
    """
    stopped = _systemctl("stop")
    try:
        yield stopped
    finally:
        if stopped:
            _systemctl("start")


def _reindex() -> None:
    """Let Plasma notice a new or removed .desktop file, if we can.

    Best effort, and never fatal: without it the entry still lands in the
    config and is picked up at the next login.
    """
    for tool in ("kbuildsycoca6", "kbuildsycoca5"):
        if shutil.which(tool):
            try:
                _run([tool])
            except (OSError, subprocess.SubprocessError) as exc:
                log.debug("%s failed: %s", tool, exc)
            return


# -- INI editing that leaves the rest of the file alone --------------------


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _section_bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    header = f"[{section}]"
    for index, line in enumerate(lines):
        if line.strip() != header:
            continue
        end = index + 1
        while end < len(lines) and not lines[end].lstrip().startswith("["):
            end += 1
        return index, end
    return None


def _read_section(text: str, section: str) -> list[str]:
    lines = text.splitlines()
    bounds = _section_bounds(lines, section)
    if bounds is None:
        return []
    start, end = bounds
    return [line.strip() for line in lines[start + 1 : end] if line.strip()]


def _sections_bound_to(text: str, shortcut: str) -> list[str]:
    """Every `[section]` of kglobalshortcutsrc that claims `shortcut`.

    KDE does not say which of two shortcuts lost a fight over a key, and the
    settings page only warns about a clash while you are editing one — so a
    key that something else already holds looks exactly like a key that was
    never registered. This reads the same file the daemon reads.
    """
    if not shortcut:
        return []
    wanted = shortcut.strip().lower()
    found: list[str] = []
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if not section or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == "_k_friendly_name":
            continue
        # "F9,none,label" — the first field is the live binding and the
        # second only the default it was changed from.
        if value.split(",")[0].strip().lower() == wanted and section not in found:
            found.append(section)
    return found


def _replace_section(text: str, section: str, body: list[str] | None) -> str:
    """Replace one `[section]` block, leaving every other line byte for byte.

    `kglobalshortcutsrc` holds every shortcut the user has. Round-tripping it
    through a parser would be a good way to lose the ones with syntax the
    parser does not share, so only our own lines are ever rewritten.
    """
    lines = text.splitlines()
    block = [] if body is None else [f"[{section}]", *body]
    bounds = _section_bounds(lines, section)

    if bounds is not None:
        start, end = bounds
        lines = lines[:start] + block + lines[end:]
    elif block:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(block)

    if not lines:
        return ""
    return "\n".join(lines) + "\n"


# -- picking one -----------------------------------------------------------


def detect_desktop() -> str:
    """A lower-case name for the running desktop, from the XDG environment."""
    raw = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION")
    return (raw or "").lower()


def backend(desktop: str | None = None) -> Hotkey:
    """The backend for this desktop; the do-nothing one when unsupported."""
    name = detect_desktop() if desktop is None else desktop.lower()
    # XDG_CURRENT_DESKTOP is colon-separated and often layered, e.g.
    # "ubuntu:GNOME" or "KDE", so this is a substring test on purpose.
    if "kde" in name or "plasma" in name:
        return KdeHotkey()
    if "gnome" in name or "unity" in name or "cinnamon" in name:
        return GnomeHotkey()
    return Hotkey()


def toggle_command(config_path: Path | None = None) -> str:
    """The command a global binding should run. Absolute, with --toggle."""
    return autostart.toggle_command(config_path)


# -- telling the user what is actually set up ------------------------------


def diagnose(config_path: Path | None = None) -> list[str]:
    """A report on the global shortcut, for `nextdeck --check-hotkey`.

    Every step of the chain in order, because a key that does nothing can
    fail at any of them and they need different fixes.
    """
    lines: list[str] = []

    def say(label: str, value: str) -> None:
        lines.append(f"{label:<22} {value}")

    session = os.environ.get("XDG_SESSION_TYPE") or "(unset)"
    say("XDG_CURRENT_DESKTOP", os.environ.get("XDG_CURRENT_DESKTOP") or "(unset)")
    say("XDG_SESSION_TYPE", session)

    desk = backend()
    say("Desktop nextdeck sees", desk.name or "unsupported — bind the command by hand")

    command = toggle_command(config_path)
    say("Command to bind", command)

    if isinstance(desk, KdeHotkey):
        entry = desk.desktop_path()
        say("Desktop entry", f"{entry} {'(present)' if entry.exists() else '(MISSING)'}")
        shortcuts = desk.shortcuts_path()
        say(
            "Shortcut file",
            f"{shortcuts} {'(present)' if shortcuts.exists() else '(MISSING)'}",
        )
        registered = desk.installed_sequence()
        say("Registered key", registered or "NONE — nothing is bound")
        if registered:
            body = _read_section(_read_text(shortcuts), KDE_DESKTOP_FILENAME)
            lines.append("")
            lines.append(f"  [{KDE_DESKTOP_FILENAME}]")
            lines.extend(f"  {line}" for line in body)
        load_state, active_state = _unit_state()
        if not load_state:
            say("Shortcut daemon", "systemctl not found — changes need a re-login")
        elif load_state == "not-found":
            say(
                "Shortcut daemon",
                f"{KGLOBALACCEL_UNIT} does not exist here — kglobalaccel is "
                "part of KWin, so a new key needs a re-login",
            )
        else:
            say("Shortcut daemon", f"{KGLOBALACCEL_UNIT} {load_state}/{active_state}")
        known = desk.session_has_it()
        say(
            "kglobalaccel knows it",
            "yes"
            if known
            else (
                "NO — the file is written but the live session has not read it"
                if known is False
                else "could not ask (no qdbus or gdbus installed)"
            ),
        )
        others = [
            name
            for name in _sections_bound_to(_read_text(shortcuts), registered)
            if name != KDE_DESKTOP_FILENAME
        ]
        if others:
            say("Key also claimed by", ", ".join(others))
    elif isinstance(desk, GnomeHotkey):
        say("gsettings", shutil.which("gsettings") or "NOT FOUND")
        registered = desk.installed_sequence()
        say("Registered key", registered or "NONE — nothing is bound")

    return lines

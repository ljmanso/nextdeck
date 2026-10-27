"""Window state that survives a restart.

Deliberately not part of config.yaml. That file holds the app password, is
written 0600 and is meant to be read and edited by hand; window geometry is
machine-written noise that changes every time you move the window, and
rewriting the credential file on every close is a risk taken for no benefit.
So this lives under `$XDG_STATE_HOME` (`~/.local/state` by default), which is
exactly the spec's slot for state that should persist but is not configuration.

The geometry itself is Qt's own `saveGeometry()` blob, base64-encoded. It is
opaque, but it is also the only representation that gets multi-monitor
layouts, maximised windows and screens that have since been unplugged right —
and `restoreGeometry` refuses data it cannot use, so a stale or corrupt value
costs nothing but the default size.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

STATE_FILENAME = "state.yaml"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "nextdeck"


def state_path() -> Path:
    return state_dir() / STATE_FILENAME


@dataclass
class WindowState:
    #: Base64 of QWidget.saveGeometry(); empty means "never saved".
    geometry: str = ""
    #: Set the first time nextdeck starts. Until it is, the window is shown
    #: whatever `start_minimized` says: a brand new install that went straight
    #: to the tray would look like it had not started at all.
    has_run_before: bool = False
    #: A show/hide key the user was offered and turned down. Remembered so
    #: that saying no once is not re-asked on every visit to Settings; a
    #: different key is a different question and is asked again.
    declined_hotkey: str = ""

    def geometry_bytes(self) -> bytes:
        if not self.geometry:
            return b""
        try:
            return base64.b64decode(self.geometry.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError, ValueError):
            log.warning("ignoring unreadable saved window geometry")
            return b""

    @classmethod
    def from_geometry_bytes(cls, raw: bytes | bytearray | memoryview) -> "WindowState":
        return cls(geometry=base64.b64encode(bytes(raw)).decode("ascii"))


def load(path: Path | None = None) -> WindowState:
    """Read the saved state, falling back to defaults for anything missing."""
    path = path or state_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return WindowState()
    except OSError as exc:
        log.warning("could not read %s: %s", path, exc)
        return WindowState()

    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        log.warning("ignoring unreadable %s: %s", path, exc)
        return WindowState()

    if not isinstance(data, dict):
        return WindowState()
    geometry = data.get("geometry")
    return WindowState(
        geometry=geometry if isinstance(geometry, str) else "",
        # Anything but a literal true is treated as "not yet", so a corrupt or
        # hand-edited file errs towards showing the window.
        has_run_before=data.get("has_run_before") is True,
        declined_hotkey=(
            data["declined_hotkey"]
            if isinstance(data.get("declined_hotkey"), str)
            else ""
        ),
    )


def mark_has_run(path: Path | None = None) -> None:
    """Record that nextdeck has started at least once.

    Read-modify-write so it cannot drop the saved geometry, and called at
    startup rather than at exit: a first run that is killed should not leave
    the app thinking it has still never run.
    """
    state = load(path)
    if state.has_run_before:
        return
    state.has_run_before = True
    save(state, path)


def remember_declined_hotkey(sequence: str, path: Path | None = None) -> None:
    """Record that the user said no to registering `sequence`."""
    state = load(path)
    if state.declined_hotkey == sequence:
        return
    state.declined_hotkey = sequence
    save(state, path)


def save(state: WindowState, path: Path | None = None) -> Path | None:
    """Write the state atomically. Never raises: this is a convenience."""
    path = path or state_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            yaml.safe_dump(asdict(state), fh, default_flow_style=False, sort_keys=False)
        os.replace(tmp, path)
    except OSError as exc:
        # Losing the window size is a nuisance; failing to quit is not.
        log.warning("could not save window state to %s: %s", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return path

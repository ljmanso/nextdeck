"""Freedesktop desktop-entry management.

Two entries, with different jobs:

* ``~/.config/autostart/nextdeck.desktop`` starts the app minimised to the
  tray on login. The Settings checkbox writes and removes it.
* ``~/.local/share/applications/nextdeck.desktop`` is the application menu
  entry, so nextdeck can be launched like any other installed app. A
  ``pip install`` drops a console script on PATH and nothing else, which
  leaves a GUI app invisible to every launcher on the system.

Both resolve ``Icon=nextdeck`` through the icon installed into the user's
icon theme.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

DESKTOP_FILENAME = "nextdeck.desktop"
ICON_NAME = "nextdeck"


def _xdg_dir(env_var: str, default: Path) -> Path:
    value = os.environ.get(env_var)
    return Path(value) if value else default


def autostart_dir() -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", Path.home() / ".config") / "autostart"


def autostart_path() -> Path:
    return autostart_dir() / DESKTOP_FILENAME


def icon_install_path() -> Path:
    data = _xdg_dir("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return data / "icons" / "hicolor" / "scalable" / "apps" / f"{ICON_NAME}.svg"


def applications_dir() -> Path:
    data = _xdg_dir("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return data / "applications"


def menu_entry_path() -> Path:
    return applications_dir() / DESKTOP_FILENAME


def launch_command(tray: bool = True) -> str:
    """The command a desktop entry should run.

    Prefers the installed `nextdeck` console script; falls back to the current
    interpreter with `-m nextdeck`, which keeps a `pip install --user` or a
    checkout working without a console script on PATH.

    `tray` is right for autostart, where the app should appear quietly in the
    tray. The menu entry leaves it off: clicking an icon in a launcher is a
    request to see the window.
    """
    suffix = " --tray" if tray else ""
    script = shutil.which("nextdeck")
    if script:
        return f"{script}{suffix}"
    return f"{sys.executable} -m nextdeck{suffix}"


def toggle_command(config_path: Path | None = None) -> str:
    """The command a desktop-wide hotkey should run to show or hide nextdeck.

    Always an absolute path. A keyboard shortcut defined in a desktop
    environment runs with a minimal environment, so a bare `nextdeck` is
    usually not on its PATH — a venv or a `pip install --user` script would
    simply fail to launch, silently.

    `--config` is carried through, because the socket a toggle reaches is
    keyed by config path: without it the request would go to the default
    instance, which may not be the one running.
    """
    command = launch_command(tray=False).removesuffix(" --tray")
    if config_path is not None:
        command += f" --config {Path(config_path).expanduser().resolve()}"
    return f"{command} --toggle"


def is_enabled() -> bool:
    return autostart_path().exists()


def _install_icon() -> None:
    source = Path(__file__).resolve().parent / "resources" / f"{ICON_NAME}.svg"
    if not source.exists():
        return
    target = icon_install_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    except OSError as exc:
        # A missing icon is cosmetic; never block enabling autostart over it.
        log.warning("could not install the icon to %s: %s", target, exc)


def _desktop_entry(exec_command: str, *extra: str) -> str:
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=nextdeck",
            "Comment=Nextcloud Deck tasks by due date",
            f"Exec={exec_command}",
            f"Icon={ICON_NAME}",
            "Terminal=false",
            "Categories=Utility;Office;",
            *extra,
            "",
        ]
    )


def enable() -> Path:
    """Write the autostart entry, returning its path."""
    _install_icon()

    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _desktop_entry(
            launch_command(tray=True),
            "StartupNotify=false",
            "X-GNOME-Autostart-enabled=true",
        ),
        encoding="utf-8",
    )
    return path


def disable() -> None:
    autostart_path().unlink(missing_ok=True)


# -- application menu entry ------------------------------------------------


def system_menu_entry() -> Path | None:
    """A system-wide entry, if a distro package already provides one.

    A user-level file shadows it, so writing ours over a packaged install
    would replace a correct Exec with a guess.
    """
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    for directory in raw.split(":"):
        if not directory:
            continue
        candidate = Path(directory) / "applications" / DESKTOP_FILENAME
        if candidate.exists():
            return candidate
    return None


def menu_entry_contents() -> str:
    return _desktop_entry(
        launch_command(tray=False),
        "StartupNotify=true",
        # Lets a dock match the running window to this launcher rather than
        # showing a second, unlabelled icon. Qt derives WM_CLASS from the
        # desktop file name the app sets at startup.
        f"StartupWMClass={ICON_NAME}",
    )


def install_menu_entry() -> Path | None:
    """Make nextdeck visible to the application launcher.

    Returns the path written, or None when there was nothing to do. Safe to
    call on every startup: it rewrites only when the contents would change,
    so it does not churn the file's mtime and set desktop databases rescanning
    on each launch.
    """
    system = system_menu_entry()
    if system is not None:
        log.debug("a system menu entry already exists at %s", system)
        return None

    path = menu_entry_path()
    wanted = menu_entry_contents()
    try:
        if path.exists() and path.read_text(encoding="utf-8") == wanted:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wanted, encoding="utf-8")
    except OSError as exc:
        # Not being in the menu is a nuisance, never a reason not to start.
        log.warning("could not write the menu entry to %s: %s", path, exc)
        return None

    _install_icon()
    log.info("installed the application menu entry at %s", path)
    return path


def remove_menu_entry() -> None:
    menu_entry_path().unlink(missing_ok=True)


def apply(enabled: bool) -> None:
    """Make the on-disk state match `enabled`."""
    if enabled:
        enable()
    else:
        disable()

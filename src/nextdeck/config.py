"""Configuration loading and saving.

The config file holds the Nextcloud app password in plaintext, so the file is
always written with mode 0600 and a wider mode found on load is narrowed back
down (with a warning) rather than silently tolerated.
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .model import ALL_BUCKET_KEYS

log = logging.getLogger(__name__)

SECRET_MODE = 0o600


def config_dir() -> Path:
    """Return the directory holding config.yaml, honouring XDG_CONFIG_HOME."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "nextdeck"


def config_path() -> Path:
    return config_dir() / "config.yaml"


@dataclass
class ServerConfig:
    url: str = ""
    username: str = ""
    app_password: str = ""
    verify_ssl: bool = True

    def is_complete(self) -> bool:
        return bool(self.url and self.username and self.app_password)


@dataclass
class BoardConfig:
    id: int | None = None
    default_stack_id: int | None = None
    #: Cached names, for window titles and dialog hints. Display only.
    title: str = ""
    default_stack_title: str = ""

    def is_complete(self) -> bool:
        return self.id is not None and self.default_stack_id is not None


@dataclass
class UIConfig:
    refresh_interval_minutes: int = 10
    #: Also refresh whenever the window is brought back into view. The window
    #: spends most of its life hidden in the tray, so opening it is the moment
    #: stale data is most likely and most visible.
    refresh_on_show: bool = True
    start_minimized: bool = True
    #: How long a completed task stays in the Done column.
    done_column_days: int = 14
    date_format: str = "%a %d %b"
    #: Application font size in points; 0 means "whatever the system uses".
    font_size_pt: int = 0
    #: Maximum lines a card title may wrap to before it is elided.
    card_title_lines: int = 2
    #: Collapse a column with no cards to its header, so it stops reserving
    #: width it has nothing to put in.
    compact_empty_columns: bool = True
    #: Which columns to show, by Bucket.key, e.g. ["today", "tomorrow"].
    visible_columns: list[str] = field(default_factory=lambda: list(ALL_BUCKET_KEYS))
    #: Key that shows and hides the window, as Qt spells a key sequence
    #: ("F10", "Ctrl+Shift+D"). Empty — the default — means no key at all.
    #: Choosing one in Settings offers to register it with GNOME or KDE too,
    #: which is what makes it work while the window is hidden; on its own a Qt
    #: shortcut only reaches nextdeck while nextdeck has the keyboard.
    toggle_shortcut: str = ""


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    board: BoardConfig = field(default_factory=BoardConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    autostart: bool = True

    def is_complete(self) -> bool:
        """True when the app has enough configuration to show a board."""
        return self.server.is_complete() and self.board.is_complete()


def _section(cls: type, raw: Any) -> Any:
    """Build a dataclass from a mapping, ignoring unknown keys."""
    if not isinstance(raw, dict):
        return cls()
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in raw.items() if k in known})


def normalize_url(url: str) -> str:
    """Strip trailing slashes and default to https:// when no scheme is given."""
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    return url


def load(path: Path | None = None) -> Config:
    """Load the config, returning defaults when the file does not exist."""
    path = path or config_path()
    if not path.exists():
        return Config()

    _warn_if_world_readable(path)

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        log.warning("%s is not a YAML mapping; ignoring it", path)
        return Config()

    cfg = Config(
        server=_section(ServerConfig, raw.get("server")),
        board=_section(BoardConfig, raw.get("board")),
        ui=_section(UIConfig, raw.get("ui")),
        autostart=bool(raw.get("autostart", True)),
    )
    cfg.server.url = normalize_url(cfg.server.url)
    return cfg


def save(cfg: Config, path: Path | None = None) -> Path:
    """Write the config atomically with mode 0600."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    cfg.server.url = normalize_url(cfg.server.url)
    payload = asdict(cfg)

    tmp = path.with_name(path.name + ".tmp")
    # Create the temp file restricted from the outset so the secret is never
    # momentarily visible to other users.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECRET_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(payload, fh, default_flow_style=False, sort_keys=False)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    os.chmod(path, SECRET_MODE)
    return path


def _warn_if_world_readable(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        log.warning(
            "%s was readable by other users (mode %o); tightening to 600", path, mode
        )
        os.chmod(path, SECRET_MODE)

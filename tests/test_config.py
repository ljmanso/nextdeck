import os
import stat

import pytest
import yaml

from nextdeck import config as cfgmod
from nextdeck.config import BoardConfig, Config, ServerConfig


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = cfgmod.load(tmp_path / "nope.yaml")
    assert cfg == Config()
    assert cfg.ui.refresh_interval_minutes == 10
    assert cfg.ui.done_column_days == 14
    assert cfg.autostart is True
    assert not cfg.is_complete()


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = Config(
        server=ServerConfig(
            url="https://cloud.example.org",
            username="luis",
            app_password="secret-token",
        ),
        board=BoardConfig(id=3, default_stack_id=7),
    )
    cfg.ui.refresh_interval_minutes = 5

    cfgmod.save(cfg, path)
    loaded = cfgmod.load(path)

    assert loaded == cfg
    assert loaded.is_complete()


def test_save_uses_owner_only_permissions(tmp_path):
    path = tmp_path / "config.yaml"
    cfgmod.save(Config(server=ServerConfig(app_password="secret")), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_load_tightens_loose_permissions(tmp_path, caplog):
    path = tmp_path / "config.yaml"
    cfgmod.save(Config(), path)
    os.chmod(path, 0o644)

    with caplog.at_level("WARNING"):
        cfgmod.load(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "readable by other users" in caplog.text


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "server": {"url": "https://x.example", "nonsense": 1},
                "board": {"id": 2},
                "surprise": True,
            }
        )
    )
    cfg = cfgmod.load(path)
    assert cfg.server.url == "https://x.example"
    assert cfg.board.id == 2
    assert cfg.board.default_stack_id is None


def test_non_mapping_yaml_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("- just\n- a\n- list\n")
    assert cfgmod.load(path) == Config()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://cloud.example.org/", "https://cloud.example.org"),
        ("cloud.example.org", "https://cloud.example.org"),
        ("http://localhost:8080//", "http://localhost:8080"),
        ("  https://x.example  ", "https://x.example"),
        ("", ""),
    ],
)
def test_normalize_url(raw, expected):
    assert cfgmod.normalize_url(raw) == expected


def test_config_dir_honours_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cfgmod.config_dir() == tmp_path / "nextdeck"
    assert cfgmod.config_path() == tmp_path / "nextdeck" / "config.yaml"


def test_is_complete_requires_board_and_credentials():
    cfg = Config(
        server=ServerConfig(url="https://x", username="u", app_password="p"),
    )
    assert not cfg.is_complete(), "no board pinned yet"
    cfg.board = BoardConfig(id=1, default_stack_id=2)
    assert cfg.is_complete()

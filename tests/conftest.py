"""Make `nextdeck` importable from a checkout without installing it."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


import pytest


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    """Keep window-state writes out of the real home directory.

    MainWindow saves its geometry whenever it closes, which a number of tests
    do incidentally. Without this they would scribble on the developer's own
    ~/.local/state/nextdeck/state.yaml.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

import pytest

from nextdeck import autostart


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


def test_paths_honour_xdg(xdg):
    assert autostart.autostart_path() == (
        xdg / "config" / "autostart" / "nextdeck.desktop"
    )
    assert autostart.icon_install_path() == (
        xdg / "data" / "icons" / "hicolor" / "scalable" / "apps" / "nextdeck.svg"
    )


def test_enable_writes_a_valid_desktop_entry(xdg):
    path = autostart.enable()
    body = path.read_text()

    assert body.startswith("[Desktop Entry]")
    assert "Type=Application" in body
    assert "X-GNOME-Autostart-enabled=true" in body
    assert "Icon=nextdeck" in body
    assert "--tray" in body, "autostart must start hidden"
    assert autostart.is_enabled()


def test_enable_installs_the_icon_so_the_entry_resolves(xdg):
    autostart.enable()
    installed = autostart.icon_install_path()
    assert installed.exists()
    assert installed.read_text().lstrip().startswith("<svg")


def test_disable_removes_the_entry(xdg):
    autostart.enable()
    autostart.disable()
    assert not autostart.is_enabled()


def test_disable_is_idempotent(xdg):
    autostart.disable()
    autostart.disable()
    assert not autostart.is_enabled()


def test_apply_toggles_both_ways(xdg):
    autostart.apply(True)
    assert autostart.is_enabled()
    autostart.apply(False)
    assert not autostart.is_enabled()


def test_enable_is_idempotent(xdg):
    first = autostart.enable().read_text()
    second = autostart.enable().read_text()
    assert first == second


def test_launch_command_prefers_the_console_script(xdg, monkeypatch):
    monkeypatch.setattr(autostart.shutil, "which", lambda _: "/usr/bin/nextdeck")
    assert autostart.launch_command() == "/usr/bin/nextdeck --tray"


def test_launch_command_falls_back_to_the_module(xdg, monkeypatch):
    monkeypatch.setattr(autostart.shutil, "which", lambda _: None)
    command = autostart.launch_command()
    assert command.endswith("-m nextdeck --tray")


def test_missing_icon_does_not_block_enabling(xdg, monkeypatch):
    """A cosmetic icon failure must not stop autostart from being set up."""

    def boom(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(autostart.shutil, "copyfile", boom)
    assert autostart.enable().exists()
    assert autostart.is_enabled()


# -- application menu entry ----------------------------------------------


@pytest.fixture
def no_system_entry(monkeypatch, tmp_path):
    """A system with no packaged nextdeck, which is the pip-install case."""
    empty = tmp_path / "system"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("XDG_DATA_DIRS", str(empty))
    return empty


def test_the_menu_entry_path_honours_xdg(xdg):
    assert autostart.menu_entry_path() == (
        xdg / "data" / "applications" / "nextdeck.desktop"
    )


def test_installing_writes_a_launchable_entry(xdg, no_system_entry):
    path = autostart.install_menu_entry()

    assert path is not None
    body = path.read_text()
    assert body.startswith("[Desktop Entry]")
    assert "Type=Application" in body
    assert "Icon=nextdeck" in body
    assert "Categories=" in body


def test_the_menu_entry_opens_the_window_rather_than_the_tray(xdg, no_system_entry):
    """Clicking a launcher icon is a request to see the board."""
    path = autostart.install_menu_entry()
    assert "--tray" not in path.read_text()


def test_the_menu_entry_groups_with_its_window(xdg, no_system_entry):
    """Without StartupWMClass a dock shows a second, unlabelled icon."""
    path = autostart.install_menu_entry()
    assert "StartupWMClass=nextdeck" in path.read_text()


def test_installing_also_installs_the_icon(xdg, no_system_entry):
    autostart.install_menu_entry()
    assert autostart.icon_install_path().exists()


def test_installing_twice_does_not_rewrite_the_file(xdg, no_system_entry):
    """Rewriting on every launch churns the desktop database for nothing."""
    first = autostart.install_menu_entry()
    assert first is not None
    stamp = first.stat().st_mtime_ns

    again = autostart.install_menu_entry()

    assert again is None, "nothing to do the second time"
    assert first.stat().st_mtime_ns == stamp


def test_an_outdated_entry_is_rewritten(xdg, no_system_entry):
    path = autostart.install_menu_entry()
    path.write_text("[Desktop Entry]\nExec=/gone/nextdeck\n")

    rewritten = autostart.install_menu_entry()

    assert rewritten == path
    assert "/gone/nextdeck" not in path.read_text()


def test_a_packaged_entry_is_left_alone(xdg, tmp_path, monkeypatch):
    """A user-level file shadows the system one, Exec and all."""
    system = tmp_path / "system" / "applications"
    system.mkdir(parents=True)
    (system / "nextdeck.desktop").write_text("[Desktop Entry]\nExec=/usr/bin/nextdeck\n")
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system"))

    assert autostart.install_menu_entry() is None
    assert not autostart.menu_entry_path().exists()


def test_a_packaged_entry_is_found_in_any_data_dir(xdg, tmp_path, monkeypatch):
    first = tmp_path / "a"
    second = tmp_path / "b" / "applications"
    second.mkdir(parents=True)
    (second / "nextdeck.desktop").write_text("[Desktop Entry]\n")
    monkeypatch.setenv("XDG_DATA_DIRS", f"{first}:{tmp_path / 'b'}")

    assert autostart.system_menu_entry() is not None


def test_an_unwritable_location_does_not_stop_the_app(xdg, no_system_entry):
    """Not being in the menu is a nuisance, not a reason to refuse to start."""
    target = autostart.applications_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    # A file where the directory should be, so mkdir and write both fail.
    target.write_text("not a directory")

    assert autostart.install_menu_entry() is None


def test_removing_the_entry(xdg, no_system_entry):
    path = autostart.install_menu_entry()
    assert path is not None and path.exists()

    autostart.remove_menu_entry()
    assert not path.exists()

    autostart.remove_menu_entry()  # idempotent


def test_the_menu_entry_is_independent_of_autostart(xdg, no_system_entry):
    """Turning autostart off must not take the launcher icon with it."""
    autostart.install_menu_entry()
    autostart.enable()
    autostart.disable()

    assert not autostart.is_enabled()
    assert autostart.menu_entry_path().exists()


# -- the command a desktop-wide hotkey runs -------------------------------


def test_toggle_command_is_absolute_and_asks_for_a_toggle(monkeypatch):
    """A DE keybinding runs with a minimal PATH; a bare name would not launch."""
    monkeypatch.setattr(autostart.shutil, "which", lambda name: None)
    command = autostart.toggle_command()
    assert command.startswith("/")
    assert command.endswith(" --toggle")
    assert " --tray" not in command, "a hotkey wants the window, not the tray"


def test_toggle_command_prefers_the_console_script(monkeypatch):
    monkeypatch.setattr(
        autostart.shutil, "which", lambda name: "/usr/local/bin/nextdeck"
    )
    assert autostart.toggle_command() == "/usr/local/bin/nextdeck --toggle"


def test_toggle_command_carries_an_alternate_config(monkeypatch, tmp_path):
    """The toggle socket is keyed by config path, so it has to be passed on."""
    monkeypatch.setattr(autostart.shutil, "which", lambda name: "/usr/bin/nextdeck")
    path = tmp_path / "work.yaml"
    assert autostart.toggle_command(path) == f"/usr/bin/nextdeck --config {path} --toggle"

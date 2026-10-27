"""Entry point.

    nextdeck            # open the board window
    nextdeck --tray     # start hidden in the tray (used by the autostart entry)
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from . import __version__

# `config` and `autostart` are imported where they are used, not here: a
# hotkey press runs this module only to pass a request to the instance
# already running, and `config` alone pulls in PyYAML and dataclasses for
# 56 ms it never reads a byte with.

log = logging.getLogger("nextdeck")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nextdeck", description="Nextcloud Deck tasks, grouped by due date."
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="start minimised to the tray regardless of the saved preference",
    )
    parser.add_argument(
        "--window",
        action="store_true",
        help="start with the window visible regardless of the saved preference",
    )
    parser.add_argument(
        "--config", type=Path, default=None, help="path to an alternate config.yaml"
    )
    parser.add_argument(
        "--no-menu-entry",
        action="store_true",
        help="do not install the application menu entry on startup",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="start even if another nextdeck is already running",
    )
    parser.add_argument(
        "--toggle",
        action="store_true",
        help=(
            "show the window of a running nextdeck, or hide it if it is "
            "already up; starts the app if none is running. Bind this to a "
            "key in your desktop's keyboard settings for a global hotkey."
        ),
    )
    parser.add_argument(
        "--register-hotkey",
        action="store_true",
        help=(
            "register the show/hide key from config.yaml with GNOME or KDE, "
            "then exit"
        ),
    )
    parser.add_argument(
        "--check-hotkey",
        action="store_true",
        help="report how the global show/hide shortcut is set up, and exit",
    )
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=f"nextdeck {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Before Qt is touched at all. A hotkey press runs this process only to
    # hand seven bytes to the instance already running, and importing PySide6
    # and the UI package to do that costs about three quarters of a second —
    # a key press that looks like the app hanging. The standard library can
    # write to the socket on its own, so it does, and Qt is imported below
    # only when nobody answered and there is really an app to start.
    if not args.replace and not args.register_hotkey and not args.check_hotkey:
        from . import ipc

        request = ipc.TOGGLE_REQUEST if args.toggle else ipc.SHOW_REQUEST
        if ipc.send(request, args.config):
            log.debug("handed the request to the running instance")
            return 0

    from . import config as cfgmod

    config = cfgmod.load(args.config)

    # Both report on the desktop's configuration and exit. Neither needs a
    # window, so neither pays for the UI package being imported.
    if args.check_hotkey:
        return _report_hotkey(args, config)

    if args.register_hotkey:
        return _register_hotkey(args, config)

    # Imported here so --help and --version work without a usable Qt install.
    from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

    from . import autostart
    from . import state as statemod
    from .single_instance import SHOW_REQUEST, TOGGLE_REQUEST, SingleInstance
    from .sync import SyncController
    from .ui.icon import app_icon
    from .ui.theme import apply_font_size
    from .ui.tray import TrayIcon
    from .ui.window import MainWindow

    app = QApplication(sys.argv[:1])

    # Before anything else builds state: a second launch should wake the
    # instance already in the tray rather than add another one to it.
    instance = SingleInstance(args.config)
    request = TOGGLE_REQUEST if args.toggle else SHOW_REQUEST
    if not args.replace and instance.ping_existing(request):
        log.info("nextdeck is already running; passed the request to it")
        return 0
    instance.claim()

    app.setApplicationName("nextdeck")
    app.setApplicationDisplayName("nextdeck")
    app.setDesktopFileName("nextdeck")
    app.setWindowIcon(app_icon())
    apply_font_size(config.ui.font_size_pt)
    # The window hides to the tray instead of exiting, so closing it must not
    # take the application down with it.
    app.setQuitOnLastWindowClosed(False)

    controller = SyncController(config)
    window = MainWindow(config, controller, config_path=args.config)
    instance.show_requested.connect(window.show_and_raise)
    instance.toggle_requested.connect(window.toggle_visibility)

    if not args.no_menu_entry:
        # A pip install leaves a console script and nothing a launcher can
        # see, so put nextdeck in the application menu where it belongs.
        autostart.install_menu_entry()

    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    tray: TrayIcon | None = None
    if tray_available:
        tray = TrayIcon(window)
        tray.toggle_requested.connect(window.toggle_visibility)
        tray.add_requested.connect(window.add_task)
        tray.refresh_requested.connect(controller.refresh)
        tray.settings_requested.connect(window.open_settings)
        tray.show()

    def quit_app() -> None:
        window.prepare_quit()
        instance.close()
        controller.stop()
        if tray is not None:
            tray.hide()
        app.quit()

    if tray is not None:
        tray.quit_requested.connect(quit_app)
    window.quit_requested.connect(quit_app)
    app.aboutToQuit.connect(controller.stop)

    # Ctrl+C should work when launched from a terminal. Python signal handlers
    # only run between bytecode instructions, so the idle Qt event loop needs a
    # periodic wakeup for the handler to ever fire.
    signal.signal(signal.SIGINT, lambda *_: quit_app())
    from PySide6.QtCore import QTimer

    sigint_timer = QTimer()
    sigint_timer.start(500)
    sigint_timer.timeout.connect(lambda: None)

    # Read before marking, and mark at startup rather than at exit, so a first
    # run that is killed or crashes does not keep counting as a first run.
    first_run = not statemod.load().has_run_before
    statemod.mark_has_run()

    start_hidden = _should_start_hidden(args, config, tray_available, first_run)
    if start_hidden:
        log.info("starting in the tray")
    else:
        window.show()

    if not config.is_complete():
        # Nothing to show yet, so ask for the details rather than an empty board.
        window.show_and_raise()
        if not tray_available:
            QMessageBox.information(
                window,
                "Welcome to nextdeck",
                "Enter your Nextcloud server details and pick a Deck board to "
                "get started.",
            )
        window.open_settings()
    else:
        controller.refresh()
        controller.fetch_labels()

    if not tray_available:
        log.warning(
            "no system tray is available; the window will stay open instead of "
            "hiding on close"
        )

    return app.exec()


def _report_hotkey(args, config) -> int:
    """`--check-hotkey`: say what is set up, and whether a toggle gets through.

    A key that does nothing can fail at the desktop, at the command, or at the
    app, and those need different fixes; this walks the whole chain.
    """
    from PySide6.QtCore import QCoreApplication

    from . import hotkey as hotkeymod
    from .single_instance import TOGGLE_REQUEST, SingleInstance

    for line in hotkeymod.diagnose(args.config):
        print(line)

    # Needs an application object before any Qt socket will talk — but only
    # one may exist, so never build a second over one already running.
    if QCoreApplication.instance() is None:
        QCoreApplication(sys.argv[:1])
    instance = SingleInstance(args.config)
    answered = instance.ping_existing(TOGGLE_REQUEST)
    print(
        f"{'Running instance':<22} "
        + (
            "yes — sent it a toggle, the window should have changed"
            if answered
            else "NO — nothing is listening, so a key press has nothing to reach"
        )
    )
    chosen = config.ui.toggle_shortcut
    print(f"{'Key in config.yaml':<22} {chosen or 'NONE — pick one in Settings'}")

    desk = hotkeymod.backend()
    registered = desk.installed_sequence() if desk.name else ""
    print()
    if not chosen:
        print(
            "No show/hide key is set. Pick one under Settings → Behaviour and "
            "UX → Show / hide window."
        )
    elif not desk.name:
        print(
            f"Bind this command to {chosen} in your desktop's keyboard "
            f"settings:\n\n  {hotkeymod.toggle_command(args.config)}"
        )
    elif registered == chosen:
        print(f"{chosen} is registered with {desk.name}.")
        if desk.session_has_it() is False:
            print(
                "\nBut the running session has not loaded it, which is why "
                "the key does nothing and why it is not listed in System "
                "Settings. On Plasma 6 the shortcut daemon is part of KWin, "
                "and KWin reads the shortcut file only when it starts, so:\n"
                "\n  Log out and back in, then press the key again.\n"
                "\nIf it still does nothing after that, the binding is being "
                "rejected rather than ignored — try a different key "
                "(Meta+Shift+N rather than a bare F-key), and check that "
                f'"{hotkeymod.FRIENDLY_NAME}" appears when you search your '
                "application launcher, which is how KWin finds the command to "
                "run."
            )
        elif desk.session_has_it():
            print(
                "\nThe running session is holding it, so the binding itself "
                "is fine. If the key still does nothing, something else is "
                "taking it first — try a different one, such as Meta+Shift+N "
                "rather than a bare F-key."
            )
        else:
            print(
                "\nWhether the running session has actually loaded it could "
                "not be checked here. If the key does nothing, log out and "
                "back in — on Plasma 6 the shortcut daemon is part of KWin, "
                "which reads the shortcut file only when it starts."
            )
    else:
        print(
            f"PROBLEM: {chosen} is set in config.yaml but "
            + (
                f"{desk.name} has {registered} registered instead."
                if registered
                else f"nothing is registered with {desk.name}."
            )
            + "\nThat is why the key does nothing. Fix it with:\n\n"
            "  nextdeck --register-hotkey"
        )
    return 0


def _register_hotkey(args, config) -> int:
    """`--register-hotkey`: bind the configured key, without the dialog.

    The settings dialog offers this when you pick a key, but a key can end up
    in config.yaml without ever having been offered — set before this feature
    existed, or carried over from another machine. This is the way out of that
    without hunting for the setting.
    """
    from . import hotkey as hotkeymod
    from . import state as statemod

    key = config.ui.toggle_shortcut
    if not key:
        print(
            "No show/hide key is set. Choose one in Settings, or put it in "
            "config.yaml as ui.toggle_shortcut (for example \"F9\")."
        )
        return 1

    desk = hotkeymod.backend()
    command = hotkeymod.toggle_command(args.config)
    if not desk.name:
        print(
            f"nextdeck cannot register shortcuts on this desktop "
            f"({hotkeymod.detect_desktop() or 'unknown'}).\n"
            f"Bind this command to {key} in your keyboard settings:\n\n"
            f"  {command}"
        )
        return 1

    if desk.install(key, command):
        # An explicit request outranks an earlier "no" in the dialog.
        statemod.remember_declined_hotkey("")
        print(f"Registered {key} with {desk.name}, running:\n\n  {command}")
        if not desk.went_live():
            print(
                f"\n{key} will not work until you log out and back in. "
                f"Nothing on this session can load it now: the shortcut "
                "daemon is part of KWin, and KWin only reads the file when it "
                "starts."
            )
        return 0

    print(
        f"{desk.name} would not take the shortcut. Bind this command to {key} "
        f"by hand in your keyboard settings:\n\n  {command}"
    )
    return 1


def _should_start_hidden(
    args, config, tray_available: bool, first_run: bool = False
) -> bool:
    """Decide whether to start in the tray.

    Explicit flags win over the saved preference, and hiding is refused outright
    when there is no tray to hide into — otherwise the app would look dead.
    """
    if not tray_available:
        return False
    if args.window:
        return False
    # --toggle got this far only because nothing was running to answer it.
    # Someone pressed a key asking for the board, so start showing it.
    if args.toggle:
        return False
    if args.tray:
        return True
    # The very first start shows the window even when the preference says
    # otherwise. Someone who has just installed nextdeck, or copied a config
    # in from another machine, should see it come up rather than have to
    # guess that the new icon in the tray is it.
    if first_run:
        return False
    return bool(config.ui.start_minimized) and config.is_complete()


if __name__ == "__main__":
    sys.exit(main())

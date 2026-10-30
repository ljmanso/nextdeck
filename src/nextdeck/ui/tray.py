"""System tray icon.

Deliberately quiet: a static tooltip, no badge, no counts, no notifications.
The tray is a way back to the window, not a reminder service.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from .icon import app_icon


class TrayIcon(QSystemTrayIcon):
    toggle_requested = Signal()
    add_requested = Signal()
    labels_requested = Signal()
    refresh_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(app_icon(), parent)
        self.setToolTip("nextdeck")

        menu = QMenu(parent)
        menu.addAction("Show / hide", self.toggle_requested.emit)
        menu.addSeparator()
        menu.addAction("Add task…", self.add_requested.emit)
        menu.addAction("Edit labels…", self.labels_requested.emit)
        menu.addAction("Refresh", self.refresh_requested.emit)
        menu.addAction("Settings…", self.settings_requested.emit)
        menu.addSeparator()
        menu.addAction("Quit nextdeck", self.quit_requested.emit)
        self.setContextMenu(menu)
        # Keep a reference: QSystemTrayIcon does not take ownership of the menu.
        self._menu = menu

        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.toggle_requested.emit()

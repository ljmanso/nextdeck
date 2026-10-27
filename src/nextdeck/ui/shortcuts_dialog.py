"""The keyboard cheat sheet.

Shortcuts are invisible otherwise: nothing on the board suggests that the
arrow keys do anything, let alone that Ctrl+arrow moves a due date. This is
the one place inside the app that says so.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .elided_label import WrappedLabel

#: (group title, [(keys, what it does)]). Kept as data so the README table and
#: this dialog cannot drift apart without someone noticing.
SHORTCUTS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "On the board",
        [
            ("↑  ↓", "Move between cards in a column"),
            ("←  →", "Move between columns"),
            ("Home / End", "First / last card in the column"),
            ("Enter", "Open the card for editing"),
            ("Space", "Mark complete, or reopen"),
            ("Delete", "Delete the card"),
            ("Ctrl + ← / →", "Move the due date by a day"),
            ("Shift + Ctrl + ← / →", "Move the due date by a week"),
        ],
    ),
    (
        "In the editor",
        [
            ("Ctrl + Enter", "Save and close"),
            ("Escape", "Discard changes and close"),
        ],
    ),
    (
        "Anywhere",
        [
            ("Ctrl + N", "Add a task"),
            ("Ctrl + F", "Jump to the search box"),
            ("F5", "Refresh now"),
            ("F1", "Show this list"),
            ("Ctrl + W", "Hide to the tray"),
            ("Ctrl + Q", "Quit"),
        ],
    ),
]


def toggle_note(toggle_shortcut: str = "") -> str:
    """The footnote explaining how to get a key that works from anywhere.

    Shown to everyone, because the answer differs by desktop and the two
    nextdeck can set up for you are not the two everybody runs.
    """
    lead = (
        f"{toggle_shortcut} shows and hides the window, but only while "
        "nextdeck has the keyboard"
        if toggle_shortcut
        else "No show/hide key is set — pick one in Settings"
    )
    return (
        f"{lead}. For a key that works while the window is hidden, your "
        "desktop has to own the binding. nextdeck can set that up for GNOME "
        "and KDE Plasma when you choose a key in Settings. On any other "
        "desktop, bind the command shown in Settings — it ends in "
        "“--toggle” — to a key yourself, the same way you would bind any "
        "other command."
    )


def groups(toggle_shortcut: str = "") -> list[tuple[str, list[tuple[str, str]]]]:
    """The listing, with the show/hide key filled in if one is configured.

    That key is a setting rather than a constant, so it cannot live in
    SHORTCUTS; a cheat sheet naming a key that does nothing is worse than one
    that leaves it out.
    """
    if not toggle_shortcut:
        return SHORTCUTS
    # Spaced out to match the way the fixed rows above are written.
    shown = " + ".join(part.strip() for part in toggle_shortcut.split("+"))
    return [
        (
            title,
            rows + [(shown, "Show or hide the window")]
            if title == "Anywhere"
            else rows,
        )
        for title, rows in SHORTCUTS
    ]


class ShortcutsDialog(QDialog):
    """A read-only list of every binding."""

    def __init__(self, parent: QWidget | None = None, toggle_shortcut: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.setModal(False)

        layout = QVBoxLayout(self)

        for title, rows in groups(toggle_shortcut):
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            for keys, description in rows:
                key_label = QLabel(keys)
                font = key_label.font()
                font.setBold(True)
                key_label.setFont(font)
                key_label.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                form.addRow(key_label, QLabel(description))
            layout.addWidget(box)

        self.note = WrappedLabel(toggle_note(toggle_shortcut))
        self.note.setEnabled(False)
        layout.addWidget(self.note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

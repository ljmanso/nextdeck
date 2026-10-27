"""The card title label: bounded lines, top aligned, elided overflow."""

from __future__ import annotations

import importlib

import pytest

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError as exc:  # pragma: no cover
    pytest.skip(f"PySide6 is unusable here: {exc}", allow_module_level=True)

from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nextdeck.ui.elided_label import ElidedLabel  # noqa: E402

LONG = (
    "Refactor the authentication middleware so that token refresh happens "
    "entirely off the request path and never blocks a user"
)
WIDTH = 210


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def label(text=LONG, lines=2, width=WIDTH):
    w = ElidedLabel(text, max_lines=lines)
    w.resize(width, 200)
    return w


@pytest.mark.parametrize("lines", [1, 2, 3, 4])
def test_never_uses_more_lines_than_allowed(qapp, lines):
    w = label(lines=lines)
    assert w.lineCount() <= lines
    w.deleteLater()


def test_a_long_title_is_elided_not_dropped(qapp):
    w = label(lines=2)
    assert w.lineCount() == 2
    assert w.isElided(), "overflow must be marked as elided"
    w.deleteLater()


def test_a_short_title_is_one_line_and_not_elided(qapp):
    w = label("Buy milk", lines=3)
    assert w.lineCount() == 1
    assert not w.isElided()
    w.deleteLater()


def test_height_tracks_the_lines_actually_used(qapp):
    spacing = QFontMetrics(ElidedLabel().font()).lineSpacing()
    assert label("Buy milk", lines=3).heightForWidth(WIDTH) == spacing
    assert label(LONG, lines=2).heightForWidth(WIDTH) == 2 * spacing
    assert label(LONG, lines=3).heightForWidth(WIDTH) == 3 * spacing


def test_height_is_never_zero_even_with_no_text(qapp):
    """A card with an empty title must not collapse."""
    w = label("", lines=2)
    assert w.heightForWidth(WIDTH) > 0
    assert w.lineCount() == 0
    w.deleteLater()


def test_max_lines_can_be_changed_after_construction(qapp):
    w = label(lines=1)
    assert w.lineCount() == 1
    w.setMaxLines(3)
    assert w.lineCount() == 3
    w.deleteLater()


def test_max_lines_is_at_least_one(qapp):
    assert ElidedLabel(LONG, max_lines=0).maxLines() == 1
    assert ElidedLabel(LONG, max_lines=-5).maxLines() == 1


def test_text_round_trips(qapp):
    w = label("first", lines=2)
    assert w.text() == "first"
    w.setText("second")
    assert w.text() == "second"
    w.deleteLater()


def test_narrower_width_needs_more_lines(qapp):
    wide = label(LONG, lines=6, width=400).lineCount()
    narrow = label(LONG, lines=6, width=120).lineCount()
    assert narrow > wide


def test_unbroken_text_still_wraps(qapp):
    """A pasted URL has no spaces; it must wrap rather than overflow."""
    w = label("https://example.org/" + "a" * 200, lines=2)
    assert w.lineCount() == 2
    assert w.isElided()
    w.deleteLater()


def test_height_for_width_is_declared_to_layouts(qapp):
    w = label()
    assert w.sizePolicy().hasHeightForWidth()
    w.deleteLater()


# -- geometry inside a real column ---------------------------------------
#
# The logic tests all passed while wrapped titles were visibly clipped: the
# label computed the right height but nothing gave it that height. These
# assert the rendered geometry, which is what actually broke.


def _filled_column(title, lines=2, point_size=None):
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from nextdeck.model import Bucket, Task
    from nextdeck.ui.column import ColumnWidget

    if point_size:
        font = QFont(QApplication.instance().font())
        font.setPointSizeF(point_size)
        QApplication.instance().setFont(font)

    task = Task(id=1, title=title, board_id=1, stack_id=2, stack_title="ToDo")
    column = ColumnWidget(Bucket.TODAY, title_lines=lines)
    column.resize(column.width(), 600)
    column.set_tasks([task])
    column.show()
    QApplication.instance().processEvents()
    return column


def _reset_font():
    from PySide6.QtWidgets import QApplication

    from nextdeck.ui.theme import apply_font_size

    apply_font_size(0)
    QApplication.instance().processEvents()


@pytest.mark.parametrize("lines", [1, 2, 3])
def test_card_is_tall_enough_for_every_title_line(qapp, lines):
    column = _filled_column(LONG, lines=lines)
    card = column.cards()[0]
    label = card.title_label

    assert label.height() >= label.heightForWidth(label.width()), (
        "the title label is shorter than the text it lays out, so the last "
        "line is clipped"
    )
    assert card.height() >= label.height(), "the card cannot contain its title"
    column.deleteLater()


def test_card_grows_with_the_title_line_limit(qapp):
    # Hold both columns: dropping one would delete its C++ widget.
    one_col = _filled_column(LONG, lines=1)
    three_col = _filled_column(LONG, lines=3)
    assert three_col.cards()[0].height() > one_col.cards()[0].height()


def test_a_short_title_does_not_pad_the_card_out(qapp):
    short_col = _filled_column("Standup", lines=3)
    long_col = _filled_column(LONG, lines=3)
    assert short_col.cards()[0].height() < long_col.cards()[0].height(), (
        "cards should fit their own title, not the maximum"
    )


def test_card_text_is_not_clipped_at_a_larger_font(qapp):
    """A bigger font widened nothing, so the meta line used to be cut off."""
    try:
        column = _filled_column(LONG, lines=2, point_size=16.0)
        card = column.cards()[0]
        label = card.title_label
        assert label.height() >= label.heightForWidth(label.width())
        assert card.height() >= label.height()
    finally:
        _reset_font()


def test_column_width_scales_with_the_font(qapp):
    from nextdeck.ui.column import ColumnWidget

    try:
        from nextdeck.model import Bucket

        plain = ColumnWidget(Bucket.TODAY)
        # A column is no longer a fixed width — it grows to fill the window —
        # so the font-dependent part is the floor it will not shrink below.
        default_width = plain.minimumWidth()
        big = _filled_column("x", lines=2, point_size=20.0)
        assert big.minimumWidth() > default_width
    finally:
        _reset_font()


# -- WrappedLabel ---------------------------------------------------------


def test_a_wrapped_label_keeps_room_for_every_line(qapp):
    """The settings hint used to come up short and run under the checkbox."""
    from nextdeck.ui.elided_label import WrappedLabel

    text = (
        "Create an app password under Nextcloud → Settings → Security. It is "
        "stored in plain text in config.yaml (readable only by you), so prefer "
        "an app password over your account password."
    )
    label = WrappedLabel(text)
    label.resize(300, 10)
    # Qt delivers the resize that drives the height sync at show time, not on
    # the resize() call itself, so a hidden label would read back zero.
    label.show()
    qapp.processEvents()

    assert label.heightForWidth(300) > label.fontMetrics().height(), "should wrap"
    assert label.minimumHeight() >= label.heightForWidth(300)
    label.deleteLater()


def test_a_wrapped_label_grows_when_its_text_grows(qapp):
    from nextdeck.ui.elided_label import WrappedLabel

    label = WrappedLabel("short")
    label.resize(120, 10)
    label.show()
    qapp.processEvents()
    before = label.minimumHeight()

    label.setText("a considerably longer sentence that has to wrap several times")
    assert label.minimumHeight() > before
    label.deleteLater()


def test_a_wrapped_label_in_a_nested_layout_is_not_clipped(qapp):
    """The real failure was two layouts deep, not on a bare widget."""
    from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QWidget

    from nextdeck.ui.elided_label import WrappedLabel

    host = QWidget()
    outer = QVBoxLayout(host)
    box = QGroupBox("Group")
    inner = QVBoxLayout(box)
    label = WrappedLabel(
        "A long explanatory sentence that will certainly need to wrap onto "
        "two or three lines inside a narrow dialog."
    )
    inner.addWidget(label)
    outer.addWidget(box)
    host.resize(320, 400)
    host.show()
    qapp.processEvents()

    assert label.height() >= label.heightForWidth(label.width()), (
        label.height(), label.heightForWidth(label.width())
    )
    host.deleteLater()

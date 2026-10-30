"""Labels: shown on cards, editable in the panel, clickable as a filter."""

from __future__ import annotations

import importlib
import os
from datetime import date, datetime, timedelta

import pytest
import responses

from nextdeck.api import DeckClient
from nextdeck.model import Label, Task, labels_from_board, task_from_card

try:
    importlib.import_module("PySide6.QtWidgets")
except ImportError:  # pragma: no cover - environment dependent
    QT = False
else:
    QT = True

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
requires_qt = pytest.mark.skipif(not QT, reason="PySide6 is unusable here")

BASE = "https://cloud.example/index.php/apps/deck/api/v1.0"
TODAY = date.today()


def client() -> DeckClient:
    return DeckClient("https://cloud.example", "u", "p")


def task(task_id=1, title="Task", labels=None, label_ids=None, offset=0) -> Task:
    due = None
    if offset is not None:
        due = datetime.combine(
            TODAY + timedelta(days=offset), datetime.min.time()
        ).replace(hour=9).astimezone()
    return Task(
        id=task_id,
        title=title,
        board_id=1,
        stack_id=2,
        stack_title="ToDo",
        duedate=due,
        done=None,
        labels=labels or [],
        label_ids=label_ids or [],
        raw={"id": task_id, "stackId": 2, "title": title},
    )


# -- the model ------------------------------------------------------------


def test_board_labels_are_parsed():
    labels = labels_from_board(
        {"labels": [{"id": 1, "title": "urgent", "color": "FF0000"}]}
    )
    assert labels == [Label(1, "urgent", "FF0000")]


def test_malformed_board_labels_are_skipped():
    labels = labels_from_board(
        {"labels": [{"id": 1, "title": "ok"}, {"title": "no id"}, "junk", {"id": 2}]}
    )
    assert [lbl.title for lbl in labels] == ["ok"]


def test_a_board_with_no_labels():
    assert labels_from_board({}) == []


def test_deck_colours_become_css():
    assert Label(1, "x", "31CC7C").css_color == "#31CC7C"
    assert Label(1, "x", "#31CC7C").css_color == "#31CC7C"


def test_an_unusable_colour_falls_back_to_grey():
    for bad in ("", "nope", "12345", "ZZZZZZ"):
        assert Label(1, "x", bad).css_color == "#b0b6bd"


def test_text_colour_follows_the_background():
    assert Label(1, "x", "000000").is_dark, "white text on near-black"
    assert not Label(1, "x", "FFFF00").is_dark, "black text on yellow"


# -- the API --------------------------------------------------------------


@responses.activate
def test_assigning_a_label_uses_decks_own_route():
    """Labels are not part of the card body; a card PUT would ignore them."""
    responses.add(responses.PUT, f"{BASE}/boards/1/stacks/2/cards/1/assignLabel",
                  json={}, status=200)

    client().assign_label(task(), 7)

    assert len(responses.calls) == 1
    import json

    assert json.loads(responses.calls[0].request.body) == {"labelId": 7}


@responses.activate
def test_removing_a_label_uses_its_own_route():
    responses.add(responses.PUT, f"{BASE}/boards/1/stacks/2/cards/1/removeLabel",
                  json={}, status=200)

    client().remove_label(task(), 7)

    assert len(responses.calls) == 1


@responses.activate
def test_the_board_fetch_carries_the_label_set():
    responses.add(
        responses.GET,
        f"{BASE}/boards/1",
        json={"id": 1, "title": "Work",
              "labels": [{"id": 3, "title": "urgent", "color": "FF0000"}]},
        status=200,
    )

    board = client().get_board(1)

    assert labels_from_board(board) == [Label(3, "urgent", "FF0000")]


# -- only what changed gets written ---------------------------------------


class RecordingClient:
    def __init__(self):
        self.assigned: list[int] = []
        self.removed: list[int] = []

    def update_task(self, task, **changes):
        return {"id": task.id}

    def assign_label(self, task, label_id):
        self.assigned.append(label_id)

    def remove_label(self, task, label_id):
        self.removed.append(label_id)

    def get_stacks(self, board_id):
        return []

    def close(self):
        pass


@requires_qt
def test_saving_only_touches_the_labels_that_changed(qapp):
    from nextdeck.config import BoardConfig, Config, ServerConfig
    from nextdeck.sync import SyncWorker

    worker = SyncWorker(
        Config(
            server=ServerConfig(url="https://x", username="u", app_password="p"),
            board=BoardConfig(id=1, default_stack_id=2),
        )
    )
    recorder = RecordingClient()
    worker._client = recorder

    subject = task(label_ids=[1, 2])
    worker.save_task(subject, "t", "d", None, [2, 3])

    assert recorder.assigned == [3], "only the new one"
    assert recorder.removed == [1], "only the dropped one"


@requires_qt
def test_saving_without_labels_leaves_them_alone(qapp):
    """None means 'labels were not offered', not 'remove them all'."""
    from nextdeck.config import BoardConfig, Config, ServerConfig
    from nextdeck.sync import SyncWorker

    worker = SyncWorker(
        Config(
            server=ServerConfig(url="https://x", username="u", app_password="p"),
            board=BoardConfig(id=1, default_stack_id=2),
        )
    )
    recorder = RecordingClient()
    worker._client = recorder

    worker.save_task(task(label_ids=[1, 2]), "t", "d", None, None)

    assert recorder.assigned == []
    assert recorder.removed == []


# -- the UI ---------------------------------------------------------------

if QT:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication

    from nextdeck.config import BoardConfig, Config, ServerConfig, UIConfig
    from nextdeck.model import Bucket
    from nextdeck.ui.editor import TaskEditor
    from nextdeck.ui.window import MainWindow

    class StubController(QObject):
        tasks_loaded = Signal(list)
        error = Signal(str)
        busy_changed = Signal(bool)
        mutated = Signal(str)
        save_failed = Signal(object, str)
        labels_loaded = Signal(list)
        connection_ok = Signal(list)
        stacks_loaded = Signal(list)

        def __init__(self):
            super().__init__()
            self.saves: list[tuple] = []

        def refresh(self):
            pass

        def fetch_labels(self):
            pass

        def save_task(self, task, title, description, duedate, label_ids=None):
            self.saves.append((task.id, label_ids))

        def stop(self):
            pass


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


BOARD_LABELS = [
    Label(1, "urgent", "FF0000"),
    Label(2, "migration", "31CC7C"),
    Label(3, "later", "317CCC"),
]


@pytest.fixture
def board(qapp, tmp_path):
    config = Config(
        server=ServerConfig(url="https://x.example", username="u", app_password="p"),
        board=BoardConfig(id=1, default_stack_id=2, title="Work"),
        ui=UIConfig(),
    )
    controller = StubController()
    win = MainWindow(
        config, controller, config_path=None, state_path=tmp_path / "s.yaml"
    )
    win.show()
    qapp.processEvents()
    yield win, controller
    win.deleteLater()


@requires_qt
def test_a_card_shows_its_labels_as_chips(board, qapp):
    win, controller = board
    controller.labels_loaded.emit(BOARD_LABELS)
    controller.tasks_loaded.emit([task(1, labels=["urgent"], label_ids=[1])])
    qapp.processEvents()

    card = win.columns[Bucket.TODAY].cards()[0]
    assert [chip.text() for chip in card.label_row.chips()] == ["urgent"]


@requires_qt
def test_a_chip_wears_the_boards_colour(board, qapp):
    win, controller = board
    controller.labels_loaded.emit(BOARD_LABELS)
    controller.tasks_loaded.emit([task(1, labels=["urgent"], label_ids=[1])])
    qapp.processEvents()

    chip = win.columns[Bucket.TODAY].cards()[0].label_row.chips()[0]
    assert "#FF0000" in chip.styleSheet()


@requires_qt
def test_clicking_a_chip_filters_the_board(board, qapp):
    win, controller = board
    controller.labels_loaded.emit(BOARD_LABELS)
    controller.tasks_loaded.emit(
        [
            task(1, "Renew the domain", labels=["migration"], label_ids=[2]),
            task(2, "Buy milk"),
        ]
    )
    qapp.processEvents()

    labelled = next(
        card for card in win.columns[Bucket.TODAY].cards() if card.task.id == 1
    )
    chip = labelled.label_row.chips()[0]
    chip.click()
    qapp.processEvents()

    assert win.search_edit.text() == "migration"
    assert win.columns[Bucket.TODAY].task_ids() == [1]


@requires_qt
def test_the_editor_offers_every_board_label(board, qapp):
    win, controller = board
    controller.labels_loaded.emit(BOARD_LABELS)
    subject = task(1, label_ids=[2])
    controller.tasks_loaded.emit([subject])
    qapp.processEvents()

    win._on_card_activated(subject)
    qapp.processEvents()

    toggles = win.editor.label_row.chips()
    assert [t.text() for t in toggles] == ["urgent", "migration", "later"]
    assert [t.isChecked() for t in toggles] == [False, True, False]


@requires_qt
def test_toggling_a_label_is_saved(board, qapp):
    win, controller = board
    controller.labels_loaded.emit(BOARD_LABELS)
    subject = task(1, label_ids=[2])
    controller.tasks_loaded.emit([subject])
    qapp.processEvents()
    win._on_card_activated(subject)
    qapp.processEvents()

    win.editor.label_row.chips()[0].setChecked(True)   # urgent on
    win.editor.label_row.chips()[1].setChecked(False)  # migration off
    win.editor.save_and_close()
    qapp.processEvents()

    assert controller.saves == [(1, [1])]


@requires_qt
def test_toggling_a_label_counts_as_a_change(qapp):
    """Otherwise Save would decide nothing happened and skip the write."""
    editor = TaskEditor()
    editor.set_labels(BOARD_LABELS)
    editor.open_task(task(1, label_ids=[2]))
    assert not editor.has_changes()

    editor.label_row.chips()[0].setChecked(True)

    assert editor.has_changes()
    editor.deleteLater()


@requires_qt
def test_a_label_the_board_does_not_define_is_preserved(qapp):
    """A label from elsewhere must not be stripped just by opening the card."""
    editor = TaskEditor()
    editor.set_labels(BOARD_LABELS)
    editor.open_task(task(1, label_ids=[2, 99]))

    editor.label_row.chips()[0].setChecked(True)

    assert editor._label_ids_to_save() == [1, 2, 99]
    editor.deleteLater()


@requires_qt
def test_with_no_labels_defined_the_section_is_hidden(qapp):
    editor = TaskEditor()
    editor.set_labels([])
    editor.open_task(task(1))

    assert editor.label_row.isHidden()
    assert editor._label_ids_to_save() is None, "must not strip what it cannot show"
    editor.deleteLater()


@requires_qt
def test_the_card_updates_optimistically(board, qapp):
    win, controller = board
    controller.labels_loaded.emit(BOARD_LABELS)
    subject = task(1, label_ids=[])
    controller.tasks_loaded.emit([subject])
    qapp.processEvents()
    win._on_card_activated(subject)
    qapp.processEvents()

    win.editor.label_row.chips()[0].setChecked(True)
    win.editor.save_and_close()
    qapp.processEvents()

    card = win.columns[Bucket.TODAY].cards()[0]
    assert [chip.text() for chip in card.label_row.chips()] == ["urgent"]


@requires_qt
def test_labels_are_searchable_by_name(board, qapp):
    win, controller = board
    controller.labels_loaded.emit(BOARD_LABELS)
    controller.tasks_loaded.emit(
        [task(1, "Renew", labels=["migration"], label_ids=[2]), task(2, "Buy milk")]
    )
    qapp.processEvents()

    win.search_edit.setText("migration")
    qapp.processEvents()

    assert win.columns[Bucket.TODAY].task_ids() == [1]


@requires_qt
def test_a_card_with_no_labels_has_no_chip_row(board, qapp):
    """Building an empty row per card would be pure overhead."""
    win, controller = board
    controller.labels_loaded.emit(BOARD_LABELS)
    controller.tasks_loaded.emit([task(1, "Buy milk")])
    qapp.processEvents()

    assert win.columns[Bucket.TODAY].cards()[0].label_row is None


# -- duplicate labels, as the live server allows them ---------------------


def _card_with(labels):
    return {"id": 1, "title": "Dup", "stackId": 2, "labels": labels}


def test_a_label_attached_twice_is_shown_once():
    """Deck's assignLabel does not dedup; measured on Deck 1.19.1."""
    task = task_from_card(
        _card_with(
            [
                {"id": 49, "title": "To review", "color": "317CCC"},
                {"id": 49, "title": "To review", "color": "317CCC"},
            ]
        ),
        board_id=9,
    )
    assert task.label_ids == [49]
    assert task.labels == ["To review"]


def test_distinct_labels_are_all_kept_in_order():
    task = task_from_card(
        _card_with(
            [
                {"id": 48, "title": "Finished"},
                {"id": 49, "title": "To review"},
                {"id": 48, "title": "Finished"},
            ]
        ),
        board_id=9,
    )
    assert task.label_ids == [48, 49]
    assert task.labels == ["Finished", "To review"]


def test_an_id_less_duplicate_is_also_collapsed():
    task = task_from_card(
        _card_with([{"title": "ghost"}, {"title": "ghost"}]), board_id=9
    )
    assert task.labels == ["ghost"]
    assert task.label_ids == []


# -- creating labels and labelling new tasks -------------------------------


@responses.activate
def test_creating_a_label_posts_a_bare_hex_colour():
    import json

    responses.add(responses.POST, f"{BASE}/boards/1/labels",
                  json={"id": 9, "title": "errands", "color": "31CC7C"}, status=200)

    created = client().create_label(1, "errands", "#31cc7c")

    assert json.loads(responses.calls[0].request.body) == {
        "title": "errands", "color": "31CC7C"
    }
    assert created["id"] == 9


@responses.activate
def test_a_label_can_go_on_a_card_known_only_by_ids():
    import json

    responses.add(responses.PUT, f"{BASE}/boards/1/stacks/2/cards/5/assignLabel",
                  json={}, status=200)

    client().assign_label_to_card(1, 2, 5, 7)

    assert json.loads(responses.calls[0].request.body) == {"labelId": 7}


class CreatingClient(RecordingClient):
    def __init__(self):
        super().__init__()
        self.created_labels: list[tuple] = []
        self.card_labels: list[tuple] = []

    def create_card(self, board_id, stack_id, title, description, duedate):
        return {"id": 42, "stackId": stack_id}

    def assign_label_to_card(self, board_id, stack_id, card_id, label_id):
        self.card_labels.append((board_id, stack_id, card_id, label_id))

    def create_label(self, board_id, title, color):
        self.created_labels.append((board_id, title, color))
        return {"id": 9}

    def get_board(self, board_id):
        return {"labels": [{"id": 9, "title": "errands", "color": "31CC7C"}]}


def _worker_with(recorder):
    from nextdeck.config import BoardConfig, Config, ServerConfig
    from nextdeck.sync import SyncWorker

    worker = SyncWorker(
        Config(
            server=ServerConfig(url="https://x", username="u", app_password="p"),
            board=BoardConfig(id=1, default_stack_id=2),
        )
    )
    worker._client = recorder
    return worker


@requires_qt
def test_a_new_task_gets_its_labels_after_creation(qapp):
    recorder = CreatingClient()
    worker = _worker_with(recorder)

    worker.create_task("t", "", None, [3, 1, 3])

    assert recorder.card_labels == [(1, 2, 42, 1), (1, 2, 42, 3)]


@requires_qt
def test_a_new_task_without_labels_makes_no_label_calls(qapp):
    recorder = CreatingClient()
    worker = _worker_with(recorder)

    worker.create_task("t", "", None, None)

    assert recorder.card_labels == []


@requires_qt
def test_creating_a_label_reloads_the_label_set(qapp):
    recorder = CreatingClient()
    worker = _worker_with(recorder)
    loaded: list = []
    worker.labels_loaded.connect(loaded.append)

    worker.create_label("errands", "31CC7C")

    assert recorder.created_labels == [(1, "errands", "31CC7C")]
    assert loaded == [[Label(9, "errands", "31CC7C")]]


@requires_qt
def test_the_add_dialog_offers_the_board_labels(qapp):
    from nextdeck.ui.add_dialog import AddTaskDialog

    dialog = AddTaskDialog(labels=BOARD_LABELS)
    toggles = dialog.label_row.chips()
    assert [t.text() for t in toggles] == ["urgent", "migration", "later"]

    toggles[1].setChecked(True)

    assert dialog.selected_label_ids() == [2]
    dialog.deleteLater()


@requires_qt
def test_the_add_dialog_hides_labels_on_a_board_without_any(qapp):
    from nextdeck.ui.add_dialog import AddTaskDialog

    dialog = AddTaskDialog(labels=[])
    assert not dialog.label_row.isVisible()
    assert dialog.selected_label_ids() == []
    dialog.deleteLater()


@requires_qt
def test_the_label_dialog_refuses_a_duplicate_title(qapp):
    from nextdeck.ui.label_dialog import NewLabelDialog

    dialog = NewLabelDialog(BOARD_LABELS)
    assert not dialog._ok_button.isEnabled(), "an empty title is not a label"

    dialog.title_edit.setText("Urgent")
    assert not dialog._ok_button.isEnabled(), "matched regardless of case"

    dialog.title_edit.setText("  errands  ")
    dialog.set_color("#7c31cc")
    assert dialog._ok_button.isEnabled()
    assert dialog.values() == ("errands", "7C31CC")
    dialog.deleteLater()


@requires_qt
def test_creating_a_label_keeps_edits_in_the_open_editor(qapp):
    editor = TaskEditor()
    editor.set_labels(BOARD_LABELS)
    editor.open_task(task(1, label_ids=[2]))
    editor.label_row.chips()[0].setChecked(True)

    editor.set_labels(BOARD_LABELS + [Label(9, "errands", "31CC7C")])

    assert [c.isChecked() for c in editor.label_row.chips()] == [
        True, True, False, False
    ]
    editor.deleteLater()

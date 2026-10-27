"""End-to-end over a mocked Deck server: HTTP -> Task -> columns.

This is the test that catches field-name mismatches between the layers, using a
response shaped like the one in Deck's own API documentation.
"""

from datetime import date, datetime, timedelta, timezone

import json

import responses

from nextdeck.api import DeckClient
from nextdeck.model import Bucket, group_by_bucket, tasks_from_stacks

BASE = "https://cloud.example.org/index.php/apps/deck/api/v1.0"
TODAY = date(2026, 10, 26)


def iso(offset_days: int, hour: int = 9) -> str:
    stamp = datetime.combine(
        TODAY + timedelta(days=offset_days), datetime.min.time()
    ).replace(hour=hour, tzinfo=timezone.utc)
    return stamp.isoformat()


def deck_card(card_id: int, title: str, **overrides) -> dict:
    """A card shaped like the one in Deck's API docs."""
    card = {
        "title": title,
        "description": None,
        "stackId": 6,
        "type": "plain",
        "lastModified": 1541528026,
        "createdAt": 1541528026,
        "labels": None,
        "assignedUsers": None,
        "attachments": None,
        "attachmentCount": None,
        "owner": "admin",
        "order": 999,
        "archived": False,
        "done": None,
        "duedate": None,
        "deletedAt": 0,
        "commentsUnread": 0,
        "id": card_id,
        "overdue": 0,
    }
    card.update(overrides)
    return card


STACKS = [
    {
        "title": "ToDo",
        "boardId": 2,
        "deletedAt": 0,
        "lastModified": 1541426139,
        "order": 999,
        "id": 6,
        "cards": [
            deck_card(1, "Overdue thing", duedate=iso(-3), overdue=3),
            deck_card(2, "Due today", duedate=iso(0), overdue=2),
            deck_card(3, "Due tomorrow", duedate=iso(1), overdue=1),
            deck_card(4, "This week", duedate=iso(4)),
            deck_card(5, "This month", duedate=iso(20)),
            deck_card(6, "Someday", duedate=iso(90)),
            deck_card(7, "Undated", duedate=None),
            # The server flags this as overdue even though it is done; the
            # client must disagree. See deck#8001.
            deck_card(8, "Finished late", duedate=iso(-9), done=iso(-1), overdue=3),
            deck_card(9, "Archived", archived=True),
            deck_card(10, "Trashed", deletedAt=1541528026),
            deck_card(
                11,
                "Tagged",
                duedate=iso(2),
                labels=[{"id": 1, "title": "urgent"}],
            ),
        ],
    },
    {
        "title": "Doing",
        "boardId": 2,
        "deletedAt": 0,
        "order": 1000,
        "id": 7,
        "cards": [deck_card(12, "In progress", stackId=7, duedate=iso(0))],
    },
]


@responses.activate
def test_a_refresh_produces_the_expected_columns():
    responses.add(responses.GET, f"{BASE}/boards/2/stacks", json=STACKS)
    client = DeckClient("https://cloud.example.org", "luis", "token")

    tasks = tasks_from_stacks(client.get_stacks(2), board_id=2)
    grouped = group_by_bucket(tasks, TODAY, done_column_days=14)

    def ids(bucket):
        return sorted(t.id for t in grouped[bucket])

    assert ids(Bucket.OVERDUE) == [1]
    assert ids(Bucket.TODAY) == [2, 12]
    assert ids(Bucket.TOMORROW) == [3]
    assert ids(Bucket.NEXT_7) == [4, 11]
    assert ids(Bucket.NEXT_30) == [5]
    assert ids(Bucket.LATER) == [6]
    assert ids(Bucket.NO_DUE_DATE) == [7]
    assert ids(Bucket.DONE) == [8]


@responses.activate
def test_the_servers_overdue_flag_is_not_trusted():
    """Card 8 is done but the server says overdue: 3."""
    responses.add(responses.GET, f"{BASE}/boards/2/stacks", json=STACKS)
    client = DeckClient("https://cloud.example.org", "luis", "token")

    tasks = tasks_from_stacks(client.get_stacks(2), 2)
    card8 = next(t for t in tasks if t.id == 8)

    assert card8.raw["overdue"] == 3, "the server really does claim overdue"
    grouped = group_by_bucket(tasks, TODAY)
    assert card8 in grouped[Bucket.DONE]
    assert card8 not in grouped[Bucket.OVERDUE]


@responses.activate
def test_archived_and_trashed_cards_never_reach_a_column():
    responses.add(responses.GET, f"{BASE}/boards/2/stacks", json=STACKS)
    client = DeckClient("https://cloud.example.org", "luis", "token")

    tasks = tasks_from_stacks(client.get_stacks(2), 2)
    visible = {t.id for t in tasks}

    assert 9 not in visible, "archived"
    assert 10 not in visible, "soft-deleted"


@responses.activate
def test_stack_titles_and_labels_survive_the_trip():
    responses.add(responses.GET, f"{BASE}/boards/2/stacks", json=STACKS)
    client = DeckClient("https://cloud.example.org", "luis", "token")

    tasks = {t.id: t for t in tasks_from_stacks(client.get_stacks(2), 2)}

    assert tasks[1].stack_title == "ToDo"
    assert tasks[12].stack_title == "Doing"
    assert tasks[11].labels == ["urgent"]


@responses.activate
def test_completing_a_task_then_refreshing_moves_it_to_done():
    """The full write-then-read cycle a click actually performs."""
    responses.add(responses.GET, f"{BASE}/boards/2/stacks", json=STACKS)
    client = DeckClient("https://cloud.example.org", "luis", "token")
    task = next(t for t in tasks_from_stacks(client.get_stacks(2), 2) if t.id == 2)

    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/2", json={"id": 2})
    client.set_done(task, True)

    sent = json.loads(responses.calls[1].request.body)
    assert sent["done"] is not None
    assert sent["title"] == "Due today", "the update must not blank the title"
    assert sent["duedate"] == iso(0), "nor the due date"

    # Now the server reports it done; it should bucket into DONE.
    done_stacks = json.loads(json.dumps(STACKS))
    for card in done_stacks[0]["cards"]:
        if card["id"] == 2:
            card["done"] = sent["done"]
    responses.add(responses.GET, f"{BASE}/boards/2/stacks", json=done_stacks)

    grouped = group_by_bucket(tasks_from_stacks(client.get_stacks(2), 2), TODAY)
    assert 2 in {t.id for t in grouped[Bucket.DONE]}
    assert 2 not in {t.id for t in grouped[Bucket.TODAY]}

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs

import pytest
import requests
import responses

from nextdeck.api import CARD_WRITABLE_FIELDS, DeckClient, DeckError
from nextdeck.model import Task

BASE = "https://cloud.example.org/index.php/apps/deck/api/v1.0"


@pytest.fixture
def client():
    return DeckClient("https://cloud.example.org/", "luis", "token")


def make_task(**kw) -> Task:
    raw = {
        "id": 10,
        "title": "Original",
        "description": "body",
        "stackId": 6,
        "type": "plain",
        "owner": "luis",
        "order": 3,
        "duedate": "2026-10-26T09:00:00+00:00",
        "done": None,
        "archived": False,
    }
    raw.update(kw.pop("raw", {}))
    defaults = dict(
        id=10, title="Original", board_id=2, stack_id=6, description="body", raw=raw
    )
    defaults.update(kw)
    return Task(**defaults)


# -- plumbing -------------------------------------------------------------


def test_base_url_is_built_from_server_root(client):
    assert client.base == BASE


def test_required_headers_are_sent(client):
    assert client.session.headers["OCS-APIRequest"] == "true"
    assert client.session.auth == ("luis", "token")


@responses.activate
def test_verify_ssl_can_be_disabled():
    c = DeckClient("https://x.example", "u", "p", verify_ssl=False)
    assert c.session.verify is False


# -- reads ----------------------------------------------------------------


@responses.activate
def test_get_boards_filters_archived_and_deleted(client):
    responses.add(
        responses.GET,
        f"{BASE}/boards",
        json=[
            {"id": 1, "title": "Live"},
            {"id": 2, "title": "Old", "archived": True},
            {"id": 3, "title": "Gone", "deletedAt": 1541528026},
        ],
    )
    assert [b["id"] for b in client.get_boards()] == [1]


@responses.activate
def test_get_stacks_returns_stacks_with_embedded_cards(client):
    responses.add(
        responses.GET,
        f"{BASE}/boards/2/stacks",
        json=[{"id": 4, "title": "ToDo", "cards": [{"id": 1, "title": "a"}]}],
    )
    stacks = client.get_stacks(2)
    assert stacks[0]["cards"][0]["title"] == "a"
    assert len(responses.calls) == 1, "a refresh must be a single request"


@responses.activate
def test_empty_body_is_not_a_json_error(client):
    responses.add(responses.GET, f"{BASE}/boards", body="", status=200)
    assert client.get_boards() == []


# -- create ---------------------------------------------------------------


@responses.activate
def test_create_card_posts_title_and_duedate(client):
    responses.add(
        responses.POST,
        f"{BASE}/boards/2/stacks/6/cards",
        json={"id": 11, "title": "New", "duedate": "2026-11-01T12:00:00+00:00"},
    )
    due = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    client.create_card(2, 6, "New", description="desc", duedate=due)

    sent = json.loads(responses.calls[0].request.body)
    assert sent["title"] == "New"
    assert sent["description"] == "desc"
    assert sent["type"] == "plain"
    assert sent["duedate"].startswith("2026-11-01T12:00")


@responses.activate
def test_create_card_omits_duedate_when_not_given(client):
    responses.add(responses.POST, f"{BASE}/boards/2/stacks/6/cards", json={"id": 11})
    client.create_card(2, 6, "New")
    assert "duedate" not in json.loads(responses.calls[0].request.body)


@responses.activate
def test_create_card_sets_duedate_via_update_when_server_ignores_it(client):
    """Some Deck versions drop duedate on create; the client repairs that."""
    responses.add(
        responses.POST,
        f"{BASE}/boards/2/stacks/6/cards",
        json={"id": 11, "title": "New", "stackId": 6, "duedate": None},
    )
    responses.add(
        responses.PUT,
        f"{BASE}/boards/2/stacks/6/cards/11",
        json={"id": 11, "duedate": "2026-11-01T12:00:00+00:00"},
    )
    card = client.create_card(
        2, 6, "New", duedate=datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    )
    assert card["duedate"] is not None
    assert len(responses.calls) == 2


# -- update: the full-field strategy -------------------------------------


@responses.activate
def test_update_sends_every_writable_field(client):
    """Omitted fields get wiped by Deck's form fallback, so never omit any."""
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})
    client.update_task(make_task(), title="Renamed")

    sent = json.loads(responses.calls[0].request.body)
    assert set(sent) == set(CARD_WRITABLE_FIELDS)
    assert sent["title"] == "Renamed"
    assert sent["description"] == "body", "untouched fields are preserved, not dropped"
    assert sent["order"] == 3
    assert sent["duedate"] == "2026-10-26T09:00:00+00:00"


@responses.activate
def test_update_datetimes_are_serialised(client):
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})
    client.update_task(make_task(), duedate=datetime(2026, 12, 1, 8, tzinfo=timezone.utc))
    sent = json.loads(responses.calls[0].request.body)
    assert sent["duedate"].startswith("2026-12-01T08:00")


@responses.activate
def test_update_falls_back_to_form_encoding_on_4xx(client):
    """Deck 1.19 rejects JSON bodies on this route; retry form-encoded once."""
    responses.add(
        responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", status=400, body="nope"
    )
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})

    client.update_task(make_task(), title="Renamed")

    assert len(responses.calls) == 2
    retry = responses.calls[1].request
    assert retry.headers["Content-Type"] == "application/x-www-form-urlencoded"
    form = parse_qs(retry.body)
    assert form["title"] == ["Renamed"]
    assert form["description"] == ["body"], "fallback still sends the full field set"
    # PHP casts the non-empty string "false" to boolean true, which silently
    # archived the card on Deck 1.19.1. Booleans must go out as "0"/"1".
    assert form["archived"] == ["0"], "a falsey boolean must not read as true"


@responses.activate
def test_update_does_not_retry_on_server_error(client):
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", status=500)
    with pytest.raises(DeckError):
        client.update_task(make_task(), title="x")
    assert len(responses.calls) == 1, "5xx is not the JSON-body quirk"


@responses.activate
def test_form_fallback_encodes_null_as_empty_string(client):
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", status=400)
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})
    client.set_done(make_task(), False)

    form = parse_qs(responses.calls[1].request.body, keep_blank_values=True)
    assert form["done"] == [""]


# -- done / duedate / delete ---------------------------------------------


@responses.activate
def test_set_done_true_sends_a_timestamp(client):
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})
    client.set_done(make_task(), True)
    assert json.loads(responses.calls[0].request.body)["done"] is not None


@responses.activate
def test_set_done_false_clears_the_timestamp(client):
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})
    task = make_task(raw={"done": "2026-10-20T10:00:00+00:00"})
    client.set_done(task, False)
    assert json.loads(responses.calls[0].request.body)["done"] is None


@responses.activate
def test_set_duedate_none_clears_it(client):
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})
    client.set_duedate(make_task(), None)
    assert json.loads(responses.calls[0].request.body)["duedate"] is None


@responses.activate
def test_delete_card_targets_the_right_path(client):
    responses.add(
        responses.DELETE, f"{BASE}/boards/2/stacks/6/cards/10", status=200, body=""
    )
    client.delete_card(make_task())
    assert responses.calls[0].request.method == "DELETE"


# -- error reporting ------------------------------------------------------


@responses.activate
def test_401_mentions_the_app_password(client):
    responses.add(responses.GET, f"{BASE}/boards", status=401, body="denied")
    with pytest.raises(DeckError) as exc:
        client.get_boards()
    assert "app password" in str(exc.value)
    assert exc.value.status == 401


@responses.activate
def test_403_blames_access_not_the_password(client):
    """Deck answers 403 for a board that was deleted or unshared, so telling
    the user their password is wrong would send them to the wrong place."""
    responses.add(responses.GET, f"{BASE}/boards/9/stacks", status=403, body="")
    with pytest.raises(DeckError) as exc:
        client.get_stacks(9)
    message = str(exc.value)
    assert "deleted or unshared" in message
    assert "app password" not in message


@responses.activate
def test_not_found_is_explained(client):
    responses.add(responses.GET, f"{BASE}/boards", status=404, body="")
    with pytest.raises(DeckError, match="Not found"):
        client.get_boards()


@responses.activate
def test_server_error_is_explained(client):
    responses.add(responses.GET, f"{BASE}/boards", status=503, body="")
    with pytest.raises(DeckError, match="internal error"):
        client.get_boards()


@responses.activate
def test_html_response_suggests_a_wrong_url(client):
    responses.add(responses.GET, f"{BASE}/boards", body="<html>login</html>", status=200)
    with pytest.raises(DeckError, match="not JSON"):
        client.get_boards()


@responses.activate
def test_connection_error_is_friendly(client):
    responses.add(
        responses.GET, f"{BASE}/boards", body=requests.exceptions.ConnectionError()
    )
    with pytest.raises(DeckError, match="Could not reach the server"):
        client.get_boards()


@responses.activate
def test_timeout_is_friendly(client):
    responses.add(responses.GET, f"{BASE}/boards", body=requests.exceptions.Timeout())
    with pytest.raises(DeckError, match="too long to respond"):
        client.get_boards()


@responses.activate
def test_tls_error_is_friendly(client):
    responses.add(
        responses.GET, f"{BASE}/boards", body=requests.exceptions.SSLError("bad cert")
    )
    with pytest.raises(DeckError, match="TLS verification failed"):
        client.get_boards()


def test_deck_error_includes_status_in_str():
    assert "HTTP 404" in str(DeckError("missing", 404))
    assert str(DeckError("plain")) == "plain"


# -- owner handling: the Deck 1.19.1 400 (regression) --------------------


@responses.activate
def test_owner_object_is_reduced_to_a_uid_string(client):
    """Deck returns `owner` as a user object but rejects one on write (400)."""
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})
    task = make_task(
        raw={
            "owner": {
                "primaryKey": "prueba",
                "uid": "prueba",
                "displayname": "ddd",
                "type": 0,
            }
        }
    )
    client.update_task(task, title="Renamed")

    assert json.loads(responses.calls[0].request.body)["owner"] == "prueba"


@responses.activate
def test_owner_falls_back_to_primary_key(client):
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})
    client.update_task(make_task(raw={"owner": {"primaryKey": "someone"}}), title="x")
    assert json.loads(responses.calls[0].request.body)["owner"] == "someone"


@responses.activate
def test_missing_owner_falls_back_to_the_authenticated_user(client):
    """Omitting `owner` is also a 400, so it must never be sent empty."""
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})
    client.update_task(make_task(raw={"owner": None}), title="x")
    assert json.loads(responses.calls[0].request.body)["owner"] == "luis"


@responses.activate
def test_owner_string_is_passed_through(client):
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})
    client.update_task(make_task(), title="x")
    assert json.loads(responses.calls[0].request.body)["owner"] == "luis"


@responses.activate
def test_null_description_is_sent_as_empty_string(client):
    """A null description is rejected by Deck; empty means "no details"."""
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})
    client.update_task(make_task(raw={"description": None}), title="x")
    assert json.loads(responses.calls[0].request.body)["description"] == ""


@responses.activate
def test_archived_true_still_round_trips_as_true(client):
    """The "0"/"1" fix must not make archiving impossible."""
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", status=400)
    responses.add(responses.PUT, f"{BASE}/boards/2/stacks/6/cards/10", json={"id": 10})
    client.update_task(make_task(), archived=True)
    assert parse_qs(responses.calls[1].request.body)["archived"] == ["1"]

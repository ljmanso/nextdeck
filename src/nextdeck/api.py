"""Nextcloud Deck REST client.

No Qt imports here — this layer is pure HTTP so it can be tested headlessly.

Two Deck quirks shape this module:

1. Card updates are sent as the *complete* writable field set, read-modify-write
   style. The documented PUT treats every field as optional, but some Deck
   releases reject JSON bodies outright, and the form-encoded fallback wipes
   fields that are omitted. Always sending every field makes that fallback safe.
2. There is no documented done/undone route, so completion is just a card
   update setting `done` to a timestamp or null. `set_done` isolates that.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from .model import Task, format_datetime

log = logging.getLogger(__name__)

API_PATH = "/index.php/apps/deck/api/v1.0"
DEFAULT_TIMEOUT = 20

#: Every writable field on a card. Updates send all of these, always.
CARD_WRITABLE_FIELDS = (
    "title",
    "description",
    "type",
    "owner",
    "order",
    "duedate",
    "done",
    "archived",
)


class DeckError(Exception):
    """An API call failed."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status

    def __str__(self) -> str:
        if self.status:
            return f"{self.message} (HTTP {self.status})"
        return self.message


def _friendly_error(status: int, body: str) -> str:
    if status == 401:
        return (
            "Nextcloud rejected the credentials — check the username and app "
            "password in Settings"
        )
    if status == 403:
        # Deck checks permission before existence, so a board that was deleted,
        # unshared, or never existed answers 403 rather than 404. Blaming the
        # credentials here would send the user to the wrong place entirely.
        return (
            "Access denied — the board may have been deleted or unshared, or "
            "this account may not have permission to use it"
        )
    if status == 404:
        return "Not found on the server — the board or card may have been deleted"
    if status >= 500:
        return "The Nextcloud server reported an internal error"
    detail = body.strip()
    if len(detail) > 200:
        detail = detail[:200] + "…"
    return detail or "The request failed"


class DeckClient:
    """Synchronous Deck API client. Call from a worker thread, not the GUI."""

    def __init__(
        self,
        url: str,
        username: str,
        app_password: str,
        verify_ssl: bool = True,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.base = url.rstrip("/") + API_PATH
        self.username = username
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (username, app_password)
        self.session.verify = verify_ssl
        self.session.headers.update(
            {
                "OCS-APIRequest": "true",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    # -- plumbing ---------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = self.base + path
        try:
            response = self.session.request(
                method, url, timeout=self.timeout, **kwargs
            )
        except requests.exceptions.SSLError as exc:
            raise DeckError(f"TLS verification failed: {exc}") from exc
        except requests.exceptions.ConnectionError as exc:
            raise DeckError(
                "Could not reach the server — check the URL and your connection"
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise DeckError("The server took too long to respond") from exc
        except requests.RequestException as exc:
            raise DeckError(str(exc)) from exc

        if not response.ok:
            raise DeckError(
                _friendly_error(response.status_code, response.text),
                response.status_code,
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise DeckError(
                "The server returned a response that was not JSON — is the URL "
                "the root of a Nextcloud instance with Deck installed?"
            ) from exc

    def close(self) -> None:
        self.session.close()

    # -- reads ------------------------------------------------------------

    def get_boards(self) -> list[dict[str, Any]]:
        """All boards visible to the user. Used by the settings picker."""
        boards = self._request("GET", "/boards") or []
        return [b for b in boards if not b.get("archived") and not b.get("deletedAt")]

    def get_board(self, board_id: int) -> dict[str, Any]:
        """One board, including the label set it defines.

        Labels belong to the board, not the card, so the editor needs this to
        know what it may offer. Fetched when the board changes rather than on
        every refresh: the set rarely moves, and the refresh is deliberately
        one request.
        """
        return self._request("GET", f"/boards/{board_id}") or {}

    def get_stacks(self, board_id: int) -> list[dict[str, Any]]:
        """Stacks for a board, each with its cards embedded.

        This single request is the whole board refresh.
        """
        return self._request("GET", f"/boards/{board_id}/stacks") or []

    # -- writes -----------------------------------------------------------

    def create_card(
        self,
        board_id: int,
        stack_id: int,
        title: str,
        description: str | None = None,
        duedate: datetime | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "type": "plain", "order": 999}
        if description:
            payload["description"] = description
        if duedate is not None:
            payload["duedate"] = format_datetime(duedate)
        card = self._request(
            "POST", f"/boards/{board_id}/stacks/{stack_id}/cards", json=payload
        )
        # Older Deck versions ignore duedate on create; set it explicitly.
        if duedate is not None and card and not card.get("duedate"):
            card = self.update_card_raw(board_id, card, duedate=format_datetime(duedate))
        return card or {}

    def update_card_raw(
        self, board_id: int, card: dict[str, Any], **changes: Any
    ) -> dict[str, Any]:
        """Update a card, sending every writable field.

        `card` must be the server's own card dict; `changes` are merged on top.
        Falls back to a form-encoded body once if the JSON body is rejected,
        which is safe only because the body is complete.
        """
        card_id = int(card["id"])
        stack_id = int(changes.get("stackId", card.get("stackId")) or 0)

        body: dict[str, Any] = {}
        for name in CARD_WRITABLE_FIELDS:
            body[name] = changes[name] if name in changes else card.get(name)
        body.setdefault("type", "plain")
        if body.get("type") is None:
            body["type"] = "plain"
        if body.get("order") is None:
            body["order"] = 0
        if body.get("title") is None:
            body["title"] = ""
        body["archived"] = bool(body.get("archived"))
        # Deck *returns* `owner` as a user object but *requires* it on write as
        # a uid string, and rejects the whole update with HTTP 400 if it is an
        # object or missing. Verified against Deck 1.19.1.
        body["owner"] = self._owner_uid(body.get("owner"))
        # A null description is rejected too; empty string means "no details".
        if body.get("description") is None:
            body["description"] = ""

        path = f"/boards/{board_id}/stacks/{stack_id}/cards/{card_id}"
        try:
            updated = self._request("PUT", path, json=body)
        except DeckError as exc:
            if exc.status is None or not 400 <= exc.status < 500:
                raise
            log.info(
                "JSON card update rejected (HTTP %s); retrying form-encoded",
                exc.status,
            )
            updated = self._request(
                "PUT",
                path,
                data=_form_encode(body),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        return updated or {}

    def _owner_uid(self, owner: Any) -> str:
        """Reduce whatever Deck gave us for `owner` to a uid string."""
        if isinstance(owner, dict):
            owner = owner.get("uid") or owner.get("primaryKey") or ""
        if isinstance(owner, str) and owner:
            return owner
        # Nothing usable on the card: the authenticated user is the best guess,
        # and an empty owner is refused by the server.
        return self.username

    def update_task(self, task: Task, **changes: Any) -> dict[str, Any]:
        """Update the card behind a Task. Datetimes are serialised for you."""
        prepared = {
            key: format_datetime(value) if isinstance(value, datetime) else value
            for key, value in changes.items()
        }
        return self.update_card_raw(task.board_id, task.raw or {"id": task.id, "stackId": task.stack_id}, **prepared)

    def set_done(self, task: Task, done: bool) -> dict[str, Any]:
        """Mark a task complete or incomplete."""
        stamp = datetime.now(timezone.utc) if done else None
        return self.update_task(task, done=format_datetime(stamp))

    def set_duedate(self, task: Task, duedate: datetime | None) -> dict[str, Any]:
        return self.update_task(task, duedate=format_datetime(duedate))

    def assign_label(self, task: Task, label_id: int) -> None:
        """Put a label on a card.

        Labels are not part of the card body: Deck has its own routes for
        them, so a card PUT carrying `labels` would be ignored.
        """
        self._label_request("assignLabel", task, label_id)

    def remove_label(self, task: Task, label_id: int) -> None:
        self._label_request("removeLabel", task, label_id)

    def _label_request(self, action: str, task: Task, label_id: int) -> None:
        self._card_label_request(
            action, task.board_id, task.stack_id, task.id, label_id
        )

    def assign_label_to_card(
        self, board_id: int, stack_id: int, card_id: int, label_id: int
    ) -> None:
        """Put a label on a card known only by its ids, such as one just created."""
        self._card_label_request("assignLabel", board_id, stack_id, card_id, label_id)

    def _card_label_request(
        self, action: str, board_id: int, stack_id: int, card_id: int, label_id: int
    ) -> None:
        self._request(
            "PUT",
            f"/boards/{board_id}/stacks/{stack_id}/cards/{card_id}/{action}",
            json={"labelId": int(label_id)},
        )

    def create_label(self, board_id: int, title: str, color: str) -> dict[str, Any]:
        """Define a new label on a board.

        Deck wants the colour as a bare RRGGBB string, with no leading hash.
        """
        return (
            self._request(
                "POST",
                f"/boards/{board_id}/labels",
                json={"title": title, "color": color.lstrip("#").upper()},
            )
            or {}
        )

    def delete_card(self, task: Task) -> None:
        """Delete a card. Deck soft-deletes into the board's trash."""
        self._request(
            "DELETE",
            f"/boards/{task.board_id}/stacks/{task.stack_id}/cards/{task.id}",
        )


def _form_encode(body: dict[str, Any]) -> dict[str, Any]:
    """Coerce a JSON body into something form encoding round-trips sanely.

    Booleans go out as "0"/"1", never "false"/"true": PHP casts the non-empty
    string "false" to boolean *true*, so sending "false" for `archived` was
    observed to silently archive the card on Deck 1.19.1.

    `requests` would also drop None values, so those become empty strings.
    """
    encoded: dict[str, Any] = {}
    for key, value in body.items():
        if value is None:
            encoded[key] = ""
        elif isinstance(value, bool):
            encoded[key] = "1" if value else "0"
        else:
            encoded[key] = value
    return encoded

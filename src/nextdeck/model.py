"""Task model and due-date bucketing.

Deliberately free of Qt imports: this module is the part of the app that can be
tested without a display, and it holds the rules that matter most.

Bucketing is computed here rather than taken from the Deck API's own `overdue`
field, which ignores the `done` field and therefore reports completed cards as
overdue (https://github.com/nextcloud/deck/issues/8001).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable


class Bucket(Enum):
    """A due-date column. Declaration order is display order, left to right."""

    OVERDUE = "Overdue"
    TODAY = "Today"
    TOMORROW = "Tomorrow"
    NEXT_7 = "Next 7 days"
    NEXT_30 = "Next 30 days"
    LATER = "Later"
    NO_DUE_DATE = "No due date"
    DONE = "Done"

    @property
    def title(self) -> str:
        return self.value

    @property
    def key(self) -> str:
        """Stable identifier for config files, unlike the display title."""
        return self.name.lower()


#: Columns in display order.
BUCKET_ORDER: tuple[Bucket, ...] = tuple(Bucket)

#: Every column key, in display order.
ALL_BUCKET_KEYS: tuple[str, ...] = tuple(b.key for b in BUCKET_ORDER)

_BY_KEY = {b.key: b for b in BUCKET_ORDER}


def bucket_from_key(key: str) -> "Bucket | None":
    return _BY_KEY.get(str(key).strip().lower())


def buckets_from_keys(keys: Iterable[str]) -> tuple[Bucket, ...]:
    """Resolve config keys to buckets, always in display order.

    Unknown keys are ignored, and an empty result falls back to every column:
    a board with no columns at all would look broken rather than configured.
    """
    wanted = {k for k in (bucket_from_key(k) for k in keys or ()) if k is not None}
    ordered = tuple(b for b in BUCKET_ORDER if b in wanted)
    return ordered or BUCKET_ORDER


def parse_datetime(value: Any) -> datetime | None:
    """Parse a Deck timestamp into an aware datetime, or None.

    Deck sends ISO-8601 strings for `duedate`/`done`, but uses 0 and epoch
    integers in a few places (notably `deletedAt`), so handle both.
    """
    if value is None or value == "" or value == 0:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    if not isinstance(value, str):
        return None

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # A naive timestamp is interpreted as local time, which is the least
    # surprising reading of a date typed by the user.
    return parsed if parsed.tzinfo else parsed.astimezone()


def format_datetime(value: datetime | None) -> str | None:
    """Serialise a datetime for the Deck API as ISO-8601 with an offset."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.astimezone()
    return value.isoformat()


@dataclass
class Task:
    """A Deck card, flattened to what the board view needs."""

    id: int
    title: str
    board_id: int
    stack_id: int
    stack_title: str = ""
    description: str = ""
    duedate: datetime | None = None
    done: datetime | None = None
    archived: bool = False
    order: int = 0
    labels: list[str] = field(default_factory=list)
    #: The ids of those labels that carry one. Editing works by id; the
    #: titles above are what gets displayed and searched, and a label with no
    #: id still shows even though it cannot be toggled.
    label_ids: list[int] = field(default_factory=list)
    #: The original card dict, kept so updates can send every writable field.
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_done(self) -> bool:
        return self.done is not None

    @property
    def due_date_local(self) -> date | None:
        """The due date as a local calendar date, which is how buckets split."""
        if self.duedate is None:
            return None
        return self.duedate.astimezone().date()

    def days_until_due(self, today: date | None = None) -> int | None:
        due = self.due_date_local
        if due is None:
            return None
        return (due - (today or date.today())).days


def task_from_card(card: dict[str, Any], board_id: int, stack_title: str = "") -> Task:
    """Build a Task from a Deck card dict.

    Labels are de-duplicated. Deck's assignLabel route happily attaches the
    same label to a card twice, with no error and no server-side dedup
    (measured on Deck 1.19.1), and a card carrying one twice would otherwise
    render the same chip twice.
    """
    labels: list[str] = []
    label_ids: list[int] = []
    for label in card.get("labels") or []:
        if not isinstance(label, dict):
            continue
        title = label.get("title") or ""
        if not title:
            continue
        raw_id = label.get("id")
        try:
            label_id = None if raw_id is None else int(raw_id)
        except (TypeError, ValueError):
            label_id = None
        # An id-less label still displays and is still searchable; it just
        # cannot be toggled, so dedup those on the title alone.
        if label_id is None:
            if title not in labels:
                labels.append(title)
            continue
        if label_id in label_ids:
            continue
        label_ids.append(label_id)
        if title not in labels:
            labels.append(title)
    return Task(
        id=int(card["id"]),
        title=card.get("title") or "",
        board_id=board_id,
        stack_id=int(card.get("stackId") or 0),
        stack_title=stack_title,
        description=card.get("description") or "",
        duedate=parse_datetime(card.get("duedate")),
        done=parse_datetime(card.get("done")),
        archived=bool(card.get("archived")),
        order=int(card.get("order") or 0),
        labels=labels,
        label_ids=label_ids,
        raw=dict(card),
    )


def tasks_from_stacks(stacks: Iterable[dict[str, Any]], board_id: int) -> list[Task]:
    """Flatten the stacks-with-embedded-cards response into Tasks.

    Archived and soft-deleted cards are dropped: they are not actionable and
    Deck keeps deleted cards around in the board trash for a while.
    """
    tasks: list[Task] = []
    for stack in stacks:
        stack_title = stack.get("title") or ""
        for card in stack.get("cards") or []:
            if card.get("archived"):
                continue
            if parse_datetime(card.get("deletedAt")) is not None:
                continue
            tasks.append(task_from_card(card, board_id, stack_title))
    return tasks


def bucket_of(
    task: Task, today: date | None = None, done_column_days: int = 14
) -> Bucket | None:
    """Return the column a task belongs in, or None if it should be hidden.

    Completed tasks always land in DONE regardless of their due date, and drop
    out of the view entirely once they are older than `done_column_days`.
    """
    today = today or date.today()

    if task.is_done:
        completed = task.done.astimezone().date()  # type: ignore[union-attr]
        if done_column_days >= 0 and (today - completed).days > done_column_days:
            return None
        return Bucket.DONE

    due = task.due_date_local
    if due is None:
        return Bucket.NO_DUE_DATE

    delta = (due - today).days
    if delta < 0:
        return Bucket.OVERDUE
    if delta == 0:
        return Bucket.TODAY
    if delta == 1:
        return Bucket.TOMORROW
    if delta <= 7:
        return Bucket.NEXT_7
    if delta <= 30:
        return Bucket.NEXT_30
    return Bucket.LATER


def group_by_bucket(
    tasks: Iterable[Task], today: date | None = None, done_column_days: int = 14
) -> dict[Bucket, list[Task]]:
    """Group tasks into every bucket, sorted for display.

    All buckets are present as keys, including empty ones, so the view can rely
    on a stable set of columns.
    """
    today = today or date.today()
    grouped: dict[Bucket, list[Task]] = {bucket: [] for bucket in BUCKET_ORDER}

    for task in tasks:
        bucket = bucket_of(task, today, done_column_days)
        if bucket is not None:
            grouped[bucket].append(task)

    for bucket, items in grouped.items():
        if bucket is Bucket.DONE:
            # Most recently completed first.
            items.sort(key=lambda t: (t.done or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        elif bucket is Bucket.NO_DUE_DATE:
            items.sort(key=lambda t: (t.order, t.title.lower()))
        else:
            items.sort(key=lambda t: (t.duedate or datetime.max.replace(tzinfo=timezone.utc), t.title.lower()))
    return grouped


#: Where a card lands when dropped on each column. Values are day offsets from
#: today; None clears the due date. Buckets absent from this map are handled
#: specially by the view (OVERDUE rejects drops, DONE marks complete).
DROP_TARGET_OFFSETS: dict[Bucket, int | None] = {
    Bucket.TODAY: 0,
    Bucket.TOMORROW: 1,
    Bucket.NEXT_7: 7,
    Bucket.NEXT_30: 30,
    Bucket.LATER: 60,
    Bucket.NO_DUE_DATE: None,
}


@dataclass(frozen=True)
class Label:
    """One of the board's labels. Deck defines these per board, not per card."""

    id: int
    title: str
    #: Deck stores colours as a bare RRGGBB hex string, with no leading hash.
    color: str = ""

    @property
    def css_color(self) -> str:
        """The colour as CSS, or a neutral grey when Deck gave us nothing."""
        value = (self.color or "").lstrip("#").strip()
        if len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value):
            return f"#{value}"
        return "#b0b6bd"

    @property
    def is_dark(self) -> bool:
        """Whether label text should be white rather than black on this."""
        value = self.css_color.lstrip("#")
        red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
        # Rec. 601 luma: close enough for picking between two text colours.
        return (0.299 * red + 0.587 * green + 0.114 * blue) < 140


def labels_from_board(board: dict[str, Any]) -> list[Label]:
    """The label set a board offers, in the order Deck lists them."""
    found: list[Label] = []
    for raw in board.get("labels") or []:
        if not isinstance(raw, dict):
            continue
        raw_id = raw.get("id")
        title = raw.get("title") or ""
        if raw_id is None or not title:
            continue
        try:
            found.append(Label(int(raw_id), title, str(raw.get("color") or "")))
        except (TypeError, ValueError):
            continue
    return found


def task_matches(task: Task, query: str) -> bool:
    """Whether `task` should survive the board's search filter.

    Every whitespace-separated term has to appear somewhere on the card, so
    adding a word narrows the result rather than widening it. Matching covers
    the title, the details, the labels and the stack name: searching by a word
    half-remembered from a description is usually the point, and the card's
    tooltip already shows the details, so a match is never invisible.
    """
    terms = query.lower().split()
    if not terms:
        return True

    haystack = " ".join(
        [task.title, task.description, task.stack_title, *task.labels]
    ).lower()
    return all(term in haystack for term in terms)


#: How far Ctrl+arrow and Shift+Ctrl+arrow move a due date.
DAY_STEP = 1
WEEK_STEP = 7


def shift_duedate(
    task: Task, days: int, today: date | None = None
) -> datetime:
    """Nudge `task`'s due date by `days`, for the keyboard reschedule.

    Unlike dropping on a column, this is a relative move, so it keeps the
    card's own time of day and lands on any date — including one in the past.
    Stepping back a day at a time is deliberate and precise, which dropping
    onto Overdue is not; that is why one is allowed and the other refused.

    A card with no due date is treated as due today, so the same two keys are
    also the quickest way to give something a date.
    """
    today = today or date.today()
    existing = task.duedate.astimezone() if task.duedate else None

    if existing is None:
        base = datetime.combine(today, datetime.min.time()).replace(hour=12)
        return (base + timedelta(days=days)).astimezone()
    return existing + timedelta(days=days)


def reschedule_datetime(
    task: Task, bucket: Bucket, today: date | None = None
) -> datetime | None:
    """The new duedate for `task` when dropped on `bucket`.

    The card's existing time of day is preserved so that dragging a 09:00
    meeting to tomorrow keeps it at 09:00; cards with no due date get noon,
    which reads as "sometime that day" rather than midnight.
    """
    if bucket not in DROP_TARGET_OFFSETS:
        raise ValueError(f"{bucket} is not a reschedule target")

    offset = DROP_TARGET_OFFSETS[bucket]
    if offset is None:
        return None

    today = today or date.today()
    target_day = today + timedelta(days=offset)
    existing = task.duedate.astimezone() if task.duedate else None
    time_of_day = existing.timetz() if existing else None

    if time_of_day is None:
        return datetime.combine(target_day, datetime.min.time()).replace(
            hour=12
        ).astimezone()
    return datetime.combine(target_day, time_of_day)

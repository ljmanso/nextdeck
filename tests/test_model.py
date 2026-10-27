from datetime import date, datetime, timedelta, timezone

import pytest

from nextdeck.model import (
    BUCKET_ORDER,
    Bucket,
    Task,
    bucket_of,
    group_by_bucket,
    parse_datetime,
    reschedule_datetime,
    tasks_from_stacks,
)

TODAY = date(2026, 10, 26)


def make_task(duedate=None, done=None, **kw) -> Task:
    """A task whose due date is `duedate` days from TODAY (or None)."""
    due = None
    if duedate is not None:
        due = datetime.combine(TODAY + timedelta(days=duedate), datetime.min.time())
        due = due.replace(hour=9, tzinfo=timezone.utc)
    kw.setdefault("id", 1)
    kw.setdefault("title", "t")
    kw.setdefault("board_id", 1)
    kw.setdefault("stack_id", 1)
    return Task(duedate=due, done=done, **kw)


# -- bucket boundaries ----------------------------------------------------


@pytest.mark.parametrize(
    "offset,expected",
    [
        (-100, Bucket.OVERDUE),
        (-1, Bucket.OVERDUE),
        (0, Bucket.TODAY),
        (1, Bucket.TOMORROW),
        (2, Bucket.NEXT_7),
        (7, Bucket.NEXT_7),
        (8, Bucket.NEXT_30),
        (30, Bucket.NEXT_30),
        (31, Bucket.LATER),
        (365, Bucket.LATER),
    ],
)
def test_bucket_boundaries(offset, expected):
    assert bucket_of(make_task(duedate=offset), TODAY) is expected


def test_task_without_due_date_has_its_own_bucket():
    assert bucket_of(make_task(), TODAY) is Bucket.NO_DUE_DATE


def test_done_beats_overdue():
    """Guards against Deck's own `overdue` field, which ignores `done`."""
    task = make_task(duedate=-5, done=datetime(2026, 10, 25, tzinfo=timezone.utc))
    assert bucket_of(task, TODAY) is Bucket.DONE


def test_done_beats_every_date_bucket():
    done = datetime(2026, 10, 26, tzinfo=timezone.utc)
    for offset in (-1, 0, 1, 5, 20, 90, None):
        task = make_task(duedate=offset, done=done)
        assert bucket_of(task, TODAY) is Bucket.DONE


def test_done_cards_drop_out_after_the_retention_window():
    recent = make_task(done=datetime(2026, 10, 20, tzinfo=timezone.utc))
    old = make_task(done=datetime(2026, 9, 1, tzinfo=timezone.utc))

    assert bucket_of(recent, TODAY, done_column_days=14) is Bucket.DONE
    assert bucket_of(old, TODAY, done_column_days=14) is None, "hidden, not shown"


def test_negative_retention_keeps_all_done_cards():
    old = make_task(done=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert bucket_of(old, TODAY, done_column_days=-1) is Bucket.DONE


def test_bucket_order_is_left_to_right():
    assert BUCKET_ORDER == (
        Bucket.OVERDUE,
        Bucket.TODAY,
        Bucket.TOMORROW,
        Bucket.NEXT_7,
        Bucket.NEXT_30,
        Bucket.LATER,
        Bucket.NO_DUE_DATE,
        Bucket.DONE,
    )


# -- timestamp parsing ----------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [None, "", 0],
)
def test_parse_datetime_empty_values(raw):
    assert parse_datetime(raw) is None


def test_parse_datetime_iso_with_offset():
    got = parse_datetime("2019-12-24T19:29:30+00:00")
    assert got == datetime(2019, 12, 24, 19, 29, 30, tzinfo=timezone.utc)


def test_parse_datetime_handles_trailing_z():
    got = parse_datetime("2019-12-24T19:29:30Z")
    assert got == datetime(2019, 12, 24, 19, 29, 30, tzinfo=timezone.utc)


def test_parse_datetime_naive_string_becomes_aware():
    got = parse_datetime("2019-12-24T19:29:30")
    assert got is not None and got.tzinfo is not None


def test_parse_datetime_rejects_garbage():
    assert parse_datetime("not a date") is None
    assert parse_datetime(["list"]) is None


def test_parse_datetime_accepts_epoch_int():
    assert parse_datetime(1541528026) == datetime.fromtimestamp(
        1541528026, tz=timezone.utc
    )


# -- flattening the API response -----------------------------------------


def card(**kw):
    base = {
        "id": 1,
        "title": "Card",
        "stackId": 4,
        "type": "plain",
        "order": 0,
        "archived": False,
        "done": None,
        "duedate": None,
        "deletedAt": 0,
        "labels": None,
    }
    base.update(kw)
    return base


def test_tasks_from_stacks_flattens_and_keeps_stack_title():
    stacks = [
        {"id": 4, "title": "ToDo", "cards": [card(id=1), card(id=2)]},
        {"id": 5, "title": "Doing", "cards": [card(id=3, stackId=5)]},
    ]
    tasks = tasks_from_stacks(stacks, board_id=2)

    assert [t.id for t in tasks] == [1, 2, 3]
    assert tasks[0].stack_title == "ToDo"
    assert tasks[2].stack_title == "Doing"
    assert all(t.board_id == 2 for t in tasks)


def test_tasks_from_stacks_drops_archived_and_deleted():
    stacks = [
        {
            "id": 4,
            "title": "ToDo",
            "cards": [
                card(id=1),
                card(id=2, archived=True),
                card(id=3, deletedAt=1541528026),
            ],
        }
    ]
    assert [t.id for t in tasks_from_stacks(stacks, 2)] == [1]


def test_tasks_from_stacks_tolerates_stack_without_cards():
    assert tasks_from_stacks([{"id": 4, "title": "Empty"}], 2) == []


def test_labels_are_flattened_to_titles():
    stacks = [
        {
            "id": 4,
            "title": "ToDo",
            "cards": [card(labels=[{"title": "urgent"}, {"title": ""}, {"id": 9}])],
        }
    ]
    assert tasks_from_stacks(stacks, 2)[0].labels == ["urgent"]


def test_label_ids_are_kept_for_editing():
    stacks = [
        {
            "id": 4,
            "title": "ToDo",
            "cards": [card(labels=[{"id": 3, "title": "urgent"},
                                   {"id": 7, "title": "later"}])],
        }
    ]
    task = tasks_from_stacks(stacks, 2)[0]
    assert task.labels == ["urgent", "later"]
    assert task.label_ids == [3, 7]


def test_a_label_without_an_id_still_shows():
    """It cannot be toggled, but hiding it would lose information."""
    stacks = [
        {"id": 4, "title": "ToDo", "cards": [card(labels=[{"title": "urgent"}])]}
    ]
    task = tasks_from_stacks(stacks, 2)[0]
    assert task.labels == ["urgent"]
    assert task.label_ids == []


def test_raw_card_is_retained_for_full_field_updates():
    original = card(id=7, title="Keep me", description="body")
    task = tasks_from_stacks([{"id": 4, "title": "s", "cards": [original]}], 2)[0]
    assert task.raw["description"] == "body"


# -- grouping -------------------------------------------------------------


def test_group_by_bucket_includes_every_column_even_when_empty():
    grouped = group_by_bucket([make_task(duedate=0)], TODAY)
    assert set(grouped) == set(BUCKET_ORDER)
    assert grouped[Bucket.TODAY] and not grouped[Bucket.LATER]


def test_group_sorts_dated_columns_by_due_date():
    tasks = [
        make_task(id=1, duedate=7),
        make_task(id=2, duedate=2),
        make_task(id=3, duedate=4),
    ]
    assert [t.id for t in group_by_bucket(tasks, TODAY)[Bucket.NEXT_7]] == [2, 3, 1]


def test_group_sorts_done_most_recent_first():
    tasks = [
        make_task(id=1, done=datetime(2026, 10, 20, tzinfo=timezone.utc)),
        make_task(id=2, done=datetime(2026, 10, 25, tzinfo=timezone.utc)),
    ]
    assert [t.id for t in group_by_bucket(tasks, TODAY)[Bucket.DONE]] == [2, 1]


def test_group_excludes_stale_done_tasks_entirely():
    tasks = [make_task(id=1, done=datetime(2020, 1, 1, tzinfo=timezone.utc))]
    grouped = group_by_bucket(tasks, TODAY, done_column_days=14)
    assert all(not items for items in grouped.values())


# -- rescheduling on drop -------------------------------------------------


@pytest.mark.parametrize(
    "bucket,expected_offset",
    [
        (Bucket.TODAY, 0),
        (Bucket.TOMORROW, 1),
        (Bucket.NEXT_7, 7),
        (Bucket.NEXT_30, 30),
        (Bucket.LATER, 60),
    ],
)
def test_reschedule_lands_on_the_expected_day(bucket, expected_offset):
    got = reschedule_datetime(make_task(duedate=3), bucket, TODAY)
    assert got is not None
    assert got.date() == TODAY + timedelta(days=expected_offset)


def test_reschedule_to_no_due_date_clears_it():
    assert reschedule_datetime(make_task(duedate=3), Bucket.NO_DUE_DATE, TODAY) is None


def test_reschedule_preserves_existing_time_of_day():
    task = make_task()
    task.duedate = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
    got = reschedule_datetime(task, Bucket.TOMORROW, TODAY)
    assert got is not None
    local = task.duedate.astimezone()
    assert (got.hour, got.minute) == (local.hour, local.minute)


def test_reschedule_defaults_to_noon_when_there_was_no_due_date():
    got = reschedule_datetime(make_task(), Bucket.TODAY, TODAY)
    assert got is not None and got.hour == 12


def test_reschedule_rejects_non_target_buckets():
    for bucket in (Bucket.OVERDUE, Bucket.DONE):
        with pytest.raises(ValueError):
            reschedule_datetime(make_task(duedate=1), bucket, TODAY)


def test_rescheduled_task_lands_in_the_bucket_it_was_dropped_on():
    """The round trip that actually matters: drop target -> same column back."""
    for bucket in (Bucket.TODAY, Bucket.TOMORROW, Bucket.NEXT_7, Bucket.NEXT_30):
        task = make_task(duedate=-3)
        task.duedate = reschedule_datetime(task, bucket, TODAY)
        assert bucket_of(task, TODAY) is bucket, bucket


def test_days_until_due():
    assert make_task(duedate=5).days_until_due(TODAY) == 5
    assert make_task().days_until_due(TODAY) is None

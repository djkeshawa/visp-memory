"""Tests for UTC time utilities and timestamp consistency in memory dynamics."""

from datetime import datetime, timedelta, timezone

from llm_memory.core.clock import age_days, parse_utc, utc_now, utc_now_iso
from llm_memory.core.storage import LocalStorage


def test_utc_now_is_aware_utc():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_utc_now_iso_round_trips():
    parsed = parse_utc(utc_now_iso())
    assert parsed is not None
    assert abs((utc_now() - parsed).total_seconds()) < 5


def test_parse_utc_handles_all_stored_formats():
    # Aware ISO with offset (new Python-side writes)
    aware = parse_utc("2026-06-21T10:00:00+00:00")
    # 'Z' suffix
    zulu = parse_utc("2026-06-21T10:00:00Z")
    # Naive ISO (legacy Python writes) — treated as UTC
    naive = parse_utc("2026-06-21T10:00:00")
    # SQLite CURRENT_TIMESTAMP space-separated format (UTC)
    sqlite = parse_utc("2026-06-21 10:00:00")
    # Non-UTC offset normalizes to UTC
    offset = parse_utc("2026-06-21T15:30:00+05:30")

    assert aware == zulu == naive == sqlite == offset
    assert aware.tzinfo is not None


def test_parse_utc_rejects_garbage():
    assert parse_utc(None) is None
    assert parse_utc("") is None
    assert parse_utc("not a timestamp") is None


def test_parse_utc_accepts_datetime_objects():
    naive = datetime(2026, 6, 21, 10, 0, 0)
    aware = datetime(2026, 6, 21, 10, 0, 0, tzinfo=timezone.utc)
    assert parse_utc(naive) == aware
    assert parse_utc(aware) == aware


def test_age_days_basic():
    hour_ago = (utc_now() - timedelta(hours=24)).isoformat()
    age = age_days(hour_ago)
    assert age is not None
    assert 0.99 <= age <= 1.01
    assert age_days(None) is None
    # Future timestamps clamp to zero rather than going negative.
    assert age_days((utc_now() + timedelta(days=2)).isoformat()) == 0.0


def test_fresh_memory_timestamps_are_utc_regardless_of_local_tz(tmp_path):
    """The core regression: stored timestamps must parse as ~now in UTC.

    On a non-UTC machine, a local-time write would show an apparent age equal to
    the UTC offset (e.g. 5.5h at UTC+5:30), skewing decay and recency ranking.
    """
    storage = LocalStorage(tmp_path)
    memory_id = storage.store_memory("utc regression check", auto_link=False)
    row = storage._get_memory_row(memory_id, track_access=False)

    for field in ("created_at", "accessed_at"):
        parsed = parse_utc(row[field])
        assert parsed is not None, field
        apparent_age_seconds = abs((utc_now() - parsed).total_seconds())
        # Anything over 5 minutes means a timezone leaked into the write path.
        assert apparent_age_seconds < 300, (
            f"{field} skewed by {apparent_age_seconds:.0f}s — local time leaked in"
        )


def test_intent_timestamps_are_utc(tmp_path):
    storage = LocalStorage(tmp_path)
    intent_id = storage.set_intent("GOAL: verify utc intents", priority=1)
    intents = storage.get_active_intents()
    row = next(item for item in intents if item["id"] == intent_id)
    parsed = parse_utc(row["created_at"])
    assert parsed is not None
    assert abs((utc_now() - parsed).total_seconds()) < 300

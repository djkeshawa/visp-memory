"""UTC time utilities shared by all memory timestamp writes and comparisons.

Memory dynamics (decay, recency ranking, staleness reports) measure elapsed time
between stored timestamps and "now". Historically the codebase mixed SQLite's
``CURRENT_TIMESTAMP`` (UTC) with Python's ``datetime.now()`` (local time), which
skews every age calculation by the machine's UTC offset. All timestamp writes and
age comparisons should go through this module so both sides of the arithmetic are
in UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with offset."""
    return utc_now().isoformat()


def parse_utc(value: Any) -> Optional[datetime]:
    """Parse a stored timestamp into a timezone-aware UTC datetime.

    Accepts datetimes (naive treated as UTC — matching SQLite CURRENT_TIMESTAMP
    and this module's writes), ISO-8601 strings with 'Z'/offset/naive, and the
    space-separated SQLite format. Returns None for missing or unparseable input
    so callers can skip undatable rows instead of crashing.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_days(value: Any, now: Optional[datetime] = None) -> Optional[float]:
    """Return the non-negative age in days of a stored timestamp, or None."""
    parsed = parse_utc(value)
    if parsed is None:
        return None
    reference = now or utc_now()
    return max(0.0, (reference - parsed).total_seconds() / 86400)

from datetime import datetime
from types import SimpleNamespace

import pytest

from visp_memory.core.reporting import MemoryIntelligenceReporter


@pytest.mark.parametrize("as_datetime", [False, True])
def test_report_compares_timestamp_instants_in_utc(as_datetime):
    created_at = "2026-01-01T00:00:00+05:30"
    if as_datetime:
        created_at = datetime.fromisoformat(created_at)
    storage = SimpleNamespace(
        list_memories=lambda **kwargs: [{
            "id": "clock", "created_at": "2026-01-30T18:30:00+00:00", "content": "Clock"
        }],
        get_all_relationships=lambda **kwargs: [],
        get_active_intents=lambda **kwargs: [{
            "id": "intent", "created_at": created_at, "description": "Long-lived work"
        }],
    )

    report = MemoryIntelligenceReporter(storage).generate(repo_id="report-audit")

    assert report["sections"]["stale_intents"]["items"][0]["facts"]["age_days"] == 30


def test_report_reference_time_uses_the_latest_instant_across_offsets():
    storage = SimpleNamespace(
        list_memories=lambda **kwargs: [
            {"id": "local", "created_at": "2026-01-01T01:00:00+05:30", "content": "Local"},
            {"id": "utc", "created_at": "2026-01-01T00:00:00Z", "content": "UTC"},
        ],
        get_all_relationships=lambda **kwargs: [],
        get_active_intents=lambda **kwargs: [],
    )

    report = MemoryIntelligenceReporter(storage).generate(repo_id="report-audit")

    assert report["as_of"] == "2026-01-01T00:00:00"

"""Tests for provenance tiers and trust decay."""

from datetime import timedelta

import pytest

from visp_memory.core.clock import utc_now
from visp_memory.core.injection import InjectionPolicy, select_for_injection
from visp_memory.core.trust import (
    CHANNEL_POLICIES,
    Provenance,
    WriteChannel,
    assess,
    channel_policy,
    filter_injectable,
    provenance_of,
    provenance_tag,
    with_channel_provenance,
    with_provenance,
)


def _memory(tier=None, days_old=0, access_count=0, **extra):
    created = utc_now() - timedelta(days=days_old)
    memory = {
        "id": "m1",
        "content": "Some memory",
        "created_at": created.isoformat(),
        "access_count": access_count,
        "tags": [provenance_tag(tier)] if tier else [],
        "relevance_score": 0.9,
    }
    memory.update(extra)
    return memory


class TestProvenanceTagRoundTrip:
    def test_enum_members_round_trip(self):
        """Regression: for a (str, Enum) subclass, str(member) is "Provenance.DERIVED",
        not "derived". Round-tripping through str() silently produced UNKNOWN for every
        memory, disabling the whole trust layer without raising anything."""
        for tier in Provenance:
            assert provenance_of({"tags": [provenance_tag(tier)]}) is tier

    def test_string_values_round_trip(self):
        assert provenance_of({"tags": [provenance_tag("external")]}) is Provenance.EXTERNAL

    def test_unlabelled_memory_is_unknown_not_trusted(self):
        assert provenance_of({"tags": []}) is Provenance.UNKNOWN

    def test_unrecognised_label_is_unknown(self):
        assert provenance_of({"tags": ["provenance:whatever"]}) is Provenance.UNKNOWN

    def test_source_field_is_a_fallback(self):
        assert provenance_of({"tags": [], "source": "authored"}) is Provenance.AUTHORED

    def test_with_provenance_replaces_rather_than_appends(self):
        tags = with_provenance(["git", provenance_tag(Provenance.ASSISTED)], Provenance.DERIVED)
        assert tags.count(provenance_tag(Provenance.DERIVED)) == 1
        assert provenance_tag(Provenance.ASSISTED) not in tags
        assert "git" in tags

    def test_every_declared_tier_is_reachable_from_a_channel_or_unknown_input(self):
        reachable = {policy.provenance for policy in CHANNEL_POLICIES.values()}
        reachable.add(provenance_of({"tags": []}))

        assert reachable == set(Provenance)

    def test_channel_policy_mapping_is_read_only(self):
        with pytest.raises(TypeError):
            CHANNEL_POLICIES[WriteChannel.CLI] = CHANNEL_POLICIES[WriteChannel.LIBRARY]

    def test_channel_policy_replaces_self_claimed_provenance(self):
        tags = with_channel_provenance(
            ["user-tag", provenance_tag(Provenance.AUTHORED)],
            WriteChannel.MCP,
        )

        assert provenance_of({"tags": tags}) is Provenance.ASSISTED
        assert provenance_tag(Provenance.AUTHORED) not in tags
        assert "user-tag" in tags

    def test_unknown_channel_is_refused(self):
        with pytest.raises(ValueError, match="Unknown memory write channel"):
            channel_policy("self-declared-channel")


class TestQuarantine:
    def test_external_memory_is_never_injectable(self):
        assessment = assess(_memory(Provenance.EXTERNAL))
        assert assessment.quarantined
        assert not assessment.injectable
        assert "outside this repository" in assessment.reason

    def test_external_quarantine_ignores_recency_and_importance(self):
        """The attack is a fresh, confident, highly relevant record."""
        assessment = assess(_memory(Provenance.EXTERNAL, days_old=0, access_count=500))
        assert not assessment.injectable

    def test_quarantined_memory_is_excluded_from_injection(self):
        poisoned = _memory(Provenance.EXTERNAL, relevance_score=0.99)
        result = select_for_injection(
            [poisoned], task="deploy the release to production", corpus_size=50
        )
        assert result.abstained
        assert result.dropped_quarantined == 1

    @pytest.mark.parametrize(
        "memory",
        [
            {"tags": []},
            {"tags": ["provenance:not-a-tier"]},
            {"tags": None, "source": "not-a-tier"},
        ],
    )
    def test_missing_or_malformed_provenance_is_quarantined(self, memory):
        assessment = assess({"id": "unknown", "content": "data", **memory})

        assert assessment.tier is Provenance.UNKNOWN
        assert assessment.quarantined is True
        assert assessment.injectable is False
        assert "missing or malformed" in assessment.reason


class TestTrustDecay:
    def test_fresh_authored_memory_is_fully_trusted(self):
        assert assess(_memory(Provenance.AUTHORED)).trust > 0.95

    def test_trust_falls_with_age(self):
        fresh = assess(_memory(Provenance.ASSISTED, days_old=0)).trust
        old = assess(_memory(Provenance.ASSISTED, days_old=240)).trust
        assert old < fresh

    def test_stale_memory_drops_out_of_injection(self):
        assessment = assess(_memory(Provenance.ASSISTED, days_old=400))
        assert not assessment.injectable
        assert "below" in assessment.reason

    def test_authored_memory_outlives_assisted(self):
        """A human-written convention should not expire as fast as a model's summary."""
        authored = assess(_memory(Provenance.AUTHORED, days_old=200)).trust
        assisted = assess(_memory(Provenance.ASSISTED, days_old=200)).trust
        assert authored > assisted

    def test_use_reinforces_but_cannot_resurrect(self):
        """Reinforcement must not let a very stale memory live forever."""
        unused = assess(_memory(Provenance.ASSISTED, days_old=90, access_count=0)).trust
        used = assess(_memory(Provenance.ASSISTED, days_old=90, access_count=50)).trust
        assert used > unused

        ancient = assess(_memory(Provenance.ASSISTED, days_old=2000, access_count=50))
        assert not ancient.injectable

    def test_missing_timestamp_does_not_bypass_unknown_quarantine(self):
        assessment = assess({"id": "x", "content": "y", "tags": []})
        assert assessment.trust == 0.0
        assert assessment.quarantined is True


class TestFiltering:
    def test_splits_and_annotates(self):
        allowed, rejected = filter_injectable(
            [
                _memory(Provenance.AUTHORED, id="ok"),
                _memory(Provenance.EXTERNAL, id="bad"),
            ]
        )
        assert [m["id"] for m in allowed] == ["ok"]
        assert allowed[0]["provenance"] == "authored"
        assert allowed[0]["trust"] > 0
        assert [a.tier for _m, a in rejected] == [Provenance.EXTERNAL]

    def test_trust_enforcement_can_be_disabled(self):
        """Explicit recall must still be able to see everything."""
        poisoned = _memory(Provenance.EXTERNAL, relevance_score=0.99)
        result = select_for_injection(
            [poisoned],
            task="deploy the release to production",
            corpus_size=50,
            policy=InjectionPolicy(enforce_trust=False),
        )
        assert not result.abstained

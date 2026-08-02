"""Provenance tiers and trust decay for injected memory.

A memory store that an assistant reads from is an attack surface, and a fairly effective
one. MemoryGraft (arXiv:2512.16962) planted 10 poisoned records among 100 benign ones --
about 9% of the store -- and those records captured **47.9% of all retrievals**, because
retrieval optimises for similarity and a crafted record can be maximally similar to the
queries it targets. Delivery was a benign-looking README containing example "successful
experiences" that the agent then wrote to its own memory.

The same paper proposes provenance attestation and safety-aware reranking as defences,
and notes that neither was empirically validated. A related line of work on evolving
memory finds the second failure mode is quieter: stale entries that were true once keep
being retrieved and override newer corrections.

Both failures share a cause: retrieval treats every stored record as equally trustworthy.
This module supplies the missing dimension.

**Provenance tiers.** Where a memory came from bounds how far it can be trusted:

- ``authored``  -- a human wrote it (CLI, dashboard). Highest trust, slowest decay.
- ``derived``   -- mined from the repository itself: commits and test results.
  The repository is the ground truth being described, so this is nearly as good.
- ``assisted``  -- written by an assistant during a session. Usually right, but it is a
  model's summary of a model's work, so it decays faster and needs corroboration.
- ``external``  -- originated in content outside a trusted package adapter: imports,
  instruction files, and HTTP/REST payloads. **Never auto-injected.** This is precisely
  the channel MemoryGraft uses, and quarantined records remain available to explicit
  recall.
- ``unknown``   -- unlabelled or malformed. Quarantined rather than trusted by default.

**Trust decay.** Trust falls with age on a per-tier half-life, so a memory that has not
been reconfirmed stops outranking newer information instead of competing with it forever.
Access reinforces: a memory that keeps proving useful decays more slowly.

Nothing here deletes anything. Quarantine and decay affect *injection eligibility* only;
explicit recall still returns everything, because hiding data from the user is a
different and worse failure than injecting it into a prompt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional

from visp_memory.core.clock import parse_utc, utc_now

PROVENANCE_TAG_PREFIX = "provenance:"


class Provenance(str, Enum):
    """Where a memory came from."""

    AUTHORED = "authored"
    DERIVED = "derived"
    ASSISTED = "assisted"
    EXTERNAL = "external"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: Any) -> "Provenance":
        # Members must be handled before str(): for a (str, Enum) subclass on Python
        # 3.11+, str(Provenance.DERIVED) is "Provenance.DERIVED", not "derived", so
        # round-tripping a member through str() silently yields UNKNOWN -- which
        # disables the entire trust layer without any error.
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except (ValueError, AttributeError):
            return cls.UNKNOWN


class WriteChannel(str, Enum):
    """Package-owned write entrypoints that assign provenance."""

    LIBRARY = "library"
    CLI = "cli"
    MCP = "mcp"
    HTTP = "http"
    REST = "rest"
    IMPORT = "import"
    INSTRUCTION = "instruction"
    CONVERSATION = "conversation"
    COMPRESSION = "compression"
    REFLECTION = "reflection"
    TEST_CAPTURE = "test_capture"
    GIT = "git"
    BOOTSTRAP = "bootstrap"
    KIT_CONTRACT = "kit-contract"


@dataclass(frozen=True)
class TierPolicy:
    """Per-tier trust parameters."""

    base_trust: float
    halflife_days: float
    injectable: bool = True


@dataclass(frozen=True)
class ChannelPolicy:
    """Provenance assigned by one package adapter, never by its payload."""

    provenance: Provenance
    source: str


_CHANNEL_POLICIES = {
    WriteChannel.LIBRARY: ChannelPolicy(Provenance.UNKNOWN, "unknown"),
    WriteChannel.CLI: ChannelPolicy(Provenance.AUTHORED, "authored"),
    WriteChannel.MCP: ChannelPolicy(Provenance.ASSISTED, "assisted"),
    WriteChannel.HTTP: ChannelPolicy(Provenance.EXTERNAL, "external"),
    WriteChannel.REST: ChannelPolicy(Provenance.EXTERNAL, "external"),
    WriteChannel.IMPORT: ChannelPolicy(Provenance.EXTERNAL, "external"),
    WriteChannel.INSTRUCTION: ChannelPolicy(Provenance.EXTERNAL, "external"),
    WriteChannel.CONVERSATION: ChannelPolicy(Provenance.ASSISTED, "assisted"),
    WriteChannel.COMPRESSION: ChannelPolicy(Provenance.ASSISTED, "assisted"),
    WriteChannel.REFLECTION: ChannelPolicy(Provenance.ASSISTED, "assisted"),
    WriteChannel.TEST_CAPTURE: ChannelPolicy(Provenance.DERIVED, "derived"),
    WriteChannel.GIT: ChannelPolicy(Provenance.DERIVED, "derived"),
    WriteChannel.BOOTSTRAP: ChannelPolicy(Provenance.DERIVED, "derived"),
    WriteChannel.KIT_CONTRACT: ChannelPolicy(Provenance.DERIVED, "kit"),
}
CHANNEL_POLICIES: Mapping[WriteChannel, ChannelPolicy] = MappingProxyType(_CHANNEL_POLICIES)


# Half-lives are deliberately long: this guards against *stale* memory, not against
# memory existing. A convention written a year ago is usually still a convention.
TIER_POLICIES: dict[Provenance, TierPolicy] = {
    Provenance.AUTHORED: TierPolicy(base_trust=1.00, halflife_days=540),
    Provenance.DERIVED: TierPolicy(base_trust=0.90, halflife_days=270),
    Provenance.ASSISTED: TierPolicy(base_trust=0.70, halflife_days=120),
    Provenance.UNKNOWN: TierPolicy(
        base_trust=0.0,
        halflife_days=120,
        injectable=False,
    ),
    # Quarantined: base trust is irrelevant because injectable is False.
    Provenance.EXTERNAL: TierPolicy(base_trust=0.30, halflife_days=60, injectable=False),
}

# Below this, a memory is too weak or too stale to spend context on. Explicit recall is
# unaffected.
DEFAULT_MIN_TRUST = 0.35

# Repeated use is evidence of usefulness, but it must not let a stale memory live
# forever, so the bonus is capped well below the decay it can offset.
_MAX_REINFORCEMENT = 0.15


def provenance_of(memory: dict[str, Any]) -> Provenance:
    """Read a memory's provenance tier from its tags, falling back to its source field."""
    tags = memory.get("tags") or []
    if isinstance(tags, (list, tuple, set)):
        for tag in tags:
            text = str(tag)
            if text.startswith(PROVENANCE_TAG_PREFIX):
                return Provenance.parse(text[len(PROVENANCE_TAG_PREFIX) :])

    source = memory.get("source")
    if source:
        return Provenance.parse(source)
    return Provenance.UNKNOWN


def provenance_tag(tier: Provenance | str) -> str:
    """Build the tag that records a provenance tier."""
    return f"{PROVENANCE_TAG_PREFIX}{Provenance.parse(tier).value}"


def with_provenance(tags: Optional[Iterable[str]], tier: Provenance | str) -> list[str]:
    """Return ``tags`` with any existing provenance tag replaced by ``tier``."""
    kept = [
        str(tag)
        for tag in (tags or ())
        if not str(tag).startswith(PROVENANCE_TAG_PREFIX)
    ]
    kept.append(provenance_tag(tier))
    return kept


def parse_write_channel(channel: WriteChannel | str) -> WriteChannel:
    """Parse a package-owned channel, refusing invented values."""
    if isinstance(channel, WriteChannel):
        return channel
    try:
        return WriteChannel(str(channel).strip())
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Unknown memory write channel: {channel!r}") from exc


def channel_policy(channel: WriteChannel | str) -> ChannelPolicy:
    """Return the immutable policy for a package-owned write channel."""
    return CHANNEL_POLICIES[parse_write_channel(channel)]


def with_channel_provenance(
    tags: Optional[Iterable[str]], channel: WriteChannel | str
) -> list[str]:
    """Replace payload provenance with the tier owned by ``channel``."""
    return with_provenance(tags, channel_policy(channel).provenance)


def age_days(memory: dict[str, Any], *, now=None) -> float:
    """Age in days, or 0.0 when the timestamp is missing or unparseable."""
    created = memory.get("created_at")
    if not created:
        return 0.0
    try:
        stamp = parse_utc(created) if isinstance(created, str) else created
    except Exception:
        return 0.0
    if stamp is None:
        return 0.0
    delta = (now or utc_now()) - stamp
    return max(0.0, delta.total_seconds() / 86400.0)


@dataclass(frozen=True)
class TrustAssessment:
    """Why a memory is or is not eligible for injection."""

    tier: Provenance
    trust: float
    quarantined: bool
    reason: str

    @property
    def injectable(self) -> bool:
        return not self.quarantined and self.trust >= DEFAULT_MIN_TRUST


@dataclass(frozen=True)
class TrustRejection:
    """Structured rejection retained by unsolicited-read surfaces for reporting."""

    memory: dict[str, Any]
    assessment: TrustAssessment

    @property
    def reason(self) -> str:
        return self.assessment.reason

    def as_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory.get("id"),
            "provenance": self.assessment.tier.value,
            "trust": round(self.assessment.trust, 4),
            "quarantined": self.assessment.quarantined,
            "reason": self.assessment.reason,
        }


@dataclass(frozen=True)
class TrustFilterResult:
    """Allowed memories plus stable rejection diagnostics for one trust boundary."""

    allowed: list[dict[str, Any]]
    rejected: list[TrustRejection]
    considered_count: int

    @classmethod
    def combine(cls, results: Iterable["TrustFilterResult"]) -> "TrustFilterResult":
        items = list(results)
        return cls(
            allowed=[memory for result in items for memory in result.allowed],
            rejected=[rejection for result in items for rejection in result.rejected],
            considered_count=sum(result.considered_count for result in items),
        )

    @property
    def quarantined_count(self) -> int:
        return sum(item.assessment.quarantined for item in self.rejected)

    @property
    def below_trust_count(self) -> int:
        return len(self.rejected) - self.quarantined_count

    def diagnostics(self) -> dict[str, Any]:
        return {
            "considered_count": self.considered_count,
            "allowed_count": len(self.allowed),
            "rejected_count": len(self.rejected),
            "quarantined_count": self.quarantined_count,
            "below_trust_count": self.below_trust_count,
            "rejected": [item.as_dict() for item in self.rejected],
        }


def assess(
    memory: dict[str, Any],
    *,
    min_trust: float = DEFAULT_MIN_TRUST,
    now=None,
) -> TrustAssessment:
    """Score how far a memory can be trusted for unsolicited injection."""
    tier = provenance_of(memory)
    if memory.get("layer") == "semantic" and (
        memory.get("belief_type") == "hypothesis"
        or memory.get("category") == "hypothesis"
        or memory.get("epistemic_status") == "hypothesized"
    ):
        return TrustAssessment(
            tier=tier,
            trust=0.0,
            quarantined=True,
            reason="provisional semantic belief is explicit-inspection-only",
        )
    policy = TIER_POLICIES.get(tier, TIER_POLICIES[Provenance.UNKNOWN])

    if not policy.injectable:
        if tier is Provenance.UNKNOWN:
            reason = "quarantined: provenance is missing or malformed"
        else:
            reason = (
                "quarantined: originated outside this repository, so it is the channel "
                "poisoned memories arrive through"
            )
        return TrustAssessment(
            tier=tier,
            trust=0.0,
            quarantined=True,
            reason=reason,
        )

    days = age_days(memory, now=now)
    decay = math.pow(0.5, days / policy.halflife_days) if policy.halflife_days > 0 else 1.0

    access_count = memory.get("access_count") or 0
    try:
        reinforcement = min(_MAX_REINFORCEMENT, math.log1p(float(access_count)) / 20.0)
    except (TypeError, ValueError):
        reinforcement = 0.0

    trust = min(1.0, policy.base_trust * decay + reinforcement)

    if trust < min_trust:
        reason = (
            f"trust {trust:.2f} below {min_trust:.2f} "
            f"({tier.value}, {days:.0f} days old)"
        )
    else:
        reason = f"{tier.value}, trust {trust:.2f}"

    return TrustAssessment(tier=tier, trust=trust, quarantined=False, reason=reason)


def filter_unsolicited(
    memories: list[dict[str, Any]],
    *,
    min_trust: float = DEFAULT_MIN_TRUST,
    now=None,
) -> TrustFilterResult:
    """Apply the common trust gate for prompt-adjacent, unsolicited memory reads.

    Each returned memory carries its assessment under ``trust`` / ``provenance`` so
    downstream reporting can explain the decision without recomputing it.
    """
    allowed: list[dict[str, Any]] = []
    rejected: list[TrustRejection] = []

    for memory in memories:
        assessment = assess(memory, min_trust=min_trust, now=now)
        if assessment.injectable:
            enriched = dict(memory)
            enriched["trust"] = round(assessment.trust, 4)
            enriched["provenance"] = assessment.tier.value
            allowed.append(enriched)
        else:
            rejected.append(TrustRejection(memory=memory, assessment=assessment))

    return TrustFilterResult(
        allowed=allowed,
        rejected=rejected,
        considered_count=len(memories),
    )


def filter_injectable(
    memories: list[dict[str, Any]],
    *,
    min_trust: float = DEFAULT_MIN_TRUST,
    now=None,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], TrustAssessment]]]:
    """Compatibility tuple wrapper around the structured unsolicited trust filter."""
    result = filter_unsolicited(memories, min_trust=min_trust, now=now)

    return result.allowed, [
        (rejection.memory, rejection.assessment) for rejection in result.rejected
    ]

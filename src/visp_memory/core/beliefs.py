"""Closed governed vocabulary for semantic beliefs.

Lifecycle state remains owned by ``MemoryStatus`` in the storage layer.  The
epistemic state below records what is known about a belief and is deliberately
persisted independently from that lifecycle state.
"""

from enum import Enum
from typing import Any


class BeliefType(str, Enum):
    """The complete set of governed semantic-belief types."""

    FACT = "fact"
    PREFERENCE = "preference"
    PROCEDURE = "procedure"
    PROHIBITION = "prohibition"
    HYPOTHESIS = "hypothesis"
    NEGATIVE = "negative"


class EpistemicStatus(str, Enum):
    """The complete set of governed epistemic states."""

    OBSERVED = "observed"
    CORROBORATED = "corroborated"
    INFERRED = "inferred"
    HYPOTHESIZED = "hypothesized"
    CONTRADICTED = "contradicted"
    STALE = "stale"
    REVOKED = "revoked"


LEGACY_BELIEF_TYPE_MAP = {
    "fact": BeliefType.FACT,
    "invariant": BeliefType.FACT,
    "behavior": BeliefType.FACT,
    "contract": BeliefType.FACT,
    "preference": BeliefType.PREFERENCE,
    "convention": BeliefType.PREFERENCE,
    "procedure": BeliefType.PROCEDURE,
    "pattern": BeliefType.PROCEDURE,
    "best_practice": BeliefType.PROCEDURE,
    "runbook": BeliefType.PROCEDURE,
    "principle": BeliefType.PROCEDURE,
    "negative": BeliefType.NEGATIVE,
    "antipattern": BeliefType.NEGATIVE,
    "fragile_area": BeliefType.NEGATIVE,
    "known_issue": BeliefType.NEGATIVE,
    "gotcha": BeliefType.NEGATIVE,
}

LEGACY_EPISTEMIC_STATUS_MAP = {
    "active": EpistemicStatus.INFERRED,
    "pending": EpistemicStatus.INFERRED,
    "archived": EpistemicStatus.STALE,
    "superseded": EpistemicStatus.CONTRADICTED,
    "merged": EpistemicStatus.CONTRADICTED,
    "deleted": EpistemicStatus.REVOKED,
}

HYPOTHESIS_TTL_DAYS = 7


def normalize_belief_type(value: Any) -> str:
    """Return one governed belief type or reject the semantic write."""
    try:
        return BeliefType(value).value
    except (TypeError, ValueError) as exc:
        accepted = ", ".join(item.value for item in BeliefType)
        raise ValueError(
            f"semantic belief type must be one of: {accepted}"
        ) from exc


def normalize_epistemic_status(value: Any) -> str:
    """Return one governed epistemic state or reject the semantic write."""
    try:
        return EpistemicStatus(value).value
    except (TypeError, ValueError) as exc:
        accepted = ", ".join(item.value for item in EpistemicStatus)
        raise ValueError(
            f"semantic belief epistemic status must be one of: {accepted}"
        ) from exc


def map_producer_belief_type(value: Any) -> str:
    """Map legacy machine output without ever fabricating prohibition authority."""
    normalized = str(value or "").strip().casefold()
    if normalized == BeliefType.PROHIBITION.value:
        return BeliefType.HYPOTHESIS.value
    return LEGACY_BELIEF_TYPE_MAP.get(
        normalized, BeliefType.HYPOTHESIS
    ).value


def migrate_legacy_belief_fields(category: Any, lifecycle_status: Any) -> tuple[str, str]:
    """Map one schema-v3 semantic row without inventing authority."""
    normalized_category = str(category or "").strip().casefold()
    if normalized_category == BeliefType.HYPOTHESIS.value:
        belief_type = BeliefType.HYPOTHESIS
    else:
        belief_type = LEGACY_BELIEF_TYPE_MAP.get(
            normalized_category, BeliefType.HYPOTHESIS
        )
    if belief_type is BeliefType.HYPOTHESIS:
        epistemic_status = EpistemicStatus.HYPOTHESIZED
    else:
        epistemic_status = LEGACY_EPISTEMIC_STATUS_MAP.get(
            str(lifecycle_status or "").strip().casefold(),
            EpistemicStatus.INFERRED,
        )
    return belief_type.value, epistemic_status.value

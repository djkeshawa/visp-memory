"""Shared temporal and entity-scope eligibility for memory reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from visp_memory.core.clock import parse_utc, utc_now

UNSCOPED_REPO_ID = "__visp_unscoped__"


def require_repo_id(repo_id: Any) -> str:
    """Return a normalized repository scope or refuse an implicit global read.

    UNSCOPED_REPO_ID is refused ON PURPOSE, and the refusal is load-bearing: it
    is the quarantine that keeps rows of unknown provenance out of every recall.
    Rows land there two ways — `_migrate_v2_to_v3` stamps it onto data written
    before repository scoping existed, and an unscoped write falls back to it in
    `store_memory`. Both carry Provenance.UNKNOWN, and neither may be surfaced
    to a model as though it were trusted project knowledge. See
    tests/core/test_evidence_contract.py, which pins exactly that.

    So do NOT "fix" this by teaching recall to serve the bucket, or by having
    import re-scope those rows into a real project. That is not recovering data,
    it is laundering unattributed content into the trusted set.

    What was genuinely broken was the SURFACE, not this rule: `visp-memory
    record` in a project without a scope reported "Recorded:" and printed an ID
    for a row it had just quarantined, and the later recall answered with an
    unhandled traceback. Both are fixed where they belong, in the CLI.
    """
    if not isinstance(repo_id, str) or not repo_id.strip():
        raise ValueError("repo_id is required")
    normalized = repo_id.strip()
    if normalized == UNSCOPED_REPO_ID:
        raise ValueError("reserved repository scope cannot be used for recall")
    return normalized


def normalize_scope_values(value: Any, *, field: str) -> tuple[str, ...]:
    """Normalize one declared scope dimension from a string or string collection."""
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
    else:
        raise ValueError(f"malformed {field} scope")
    if not values or any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"malformed {field} scope")
    return tuple(sorted({item.strip().casefold() for item in values}))


def normalize_optional_scope_values(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    return normalize_scope_values(value, field=field)


@dataclass(frozen=True)
class EligibilityAssessment:
    eligible: bool
    code: str
    reason: str


@dataclass(frozen=True)
class EligibilityRejection:
    memory: dict[str, Any]
    assessment: EligibilityAssessment

    @property
    def reason(self) -> str:
        return self.assessment.reason

    def as_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory.get("id"),
            "code": self.assessment.code,
            "reason": self.assessment.reason,
        }


@dataclass(frozen=True)
class EligibilityFilterResult:
    allowed: list[dict[str, Any]]
    rejected: list[EligibilityRejection]
    considered_count: int

    @classmethod
    def combine(cls, results: Iterable["EligibilityFilterResult"]) -> "EligibilityFilterResult":
        items = list(results)
        return cls(
            allowed=[memory for result in items for memory in result.allowed],
            rejected=[rejection for result in items for rejection in result.rejected],
            considered_count=sum(result.considered_count for result in items),
        )

    def diagnostics(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for rejection in self.rejected:
            code = rejection.assessment.code
            counts[code] = counts.get(code, 0) + 1
        return {
            "considered_count": self.considered_count,
            "allowed_count": len(self.allowed),
            "rejected_count": len(self.rejected),
            "rejection_counts": counts,
            "rejected": [rejection.as_dict() for rejection in self.rejected],
        }


def _parse_bound(metadata: dict[str, Any], field: str):
    if field not in metadata or metadata[field] is None:
        return None, None
    parsed = parse_utc(metadata[field])
    if parsed is None:
        return None, EligibilityAssessment(
            eligible=False,
            code=f"malformed_{field}",
            reason=f"malformed {field}",
        )
    return parsed, None


def assess_recall_eligibility(
    memory: dict[str, Any],
    *,
    repo_id: str,
    environment: Any = None,
    task_type: Any = None,
    as_of: Any = None,
) -> EligibilityAssessment:
    """Assess temporal validity plus repository, environment, and task scope."""
    requested_repo = require_repo_id(repo_id)
    requested_environment = normalize_optional_scope_values(
        environment, field="environment"
    )
    requested_task_types = normalize_optional_scope_values(task_type, field="task_type")
    if memory.get("repo_id") != requested_repo:
        return EligibilityAssessment(False, "repo_mismatch", "repository scope mismatch")

    if as_of is None:
        as_of_time = utc_now()
    else:
        as_of_time = parse_utc(as_of)
        if as_of_time is None:
            raise ValueError("as_of must be a valid timestamp")

    metadata = memory.get("metadata")
    if metadata is None:
        metadata = {}
    elif not isinstance(metadata, dict):
        return EligibilityAssessment(False, "malformed_metadata", "malformed memory metadata")

    valid_from, error = _parse_bound(metadata, "valid_from")
    if error:
        return error
    valid_to, error = _parse_bound(metadata, "valid_to")
    if error:
        return error
    if valid_from and as_of_time < valid_from:
        return EligibilityAssessment(
            False,
            "not_yet_valid",
            f"not yet valid until {valid_from.isoformat()}",
        )
    if valid_to and as_of_time >= valid_to:
        return EligibilityAssessment(
            False,
            "expired",
            f"expired at {valid_to.isoformat()}",
        )

    for field, requested_values in (
        ("environment", requested_environment),
        ("task_type", requested_task_types),
    ):
        if field not in metadata or metadata[field] is None:
            continue
        try:
            declared_values = normalize_scope_values(metadata[field], field=field)
        except ValueError as exc:
            return EligibilityAssessment(False, f"malformed_{field}", str(exc))
        if not requested_values:
            return EligibilityAssessment(
                False,
                f"missing_{field}",
                f"{field} scope requires caller match",
            )
        if not set(declared_values).intersection(requested_values):
            return EligibilityAssessment(
                False,
                f"{field}_mismatch",
                f"{field} scope mismatch",
            )

    return EligibilityAssessment(True, "eligible", "temporally valid and in scope")


def filter_recall_eligible(
    memories: list[dict[str, Any]],
    *,
    repo_id: str,
    environment: Any = None,
    task_type: Any = None,
    as_of: Any = None,
) -> EligibilityFilterResult:
    """Filter memory rows through the shared fail-closed read eligibility contract."""
    repo_id = require_repo_id(repo_id)
    environment = normalize_optional_scope_values(environment, field="environment")
    task_type = normalize_optional_scope_values(task_type, field="task_type")
    if as_of is None:
        as_of = utc_now()
    else:
        parsed_as_of = parse_utc(as_of)
        if parsed_as_of is None:
            raise ValueError("as_of must be a valid timestamp")
        as_of = parsed_as_of
    allowed: list[dict[str, Any]] = []
    rejected: list[EligibilityRejection] = []
    for memory in memories:
        assessment = assess_recall_eligibility(
            memory,
            repo_id=repo_id,
            environment=environment or None,
            task_type=task_type or None,
            as_of=as_of,
        )
        if assessment.eligible:
            allowed.append(memory)
        else:
            rejected.append(EligibilityRejection(memory, assessment))
    return EligibilityFilterResult(allowed, rejected, len(memories))

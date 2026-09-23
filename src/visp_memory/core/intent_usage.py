"""Report whether anything has ever set an intent in this store.

A store can hold plenty of memories and still have zero intents, and nothing
said so: `visp-memory stats` printed `intents: 0` next to a healthy memory count
and no surface treated that as a finding. The intent layer holds what the work
is *for*, what to avoid, and which task is live — the direction an assistant
otherwise re-derives every session — so a store that has recorded outcomes but
never recorded a direction is a store using half of what it ships.

This is a description, never a verdict. Memory is non-authoritative: reporting
that no intent was set says nothing about whether the work was correct, in
scope, or finished.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from visp_memory.core.verbs import INTENT_VERBS, render_inline_commands

# Statuses the report can carry. Anything other than ``never_used`` and
# ``in_use`` means the check could not run, not that the answer was "fine".
STATUS_NO_STORE = "no_store"
STATUS_EMPTY = "empty"
STATUS_NEVER_USED = "never_used"
STATUS_IN_USE = "in_use"
STATUS_NOT_CHECKED = "not_checked"
STATUS_UNREADABLE = "unreadable"


@dataclass(frozen=True)
class IntentUsageReport:
    """What the store says about its own use of the intent lifecycle."""

    status: str
    memories: int = 0
    active_intents: int = 0
    total_intents: int = 0
    detail: str = ""

    @property
    def never_used(self) -> bool:
        """True when the store holds memories and not one intent, ever."""
        return self.status == STATUS_NEVER_USED

    def headline(self) -> str:
        """One line, safe to print next to the other diagnostics."""
        if self.status == STATUS_NEVER_USED:
            return (
                f"{self._memory_count()}, 0 intents - the intent lifecycle has "
                "never been used in this store"
            )
        if self.status == STATUS_IN_USE:
            # Deliberately not "N active of M recorded": intent status is
            # externally owned and nothing in this package moves it off
            # `active`, so the two numbers are always equal and the phrasing
            # would imply a closing step the lifecycle does not perform.
            return f"{self._memory_count()}, {self.total_intents} intent(s) recorded"
        if self.status == STATUS_EMPTY:
            return "store is empty - nothing recorded yet"
        if self.status == STATUS_NO_STORE:
            return "no store yet; run visp-memory init"
        return self.detail or self.status

    def _memory_count(self) -> str:
        return f"{self.memories} {'memory' if self.memories == 1 else 'memories'}"

    def remediation(self) -> Optional[str]:
        """What to run to start using it, or None when there is nothing to say."""
        if not self.never_used:
            return None
        return (
            f"Set the direction with {render_inline_commands(INTENT_VERBS)}; "
            "`visp-memory intent list` shows what is active. Direction only - "
            "memory records intents and grants no scope or permission."
        )

    def as_dict(self) -> Dict[str, Any]:
        """Serialisable form for `doctor --format json`."""
        return {
            "status": self.status,
            "memories": self.memories,
            "active_intents": self.active_intents,
            "total_intents": self.total_intents,
            "headline": self.headline(),
            "remediation": self.remediation(),
            "detail": self.detail,
        }


def check_intent_usage(config) -> IntentUsageReport:
    """Inspect the configured store for intent adoption.

    Reads the store read-only and never creates or repairs it: a diagnostic that
    writes to what it measures cannot report on it.

    Args:
        config: A loaded ``MemoryConfig``.

    Returns:
        An :class:`IntentUsageReport`. Any condition that stops the check from
        running is reported as its own status, never as a clean result.
    """
    if config.storage.mode == "client":
        return IntentUsageReport(
            status=STATUS_NOT_CHECKED,
            detail="Storage is client mode; intent adoption is the server's to report.",
        )
    if config.storage.backend not in ("sqlite", "neo4j"):
        return IntentUsageReport(
            status=STATUS_NOT_CHECKED,
            detail=(
                f"Intent adoption reporting covers sqlite and neo4j; this store is "
                f"{config.storage.backend}."
            ),
        )

    from visp_memory.core.storage import LocalStorage

    try:
        counts = (
            _inspect_neo4j_intent_usage(config.storage)
            if config.storage.backend == "neo4j"
            else LocalStorage.inspect_intent_usage(Path(config.storage.data_dir))
        )
    except Exception as exc:
        return IntentUsageReport(status=STATUS_UNREADABLE, detail=str(exc))

    if not counts["exists"]:
        return IntentUsageReport(status=STATUS_NO_STORE)

    return IntentUsageReport(
        status=_status_for(counts),
        memories=counts["memories"],
        active_intents=counts["active_intents"],
        total_intents=counts["total_intents"],
    )


def _inspect_neo4j_intent_usage(config) -> Dict[str, Any]:
    # Constructing Neo4jStorage initializes schema; diagnostics must not do that.
    from visp_memory.core.neo4j_storage import GraphDatabase

    if GraphDatabase is None:
        raise RuntimeError("Neo4j driver is unavailable; intent usage was not inspected")
    with GraphDatabase.driver(
        config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password),
        connection_timeout=5,
    ) as driver:
        with driver.session(default_access_mode="READ") as session:
            result = session.run(
                "CALL { MATCH (m:Memory) WHERE coalesce(m.status, 'active') = 'active' "
                "RETURN count(m) AS memories } "
                "CALL { MATCH (i:Intent) RETURN count(i) AS total_intents, "
                "sum(CASE WHEN coalesce(i.status, 'active') = 'active' THEN 1 ELSE 0 END) "
                "AS active_intents } "
                "RETURN memories, total_intents, active_intents"
            ).single()
            if result is None:
                raise RuntimeError("Neo4j intent inspection returned no counts")
            return {"exists": True, **dict(result)}


def _status_for(counts: Dict[str, int]) -> str:
    """Classify the counts. Zero intents only matters once something is stored."""
    if counts["total_intents"] > 0:
        return STATUS_IN_USE
    if counts["memories"] > 0:
        return STATUS_NEVER_USED
    return STATUS_EMPTY

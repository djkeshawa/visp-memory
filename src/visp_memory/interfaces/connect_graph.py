"""Record identities and provenance summaries for migration preflight."""

from collections import Counter
from typing import Iterator

from visp_memory.core.trust import Provenance, provenance_of


def graph_records(graph: dict) -> Iterator[tuple[str, dict]]:
    for rows in (graph.get("memories") or {}).values():
        for row in rows:
            yield "memories", row
    for kind in (
        "evidence", "intents", "relationships", "authority_attestations", "belief_authority",
    ):
        for row in graph.get(kind) or []:
            yield kind, row


def record_ids(graph: dict) -> set[tuple[str, str]]:
    return {(kind, row["id"]) for kind, row in graph_records(graph)}


def trust_counts(graph: dict) -> Counter:
    counts = Counter({tier.value: 0 for tier in Provenance})
    for kind, row in graph_records(graph):
        if kind == "evidence":
            tier = Provenance.parse(row.get("provenance"))
        elif kind == "memories":
            tier = provenance_of(row)
        else:
            # Graph links and intents have no memory provenance tier.
            tier = Provenance.UNKNOWN
        counts[tier.value] += 1
    return counts

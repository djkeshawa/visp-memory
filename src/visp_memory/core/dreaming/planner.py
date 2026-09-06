"""Bounded, deterministic proposals for a project's resting memory cycle."""

import hashlib
import json
import re
from collections import defaultdict
from itertools import combinations

from visp_memory.core.clock import parse_utc

SCAN_LIMIT = 500
PAIR_LIMIT = 5000
PROPOSAL_LIMIT = 40
STOPWORDS = frozenset(
    "the a an is are was were be been to of in for and or with this that it on".split()
)
NEGATIONS = frozenset({"not", "never", "cannot", "disabled", "forbidden", "false"})


def fingerprint(memory):
    """Ignore access bookkeeping, but protect every field that a review can change."""
    values = {
        key: value for key, value in memory.items() if key not in {"accessed_at", "access_count"}
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def scope_key(memory):
    metadata = memory.get("metadata") or {}
    return json.dumps(
        {
            key: metadata.get(key)
            for key in (
                "author_id",
                "team_id",
                "environment",
                "task_type",
                "files",
                "valid_from",
                "valid_to",
            )
        },
        sort_keys=True,
    )


def protected(memory):
    metadata = memory.get("metadata") or {}
    return bool(
        metadata.get("pinned")
        or metadata.get("hold")
        or memory.get("approved_at")
        or memory.get("category") in {"prohibition", "warning"}
    )


def safe_duplicate_key(memory):
    # Identical spelling/case matters for code. Metadata carries trust and scope.
    return json.dumps(
        {
            key: memory.get(key)
            for key in (
                "content",
                "layer",
                "category",
                "repo_id",
                "metadata",
                "source",
                "tags",
                "epistemic_status",
                "belief_type",
                "quality_flags",
                "approved_by",
                "approved_at",
                "importance",
            )
        },
        sort_keys=True,
    )


def proposal(kind, memories, reason, **extra):
    ids = [m["id"] for m in memories]
    key = hashlib.sha256((kind + "\0" + "\0".join(sorted(ids))).encode()).hexdigest()[:20]
    return {
        "id": key,
        "kind": kind,
        "memory_ids": ids,
        "reason": reason,
        "sources": [{"id": m["id"], "content": m["content"][:1200]} for m in memories],
        "fingerprints": {m["id"]: fingerprint(m) for m in memories},
        **extra,
    }


def plan(memories, linked_ids, now):
    proposals = []
    groups = defaultdict(list)
    for memory in memories:
        if memory.get("content", "").strip():
            groups[safe_duplicate_key(memory)].append(memory)
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda m: (-int(m.get("access_count") or 0), m["id"]))
        eligible = all(
            m.get("layer") == "episodic"
            and not protected(m)
            and not m.get("source_ids")
            and m["id"] not in linked_ids
            for m in group
        )
        proposals.append(
            proposal(
                "duplicate",
                group,
                "Identical content and metadata; originals remain recoverable."
                if eligible
                else "These copies carry knowledge, protection, or links and need review.",
                automatic=eligible,
            )
        )

    for memory in memories:
        expiry = parse_utc((memory.get("metadata") or {}).get("valid_to"))
        if expiry and expiry < now and not protected(memory):
            proposals.append(
                proposal(
                    "expired",
                    [memory],
                    "Its recorded validity period has ended. Review before archiving.",
                    automatic=False,
                )
            )

    # An inverted word index avoids examining every possible pair. No vector or model calls.
    terms = {
        m["id"]: set(re.findall(r"\b\w{3,}\b", m["content"].casefold())) - STOPWORDS
        for m in memories
    }
    by_id = {m["id"]: m for m in memories}
    buckets = defaultdict(list)
    for memory in memories:
        for word in sorted(terms[memory["id"]]):
            buckets[(scope_key(memory), memory.get("layer"), word)].append(memory["id"])
    pairs = set()
    for ids in buckets.values():
        for left, right in combinations(ids[:30], 2):
            pairs.add(tuple(sorted((left, right))))
            if len(pairs) >= PAIR_LIMIT:
                break
        if len(pairs) >= PAIR_LIMIT:
            break
    for left, right in sorted(pairs):
        a, b = by_id[left], by_id[right]
        if a["content"] == b["content"]:
            continue
        common = terms[left] & terms[right]
        union = terms[left] | terms[right]
        if len(common) < 3 or len(common) / max(1, len(union)) < 0.55:
            continue
        conflict = bool(terms[left] & NEGATIONS) != bool(terms[right] & NEGATIONS)
        kind = "conflict" if conflict else "related"
        proposals.append(
            proposal(
                kind,
                [a, b],
                "Related wording contains different negation signals; check both sources."
                if conflict
                else "Shared words suggest a connection; review the sources for meaning.",
                automatic=False,
                draft_summary=None
                if conflict
                else "\n".join(f"• {m['content'][:500]} [{m['id']}]" for m in (a, b)),
            )
        )
        if len(proposals) >= PROPOSAL_LIMIT * 2:
            break
    return {
        "proposals": proposals[:PROPOSAL_LIMIT],
        "scanned": len(memories),
        "proposal_limit_reached": len(proposals) > PROPOSAL_LIMIT,
        "pair_limit_reached": len(pairs) >= PAIR_LIMIT,
        "method": "exact_and_lexical",
    }

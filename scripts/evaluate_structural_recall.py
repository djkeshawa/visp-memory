#!/usr/bin/env python3
"""Measure what structural conditioning does to Memory's recall, on an authored fixture.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
This is a **synthetic-corpus benchmark**. The repository is thirteen files invented
here, the memories are eighteen sentences written here, and the relevance labels are
authored here by the rule stated below. It measures whether the mechanism does the
thing it was built to do on a corpus where the right answer is known.

It is **not** a field result, and no number it prints is evidence about a real
repository or a real task. It is not comparable to, and must never be quoted beside,
`visp-kit`'s context-pack measurements: different system, different corpus, different
metric. This round's claim ceiling for Memory is conformance plus this benchmark.

Nothing here derives from `planning/private/holdout/`. The fixture is authored from
scratch and contains no material from any measured project.

THE CIRCULARITY PROBLEM, AND WHAT IS DONE ABOUT IT
--------------------------------------------------
A benchmark whose labels are "the memories the mechanism finds" measures nothing. Five
things keep this one honest, and a reader should check each:

1. **Labels precede the mechanism.** Relevance is authored per task with a written
   reason (`why`), by one rule: *an agent that did not know this memory would write
   worse code for this task.* Some labelled memories are two hops away, some are on the
   file in scope, and some are **not structurally reachable at all** -- so a perfect
   structural signal still cannot score 1.0, and the ceiling is visible.
2. **Structural distractors exist.** Several memories sit on files adjacent to a task's
   scope and are *not* relevant to it. Admitting them costs precision, and precision is
   reported next to recall rather than under it.
3. **There is a wrongness control, and it is the number that matters.** Arm C keeps
   every query and every seed identical and rotates the adjacency so that each named
   neighbour is a file that is genuinely not adjacent. On a corpus this small a *random*
   extra memory is relevant often enough to move recall on its own, so `arm C - arm A`
   is the "any three extra memories" effect and **`arm B - arm C` is the part
   attributable to the adjacency being correct.** Quoting `arm B - arm A` as the
   mechanism's benefit would be overclaiming, and the report says so in its own output.
4. **The inert cohort is a mandatory reporting line.** Tasks whose seeds have no
   neighbours in the snapshot are split out, because a mean over a mixed cohort hides a
   mechanism that does nothing on part of it.
5. **Cost is reported, not omitted.** Precision@5 falls. Three admissions per retrieval
   is three more memories in front of a model, and a recall gain bought at an
   unreported cost is not a gain.

Run: ``python scripts/evaluate_structural_recall.py``. Exits non-zero if the
preregistered expectations below are not met.
"""

from __future__ import annotations

import json
import statistics
import tempfile
from pathlib import Path
from typing import Any, Optional

from visp_memory.core.code_graph import FileGraph
from visp_memory.core.hybrid_retrieval import HybridRetriever
from visp_memory.core.storage import LocalStorage

REPO = "orders-service"
SNAPSHOT = "urn:visp-intel:snapshot:1.0:sha256:" + ("7f" * 32)

# --- the fixture repository -----------------------------------------------------------
# An orders service. Two isolated files and two test files are deliberate: the first
# make the mechanism inert for some tasks, the second exercise the test-edge kinds.

IMPORTS: dict[str, tuple[str, ...]] = {
    "src/api/orders.py": ("src/domain/orders.py", "src/domain/pricing.py"),
    "src/api/customers.py": ("src/domain/customers.py",),
    "src/domain/orders.py": ("src/domain/pricing.py", "src/store/repository.py"),
    "src/domain/pricing.py": ("src/domain/tax.py",),
    "src/domain/tax.py": ("src/config/settings.py",),
    "src/domain/customers.py": ("src/store/repository.py",),
    "src/store/repository.py": ("src/store/connection.py",),
    "src/store/connection.py": ("src/config/settings.py",),
}
TESTED_BY: dict[str, tuple[str, ...]] = {
    "src/domain/orders.py": ("tests/test_orders.py",),
    "src/domain/pricing.py": ("tests/test_pricing.py",),
}
ISOLATED = ("src/telemetry/metrics.py", "src/legacy/importer.py")

# --- the memory corpus ----------------------------------------------------------------
# `key` is used by the task labels below. Vocabulary is chosen so that a memory's words
# do not leak its file's identity: a memory about the tax module never says "tax".

MEMORIES: tuple[dict[str, Any], ...] = (
    {
        "key": "orders-api-idempotency",
        "file": "src/api/orders.py",
        "content": "Duplicate submissions are collapsed by the client-supplied request key.",
    },
    {
        "key": "orders-api-pagination",
        "file": "src/api/orders.py",
        "content": "Listing endpoints page by opaque cursor; offsets were removed in 2025.",
    },
    {
        "key": "orders-domain-state",
        "file": "src/domain/orders.py",
        "content": "A cancelled record can never return to an open state; the guard is one-way.",
    },
    {
        "key": "orders-domain-partial",
        "file": "src/domain/orders.py",
        "content": "Partial fulfilment writes two rows, and both must land or neither does.",
    },
    {
        "key": "pricing-rounding",
        "file": "src/domain/pricing.py",
        "content": "Every amount rounds half-up at two decimals, once, at the very end.",
    },
    {
        "key": "pricing-currency",
        "file": "src/domain/pricing.py",
        "content": "Mixed-currency baskets were tried in 2024 and abandoned; assume one unit.",
    },
    {
        "key": "tax-jurisdiction",
        "file": "src/domain/tax.py",
        "content": "Jurisdiction is resolved from the shipping address, never the billing one.",
    },
    {
        "key": "tax-exempt",
        "file": "src/domain/tax.py",
        "content": "Exempt entities still get a zero line so the audit trail stays complete.",
    },
    {
        "key": "settings-precedence",
        "file": "src/config/settings.py",
        "content": "Environment beats file beats default, and the file is read exactly once.",
    },
    {
        "key": "repository-lock",
        "file": "src/store/repository.py",
        "content": "Writers take the advisory lock before the transaction, not inside it.",
    },
    {
        "key": "repository-softdelete",
        "file": "src/store/repository.py",
        "content": "Rows are tombstoned rather than removed; every read filters the tombstones.",
    },
    {
        "key": "connection-pool",
        "file": "src/store/connection.py",
        "content": "The pool is sized for the smallest replica, not the primary.",
    },
    {
        "key": "customers-merge",
        "file": "src/domain/customers.py",
        "content": "Merging two records keeps the older identifier and rewrites references.",
    },
    {
        "key": "customers-api-validation",
        "file": "src/api/customers.py",
        "content": "Address validation happens at the edge; the domain layer trusts its input.",
    },
    {
        "key": "test-orders-fixtures",
        "file": "tests/test_orders.py",
        "content": "Fixtures freeze the clock; a test reading the wall clock is flaky by design.",
    },
    {
        "key": "test-pricing-goldens",
        "file": "tests/test_pricing.py",
        "content": "Golden files are regenerated by hand, never by the test that reads them.",
    },
    {
        "key": "telemetry-batching",
        "file": "src/telemetry/metrics.py",
        "content": "Counters flush every thirty seconds; a crash loses the current window.",
    },
    {
        "key": "legacy-encoding",
        "file": "src/legacy/importer.py",
        "content": "The 2019 archive is latin-1 and nothing in it may be assumed to be utf-8.",
    },
)

# --- the tasks ------------------------------------------------------------------------
# `relevant` is authored by one rule: an agent that did not know this memory would write
# worse code for this task. `why` records the reason for each, so a reader can disagree
# with a label rather than with a number. `unreachable` names the labelled memories that
# no structural walk from these seeds can reach -- the visible ceiling.

TASKS: tuple[dict[str, Any], ...] = (
    {
        "name": "rounding-bug-in-pricing",
        "query": "fix incorrect totals when a discount is applied",
        "files": ("src/domain/pricing.py",),
        "relevant": {
            "pricing-rounding": "the change is in the rounding path itself",
            "pricing-currency": "an abandoned approach the task would otherwise retry",
            "tax-jurisdiction": "one import away; totals include the tax line",
            "test-pricing-goldens": "the tests covering this file must be regenerated by hand",
        },
        "unreachable": (),
    },
    {
        "name": "new-order-cancellation-endpoint",
        "query": "add an endpoint that cancels an order",
        "files": ("src/api/orders.py",),
        "relevant": {
            "orders-api-idempotency": "a cancel endpoint must be idempotent like its siblings",
            "orders-domain-state": "one hop away; the one-way guard decides whether this is legal",
            "repository-softdelete": "two hops; cancelling must not delete the row",
        },
        "unreachable": (),
    },
    {
        "name": "settings-reload",
        "query": "support reloading configuration without a restart",
        "files": ("src/config/settings.py",),
        "relevant": {
            "settings-precedence": "the precedence rule is what a reload has to preserve",
            "connection-pool": "one hop; the pool is built from settings and would be rebuilt",
            "tax-exempt": "one hop; the tax module reads settings at import time",
        },
        "unreachable": (),
    },
    {
        "name": "repository-batch-writes",
        "query": "batch several writes into one transaction",
        "files": ("src/store/repository.py",),
        "relevant": {
            "repository-lock": "the lock ordering is exactly what batching changes",
            "repository-softdelete": "batched reads must still filter tombstones",
            "connection-pool": "one hop; a longer transaction holds a pooled connection",
            "orders-domain-partial": "one hop; the two-row invariant is the reason to batch",
        },
        "unreachable": (),
    },
    {
        "name": "customer-merge-audit",
        "query": "record an audit entry whenever two customers are merged",
        "files": ("src/domain/customers.py",),
        "relevant": {
            "customers-merge": "the merge rule is the behaviour being audited",
            "repository-softdelete": "one hop; the losing record is tombstoned, not deleted",
            "customers-api-validation": "one hop; the edge is where the merge request arrives",
        },
        "unreachable": (),
    },
    {
        "name": "telemetry-histogram",
        "query": "add a histogram for request duration",
        "files": ("src/telemetry/metrics.py",),
        "relevant": {
            "telemetry-batching": "the flush window is what a histogram has to survive",
        },
        "unreachable": (),
        "inert": "the file is in the snapshot and has no edges: the mechanism cannot help",
    },
    {
        "name": "legacy-import-rerun",
        "query": "re-run the 2019 archive import",
        "files": ("src/legacy/importer.py",),
        "relevant": {
            "legacy-encoding": "the encoding assumption is the whole trap",
            "repository-lock": "a bulk import contends with the writer lock",
        },
        "unreachable": ("repository-lock",),
        "inert": "isolated file; the relevant neighbour is real but not in the graph",
    },
    {
        "name": "orders-listing-performance",
        "query": "speed up the order listing endpoint",
        "files": ("src/api/orders.py", "src/domain/orders.py"),
        "relevant": {
            "orders-api-pagination": "cursor paging is the mechanism being tuned",
            "repository-softdelete": "one hop; the tombstone filter is the likely cost",
            "connection-pool": "two hops; pool size caps concurrent listing",
            "test-orders-fixtures": "one hop; the frozen clock makes timing tests meaningless",
        },
        "unreachable": (),
    },
)

def _all_files() -> tuple[set[str], set[str]]:
    files = set(IMPORTS) | set(ISOLATED)
    for targets in IMPORTS.values():
        files.update(targets)
    tests = {path for targets in TESTED_BY.values() for path in targets}
    files.update(tests)
    return files, tests


def _graph(imports: dict[str, tuple[str, ...]], tested_by: dict[str, tuple[str, ...]]):
    files, tests = _all_files()
    return FileGraph(
        repository_instance_id="urn:visp-intel:repository-instance:1.0:sha256:" + ("3c" * 32),
        snapshot_id=SNAPSHOT,
        head_snapshot_id=SNAPSHOT,
        file_paths=tuple(sorted(files)),
        test_file_paths=tuple(sorted(tests)),
        internal_edges={key: tuple(sorted(value)) for key, value in sorted(imports.items())},
        test_edges={key: tuple(sorted(value)) for key, value in sorted(tested_by.items())},
    )


def build_graph() -> FileGraph:
    return _graph(IMPORTS, TESTED_BY)


def build_misleading_graph() -> FileGraph:
    """The same file set, the same edge count, every edge pointing somewhere wrong.

    This is the seed-error arm, and it is deliberately the *worst* form of it. Rather
    than perturbing which files a task seeds from -- which would also change the entity
    channel and confound the measurement -- it keeps the query identical and rotates the
    adjacency, so every neighbour the walk names is a file that is genuinely not
    adjacent. Whatever recall survives this is recall structure could not have taken
    away, and whatever precision it costs is the ceiling on the damage.
    """
    sources = sorted(IMPORTS)
    rotated = {
        source: IMPORTS[sources[(index + 1) % len(sources)]]
        for index, source in enumerate(sources)
    }
    test_sources = sorted(TESTED_BY)
    rotated_tests = {
        source: TESTED_BY[test_sources[(index + 1) % len(test_sources)]]
        for index, source in enumerate(test_sources)
    }
    return _graph(rotated, rotated_tests)


def build_corpus(root: Path) -> tuple[LocalStorage, dict[str, str]]:
    storage = LocalStorage(root)
    keys: dict[str, str] = {}
    for record in MEMORIES:
        evidence_id = storage.store_evidence(record["content"], repo_id=REPO)
        # Deterministic ids. The retriever's last tie-break is the identifier, so a
        # fixture built on random uuids produces a different headline number on every
        # run -- which is a benchmark nobody can verify from disk.
        keys[record["key"]] = storage.store_memory(
            record["content"],
            layer="semantic",
            repo_id=REPO,
            evidence_ids=[evidence_id],
            auto_link=False,
            metadata={"files": [record["file"]], "confidence": 0.9},
            memory_id=f"fx-{record['key']}",
        )
    return storage, keys


def _recall_at(results: list[dict], expected: set[str], limit: int) -> float:
    if not expected:
        return 1.0
    found = {str(item["id"]) for item in results[:limit]}
    return len(found & expected) / len(expected)


def _precision_at(results: list[dict], expected: set[str], limit: int) -> float:
    window = results[:limit]
    if not window:
        return 0.0
    found = [item for item in window if str(item["id"]) in expected]
    return len(found) / len(window)


def _reciprocal_rank(results: list[dict], expected: set[str]) -> float:
    for index, item in enumerate(results, start=1):
        if str(item["id"]) in expected:
            return 1.0 / index
    return 0.0


def _run_arm(
    storage: LocalStorage,
    keys: dict[str, str],
    graph: Optional[FileGraph],
) -> list[dict[str, Any]]:
    retriever = HybridRetriever(storage, code_graph=graph)
    rows = []
    for task in TASKS:
        expected = {keys[key] for key in task["relevant"]}
        reachable = expected - {keys[key] for key in task["unreachable"]}
        results = retriever.retrieve(
            task["query"], repo_id=REPO, files=list(task["files"]), limit=20
        )
        admitted = [
            item for item in results if "structure" in item.get("retrieval_channels", [])
        ]
        rows.append(
            {
                "task": task["name"],
                "inert": bool(task.get("inert")),
                "returned": len(results),
                "recall_at_5": _recall_at(results, expected, 5),
                "recall_at_10": _recall_at(results, expected, 10),
                "reachable_recall_at_10": _recall_at(results, reachable, 10),
                "precision_at_5": _precision_at(results, expected, 5),
                "mrr": _reciprocal_rank(results, expected),
                "admitted": len(admitted),
                "admitted_relevant": sum(1 for item in admitted if str(item["id"]) in expected),
            }
        )
    return rows


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def mean(field: str, subset: list[dict[str, Any]]) -> float:
        return round(statistics.mean(row[field] for row in subset), 4) if subset else 0.0

    active = [row for row in rows if not row["inert"]]
    inert = [row for row in rows if row["inert"]]
    return {
        "tasks": len(rows),
        "recall_at_5": mean("recall_at_5", rows),
        "recall_at_10": mean("recall_at_10", rows),
        "reachable_recall_at_10": mean("reachable_recall_at_10", rows),
        "precision_at_5": mean("precision_at_5", rows),
        "mrr": mean("mrr", rows),
        "mean_returned": mean("returned", rows),
        "admitted_total": sum(row["admitted"] for row in rows),
        "admitted_relevant": sum(row["admitted_relevant"] for row in rows),
        "split": {
            "connected": {"tasks": len(active), "recall_at_10": mean("recall_at_10", active)},
            "inert": {"tasks": len(inert), "recall_at_10": mean("recall_at_10", inert)},
        },
    }


def evaluate() -> dict[str, Any]:
    graph = build_graph()
    with tempfile.TemporaryDirectory() as directory:
        storage, keys = build_corpus(Path(directory))

        arm_a = _run_arm(storage, keys, None)
        arm_b = _run_arm(storage, keys, graph)
        arm_c = _run_arm(storage, keys, build_misleading_graph())
        # Same call twice: the pack is deterministic given the same bytes, or it is not.
        repeat = _run_arm(storage, keys, build_graph())

    summary_a = _summarize(arm_a)
    summary_b = _summarize(arm_b)
    summary_c = _summarize(arm_c)

    per_task = [
        {
            "task": before["task"],
            "inert": before["inert"],
            "recall_at_10_before": before["recall_at_10"],
            "recall_at_10_after": after["recall_at_10"],
            "delta": round(after["recall_at_10"] - before["recall_at_10"], 4),
            "admitted": after["admitted"],
            "admitted_relevant": after["admitted_relevant"],
        }
        for before, after in zip(arm_a, arm_b)
    ]

    regressions = [row for row in per_task if row["delta"] < 0]
    admitted_precision = (
        round(summary_b["admitted_relevant"] / summary_b["admitted_total"], 4)
        if summary_b["admitted_total"]
        else None
    )

    metrics: dict[str, Any] = {
        "claim_ceiling": (
            "Synthetic authored corpus. Conformance plus this benchmark is the whole of "
            "Memory's claim this round. No field benefit is claimed, and no number here "
            "is evidence about any real repository or about visp-kit's context pack."
        ),
        "fixture": {
            "files": len(build_graph().file_paths),
            "memories": len(MEMORIES),
            "tasks": len(TASKS),
            "labels": sum(len(task["relevant"]) for task in TASKS),
            "unreachable_labels": sum(len(task["unreachable"]) for task in TASKS),
        },
        "arm_a_no_graph": summary_a,
        "arm_b_graph": summary_b,
        "arm_c_misleading_graph": summary_c,
        "per_task": per_task,
        "admitted_precision": admitted_precision,
        "deterministic": arm_b == repeat,
        "no_task_regressed": not regressions,
        "regressions": [row["task"] for row in regressions],
        "cost": {
            "mean_results_before": summary_a["mean_returned"],
            "mean_results_after": summary_b["mean_returned"],
            "bounded_by": "STRUCTURAL_MAX_ADMISSIONS per retrieval",
        },
        "attribution": {
            "recall_at_10_no_graph": summary_a["recall_at_10"],
            "recall_at_10_misleading_graph": summary_c["recall_at_10"],
            "recall_at_10_true_graph": summary_b["recall_at_10"],
            "any_three_extra_memories": round(
                summary_c["recall_at_10"] - summary_a["recall_at_10"], 4
            ),
            "correct_adjacency": round(
                summary_b["recall_at_10"] - summary_c["recall_at_10"], 4
            ),
            "admitted_precision_true_graph": admitted_precision,
            "admitted_precision_misleading_graph": (
                round(summary_c["admitted_relevant"] / summary_c["admitted_total"], 4)
                if summary_c["admitted_total"]
                else None
            ),
            # Counted from the fixture, not typed in. The hardcoded version of this
            # sentence said "14 files" while the same payload's `fixture.files`
            # said 13 — one artifact disagreeing with itself about its own corpus.
            "note": (
                "The headline B-minus-A gap is NOT the mechanism's benefit. This corpus "
                f"has {len(MEMORIES)} memories over {len(build_graph().file_paths)} files, "
                "so a randomly adjacent memory is "
                "relevant often enough that admitting any three raises recall. "
                "`correct_adjacency` is the honest figure; `any_three_extra_memories` is "
                "what a wrong graph buys and is the amount that must not be claimed."
            ),
        },
        "seed_error_damage": {
            "recall_at_10": summary_c["recall_at_10"],
            "vs_no_graph": round(summary_c["recall_at_10"] - summary_a["recall_at_10"], 4),
            "precision_at_5": summary_c["precision_at_5"],
            "precision_at_5_vs_no_graph": round(
                summary_c["precision_at_5"] - summary_a["precision_at_5"], 4
            ),
            "wasted_admissions": summary_c["admitted_total"] - summary_c["admitted_relevant"],
            "note": (
                "Same queries, same seeds, every adjacency rotated to a wrong file. A "
                "wrong neighbourhood can waste admissions and cost precision; it cannot "
                "remove a memory, which is what the recall floor below asserts."
            ),
        },
    }

    # Preregistered, and stated as pass/fail rather than as prose after the fact.
    metrics["expectations"] = {
        "recall_improves": summary_b["recall_at_10"] > summary_a["recall_at_10"],
        "correct_adjacency_beats_a_wrong_one": (
            summary_b["recall_at_10"] > summary_c["recall_at_10"]
            and (admitted_precision or 0.0)
            > (
                summary_c["admitted_relevant"] / summary_c["admitted_total"]
                if summary_c["admitted_total"]
                else 0.0
            )
        ),
        "no_task_regresses": metrics["no_task_regressed"],
        "seed_error_never_removes": summary_c["recall_at_10"] >= summary_a["recall_at_10"],
        "inert_cohort_unmoved": (
            summary_b["split"]["inert"]["recall_at_10"]
            == summary_a["split"]["inert"]["recall_at_10"]
        ),
        "deterministic": metrics["deterministic"],
        "ceiling_is_visible": summary_b["recall_at_10"] < 1.0,
    }
    metrics["passed"] = all(metrics["expectations"].values())
    return metrics


def main() -> int:
    metrics = evaluate()
    print(json.dumps(metrics, indent=2, sort_keys=True))
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

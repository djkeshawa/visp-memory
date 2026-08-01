#!/usr/bin/env python3
"""Measure how much of the "oracle-free gap" the injection policy closes.

SWE-ContextBench (arXiv:2602.08316) established the number this project exists to
attack. Across 1,100 tasks in 51 repositories:

- No context:                              26.26% resolution
- Curated ("oracle") summaries:            34.34% resolution
- Freely self-retrieved context:           12.12 points *below* the oracle variant,
                                           and more expensive than using no context

So curation is worth ~8 points, and naive retrieval gives most of that back. The gap
between "retrieve what looks related" and "retrieve what actually helps" is the product.

This benchmark measures *selection quality*, which is the part of that gap a memory
system controls. It is deterministic, needs no network or API key, and compares three
policies over the same labelled fixtures:

    oracle      -- select exactly the memories a human labelled relevant (upper bound)
    unfiltered  -- take the top-k recall results, the "free retrieval" baseline
    policy      -- this project's precision-gated injection

Metrics are precision, recall, F1, injected tokens, and -- the one most systems ignore
-- whether the policy correctly stays silent on tasks that no stored memory can help
with. A system that injects confidently on unanswerable tasks is worse than no memory,
which is exactly what the unfiltered baseline demonstrates below.

This is a selection-quality proxy, not a live-agent study. It cannot tell you how many
issues an agent resolves; it tells you whether the memories handed to that agent were
the right ones. Running it against a live model on SWE-Bench-CL-style chronological task
streams is the natural next step, and the fixtures here are shaped to port over.

Usage:
    python3 scripts/evaluate_oracle_gap.py
    python3 scripts/evaluate_oracle_gap.py --json
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from visp_memory import Memory, MemoryConfig
from visp_memory.core.injection import (
    CANDIDATE_MIN_SCORE,
    InjectionPolicy,
    gather_candidates,
    select_for_injection,
)
from visp_memory.core.trust import WriteChannel

# Characters per token, matching visp_memory.core.injection.
_CHARS_PER_TOKEN = 4

# The unfiltered baseline: what a system does when it "retrieves what looks related".
UNFILTERED_TOP_K = 8


@dataclass(frozen=True)
class Seed:
    """One memory to place in the fixture store."""

    key: str
    content: str
    category: str = "fact"
    layer: str = "semantic"
    importance: float = 0.8


@dataclass(frozen=True)
class Case:
    """A task, plus which seeded memories a human considers genuinely relevant."""

    case_id: str
    task: str
    files: tuple[str, ...] = field(default_factory=tuple)
    relevant: tuple[str, ...] = field(default_factory=tuple)

    @property
    def answerable(self) -> bool:
        return bool(self.relevant)


SEEDS: tuple[Seed, ...] = (
    Seed(
        "auth_jwt",
        "Authentication uses stateless JWT access tokens with a 15 minute expiry, "
        "refreshed through the /auth/refresh endpoint.",
        category="convention",
    ),
    Seed(
        "auth_race",
        "WARNING [src/auth/session.py]: Session cache writes race under concurrent "
        "refresh; take the session mutex before mutating the cache.",
        category="fragile_area",
        importance=0.9,
    ),
    Seed(
        "auth_revert",
        "Previously reverted: optimistic locking on the session table. It deadlocked "
        "under refresh storms and was backed out.",
        category="gotcha",
        importance=0.85,
    ),
    Seed(
        "billing_rounding",
        "Billing totals round half-even at two decimal places to match the ledger; "
        "never use round() on float amounts.",
        category="convention",
    ),
    Seed(
        "billing_retry",
        "WARNING [src/billing/charge.py]: Charge retries must carry the original "
        "idempotency key or customers are double charged.",
        category="fragile_area",
        importance=0.95,
    ),
    Seed(
        "migrations_offline",
        "Database migrations run offline against a maintenance replica; alembic "
        "upgrade head is never run against production directly.",
        category="convention",
    ),
    Seed(
        "search_index",
        "Search indexes rebuild nightly from the canonical postgres tables; the "
        "rebuild is not incremental and takes about forty minutes.",
        category="fact",
    ),
    Seed(
        "uploads_streaming",
        "Uploads stream directly to object storage; buffering whole files in memory "
        "caused out-of-memory kills on large imports.",
        category="gotcha",
    ),
    Seed(
        "webhooks_jitter",
        "Outbound webhooks retry with exponential backoff plus jitter, capped at six "
        "attempts over one hour.",
        category="convention",
    ),
    Seed(
        "flags_closed",
        "Feature flags default closed: an unknown flag evaluates to off, so a missing "
        "flag never enables a code path.",
        category="convention",
    ),
)


CASES: tuple[Case, ...] = (
    Case(
        case_id="auth_refresh_concurrency",
        task="Fix the concurrent session refresh race in the auth cache",
        files=("src/auth/session.py",),
        relevant=("auth_race", "auth_revert", "auth_jwt"),
    ),
    Case(
        case_id="billing_double_charge",
        task="Investigate duplicate charges when a billing retry fires",
        files=("src/billing/charge.py",),
        relevant=("billing_retry", "billing_rounding"),
    ),
    Case(
        case_id="migration_deploy",
        task="Run the pending alembic database migrations for the release",
        relevant=("migrations_offline",),
    ),
    Case(
        case_id="upload_memory",
        task="Large file uploads are killing the worker with out of memory errors",
        relevant=("uploads_streaming",),
    ),
    Case(
        case_id="webhook_backoff",
        task="Change the outbound webhook retry backoff schedule",
        relevant=("webhooks_jitter",),
    ),
    # --- Unanswerable: nothing stored can help. Silence is the correct answer. ------
    Case(
        case_id="unrelated_css",
        task="Adjust the marketing landing page hero gradient for dark mode",
    ),
    Case(
        case_id="unrelated_i18n",
        task="Add Portuguese translations to the onboarding email templates",
    ),
    Case(
        case_id="unrelated_mobile",
        task="Increase the iOS push notification badge count on new mentions",
    ),
    # --- Vague: real prompts are often this thin. Injecting here is guessing. -------
    Case(
        case_id="vague_continue",
        task="continue",
    ),
    Case(
        case_id="vague_fix",
        task="fix it",
    ),
)


@dataclass
class Outcome:
    """Aggregated scores for one selection strategy."""

    name: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    injected_chars: int = 0
    injections: int = 0
    correct_silences: int = 0
    incorrect_silences: int = 0
    false_alarms: int = 0
    silence_opportunities: int = 0

    @property
    def precision(self) -> float:
        selected = self.true_positives + self.false_positives
        return self.true_positives / selected if selected else 1.0

    @property
    def recall(self) -> float:
        available = self.true_positives + self.false_negatives
        return self.true_positives / available if available else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def mean_tokens(self) -> float:
        return (self.injected_chars / _CHARS_PER_TOKEN) / max(1, len(CASES))

    @property
    def silence_accuracy(self) -> float:
        """On tasks no memory can help, how often did the strategy stay quiet?"""
        if not self.silence_opportunities:
            return 1.0
        return self.correct_silences / self.silence_opportunities

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.name,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "mean_injected_tokens": round(self.mean_tokens, 1),
            "silence_accuracy": round(self.silence_accuracy, 4),
            "false_alarms": self.false_alarms,
            "missed_answerable": self.incorrect_silences,
        }


def _build_store(tmp: Path) -> tuple[Memory, dict[str, str]]:
    """Seed a fresh store and return it plus a memory-id -> seed-key map."""
    config = MemoryConfig()
    config.storage.data_dir = tmp / "data"
    config.embedding.provider = "noop"  # deterministic, no network
    memory = Memory(config=config)

    id_to_key: dict[str, str] = {}
    for seed in SEEDS:
        memory_id = memory.learn(
            knowledge=seed.content,
            category=seed.category,
            importance=seed.importance,
            _write_channel=WriteChannel.TEST_CAPTURE,
        )
        id_to_key[memory_id] = seed.key
    return memory, id_to_key


def _raw_candidates(memory: Memory, task: str, files: Optional[list[str]]) -> list[dict[str, Any]]:
    """Retrieve with no relevance floor at all -- the naive-retrieval baseline."""
    query = task
    if files:
        query = f"{task} {' '.join(files)}"
    return memory.recall(query=query, limit=UNFILTERED_TOP_K, min_score=0.0)


def _score(
    outcome: Outcome,
    case: Case,
    selected_keys: list[str],
    chars: int,
) -> None:
    relevant = set(case.relevant)
    selected = set(selected_keys)

    outcome.true_positives += len(selected & relevant)
    outcome.false_positives += len(selected - relevant)
    outcome.false_negatives += len(relevant - selected)
    outcome.injected_chars += chars
    if selected:
        outcome.injections += 1

    if not case.answerable:
        outcome.silence_opportunities += 1
        if selected:
            outcome.false_alarms += 1
        else:
            outcome.correct_silences += 1
    elif not selected:
        outcome.incorrect_silences += 1


def run(policy: Optional[InjectionPolicy] = None) -> dict[str, Any]:
    policy = policy or InjectionPolicy()

    with tempfile.TemporaryDirectory() as raw_tmp:
        memory, id_to_key = _build_store(Path(raw_tmp))
        corpus_size = len(SEEDS)

        oracle = Outcome("oracle")
        unfiltered = Outcome(f"unfiltered_top{UNFILTERED_TOP_K}")
        gated = Outcome("policy")
        per_case: list[dict[str, Any]] = []

        for case in CASES:
            files = list(case.files) or None
            candidates = gather_candidates(memory, task=case.task, files=files)

            # The baseline has to be genuinely unfiltered to be honest: it takes the
            # top-k by score with no floor at all, which is what a system does when it
            # "retrieves whatever looks related". Reusing the policy's own pre-filtered
            # candidate pool here would flatter the policy by comparing it to itself.
            raw = _raw_candidates(memory, case.task, files)

            # --- oracle: exactly the labelled memories -------------------------
            oracle_chars = sum(
                len(seed.content) for seed in SEEDS if seed.key in set(case.relevant)
            )
            _score(oracle, case, list(case.relevant), oracle_chars)

            # --- unfiltered: top-k, no floor, no abstention --------------------
            top = raw[:UNFILTERED_TOP_K]
            top_keys = [id_to_key.get(c.get("id"), "?") for c in top]
            top_chars = sum(len(str(c.get("content", ""))) for c in top)
            _score(unfiltered, case, top_keys, top_chars)

            # --- policy --------------------------------------------------------
            result = select_for_injection(
                candidates,
                task=case.task,
                files=files,
                corpus_size=corpus_size,
                policy=policy,
            )
            policy_keys = [id_to_key.get(m.get("id"), "?") for m in result.memories]
            _score(gated, case, policy_keys, result.chars)

            per_case.append(
                {
                    "case_id": case.case_id,
                    "answerable": case.answerable,
                    "relevant": list(case.relevant),
                    "policy_selected": policy_keys,
                    "policy_reason": result.reason,
                    "unfiltered_selected": top_keys,
                    "candidates": len(candidates),
                }
            )

    # How much of the oracle's advantage over unfiltered retrieval did the policy
    # recover? This is the headline number the module docstring motivates.
    span = oracle.f1 - unfiltered.f1
    closed = (gated.f1 - unfiltered.f1) / span if span > 1e-9 else 0.0

    return {
        "mode": "deterministic_selection_proxy",
        "cases": len(CASES),
        "answerable_cases": sum(1 for c in CASES if c.answerable),
        "corpus_size": len(SEEDS),
        "candidate_min_score": CANDIDATE_MIN_SCORE,
        "strategies": [oracle.as_dict(), unfiltered.as_dict(), gated.as_dict()],
        "oracle_gap_closed": round(closed, 4),
        "per_case": per_case,
    }


def _print_report(report: dict[str, Any]) -> None:
    print("Oracle-gap evaluation")
    print(f"Mode: {report['mode']}")
    print(
        f"Cases: {report['cases']} "
        f"({report['answerable_cases']} answerable, "
        f"{report['cases'] - report['answerable_cases']} where silence is correct)"
    )
    print(f"Corpus: {report['corpus_size']} memories")
    print()

    header = f"{'strategy':<18}{'prec':>7}{'recall':>8}{'F1':>7}{'tokens':>9}{'silence':>9}"
    print(header)
    print("-" * len(header))
    for strategy in report["strategies"]:
        print(
            f"{strategy['strategy']:<18}"
            f"{strategy['precision']:>7.2f}"
            f"{strategy['recall']:>8.2f}"
            f"{strategy['f1']:>7.2f}"
            f"{strategy['mean_injected_tokens']:>9.1f}"
            f"{strategy['silence_accuracy']:>9.2f}"
        )
    print()

    unfiltered = next(s for s in report["strategies"] if s["strategy"].startswith("unfiltered"))
    policy = next(s for s in report["strategies"] if s["strategy"] == "policy")

    print(f"Oracle gap closed: {report['oracle_gap_closed'] * 100:.0f}%")
    print(
        f"False alarms on unanswerable tasks: "
        f"unfiltered {unfiltered['false_alarms']}, policy {policy['false_alarms']}"
    )
    print(
        f"Answerable tasks missed entirely: policy {policy['missed_answerable']} "
        f"(the cost of abstaining)"
    )
    print(
        f"Mean injected tokens: unfiltered {unfiltered['mean_injected_tokens']:.1f}, "
        f"policy {policy['mean_injected_tokens']:.1f}"
    )
    print()
    print("Selection-quality proxy only: it measures whether the right memories were")
    print("chosen, not how many issues an agent then resolved.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    args = parser.parse_args()

    report = run()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

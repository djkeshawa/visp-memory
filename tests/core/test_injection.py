"""Tests for precision-gated injection.

The behaviour under test is mostly *restraint*, so most of these assert that nothing was
injected. That is the point: the failure mode this policy exists to prevent is a
confident, irrelevant memory steering the assistant.
"""

from visp_memory.core.injection import (
    InjectionPolicy,
    format_injection,
    select_for_injection,
    task_signal,
)
from visp_memory.core.trust import Provenance, provenance_tag

_SUBJECTS = [
    "alembic migrations run offline",
    "redis caches session lookups",
    "webhooks retry with jitter",
    "billing rounds half-even",
    "uploads stream to object storage",
    "cron jobs use utc timestamps",
    "feature flags default closed",
    "search indexes rebuild nightly",
    "emails queue through sendgrid",
    "metrics ship via statsd",
]


def _distinct_candidates(count, score=0.9):
    """Candidates with no meaningful term overlap, so only the budget gates them."""
    return [
        _memory(_SUBJECTS[i], score=score, memory_id=f"m{i}") for i in range(count)
    ]


def _memory(content, score=0.9, memory_id="m1", category="knowledge", tags=None):
    return {
        "id": memory_id,
        "content": content,
        "relevance_score": score,
        "category": category,
        "tags": tags if tags is not None else [provenance_tag(Provenance.DERIVED)],
    }


class TestAbstention:
    def test_no_candidates_abstains(self):
        result = select_for_injection([], task="refactor the auth token flow")
        assert result.abstained
        assert result.reason == "no candidates"

    def test_thin_corpus_suppresses_ordinary_knowledge(self):
        """A near-empty store cannot be ranked meaningfully."""
        result = select_for_injection(
            [_memory("Auth uses JWT", score=0.99)],
            task="refactor the auth token flow",
            corpus_size=2,
        )
        assert result.abstained
        assert "corpus too small" in result.reason

    def test_thin_corpus_still_surfaces_warnings(self):
        """A hand-written warning is high-precision however small the store is; gating
        these out meant a user who recorded three warnings saw none of them."""
        result = select_for_injection(
            [_memory("Race condition here", score=0.5, category="fragile_area")],
            task="refactor the auth token flow",
            corpus_size=2,
        )
        assert not result.abstained

    def test_vague_task_injects_nothing_but_warnings(self):
        candidates = [
            _memory("Auth uses JWT for stateless scaling", score=0.99, memory_id="k1"),
            _memory(
                "Race condition in the session cache",
                score=0.99,
                memory_id="w1",
                category="warning",
            ),
        ]
        # A thin corpus cannot be ranked, so only the warning survives.
        result = select_for_injection(candidates, task="refactor session auth", corpus_size=2)

        assert [m["id"] for m in result.memories] == ["w1"]
        assert result.dropped_below_floor == 1

    def test_naming_a_file_makes_a_terse_task_specific(self):
        """"fix it" against a concrete file is answerable, so knowledge is allowed."""
        candidates = [
            _memory("Auth uses JWT for stateless scaling", score=0.99, memory_id="k1"),
        ]
        result = select_for_injection(
            candidates, task="fix it", files=["src/session.py"], corpus_size=50
        )

        assert [m["id"] for m in result.memories] == ["k1"]

    def test_vague_task_with_warnings_disabled_abstains_entirely(self):
        policy = InjectionPolicy(always_allow_warnings=False)
        result = select_for_injection(
            [_memory("Race condition", score=0.99, category="warning")],
            task="continue",
            corpus_size=50,
            policy=policy,
        )
        assert result.abstained
        assert "too vague" in result.reason

    def test_below_relevance_floor_abstains(self):
        """0.55 maps to "at least half the query's terms matched"; below that is noise."""
        result = select_for_injection(
            [_memory("Loosely related note", score=0.48)],
            task="refactor the auth token flow",
            corpus_size=50,
        )
        assert result.abstained
        assert "relevance floor" in result.reason
        assert result.dropped_below_floor == 1

    def test_single_candidate_is_judged_on_the_absolute_floor(self):
        """A lone candidate is its own pool floor, so the margin rule must not apply."""
        result = select_for_injection(
            [_memory("Auth tokens are validated per request", score=0.56)],
            task="refactor the auth token flow",
            corpus_size=50,
        )
        assert not result.abstained

    def test_flat_pool_abstains(self):
        """When every candidate scores alike the ranking carries no information, so the
        top result is arbitrary -- the unfiltered-context failure mode."""
        candidates = _distinct_candidates(5, score=0.56)
        result = select_for_injection(
            candidates, task="refactor the auth token flow", corpus_size=50
        )
        assert result.abstained
        assert "not distinguishable" in result.reason

    def test_strong_matches_survive_a_flat_pool(self):
        """A high absolute score does not need the pool to agree."""
        candidates = _distinct_candidates(5, score=0.70)
        result = select_for_injection(
            candidates, task="refactor the auth token flow", corpus_size=50
        )
        assert not result.abstained

    def test_warning_categories_use_the_real_vocabulary(self):
        """The semantic layer emits fragile_area/known_issue/gotcha; there is no
        category literally named "warning"."""
        for category in ("fragile_area", "known_issue", "gotcha"):
            result = select_for_injection(
                [_memory("Race condition here", score=0.10, category=category)],
                task="fix it",
                files=["src/session.py"],
                corpus_size=50,
            )
            assert not result.abstained, f"{category} should be treated as a warning"


class TestBudget:
    def test_caps_number_of_memories(self):
        result = select_for_injection(
            _distinct_candidates(10), task="refactor the auth token flow", corpus_size=50
        )

        assert len(result.memories) == InjectionPolicy().max_memories
        assert result.dropped_over_budget == 10 - InjectionPolicy().max_memories

    def test_caps_characters(self):
        policy = InjectionPolicy(max_chars=100, max_memories=10)
        candidates = [
            _memory("x" * 60, memory_id="m1"),
            _memory("y" * 60, memory_id="m2"),
            _memory("z" * 20, memory_id="m3"),
        ]
        result = select_for_injection(
            candidates, task="refactor the auth token flow", corpus_size=50, policy=policy
        )

        assert result.chars <= policy.max_chars
        # m2 does not fit, but the smaller m3 still does: one oversized candidate must
        # not close the budget for everything after it.
        assert [m["id"] for m in result.memories] == ["m1", "m3"]

    def test_drops_redundant_candidates(self):
        candidates = [
            _memory("The auth service validates JWT tokens on every request", memory_id="m1"),
            _memory("The auth service validates JWT tokens on each request", memory_id="m2"),
            _memory("Database migrations run through alembic upgrade head", memory_id="m3"),
        ]
        result = select_for_injection(
            candidates, task="change the auth token validation", corpus_size=50
        )

        assert [m["id"] for m in result.memories] == ["m1", "m3"]
        assert result.dropped_redundant == 1

    def test_relaxed_policy_is_wider_for_explicit_requests(self):
        candidates = _distinct_candidates(8, score=0.55)
        strict = select_for_injection(candidates, task="auth token flow", corpus_size=50)
        relaxed = select_for_injection(
            candidates, task="auth token flow", corpus_size=50, policy=InjectionPolicy.relaxed()
        )

        assert strict.abstained
        assert len(relaxed.memories) == 8


class TestTaskSignal:
    def test_counts_distinct_meaningful_terms(self):
        assert task_signal("fix it") < 3
        assert task_signal("refactor the auth token validation flow") >= 3

    def test_file_paths_supply_signal(self):
        """Thin prose plus a concrete file is still a specific task."""
        assert task_signal("why this", files=["src/auth/tokens.py"]) >= 3


class TestFormatting:
    def test_abstention_renders_empty(self):
        assert format_injection(select_for_injection([], task="x")) == ""

    def test_renders_citations_and_markers(self):
        result = select_for_injection(
            [
                _memory(
                    "Race condition in session cache",
                    memory_id="abcdef1234567890",
                    category="warning",
                )
            ],
            task="refactor the auth token flow",
            corpus_size=50,
        )
        rendered = format_injection(result)

        assert "**warning**" in rendered
        assert "[abcdef12]" in rendered
        assert "Race condition in session cache" in rendered


class TestReporting:
    def test_summary_reports_abstention_reason(self):
        result = select_for_injection([], task="anything")
        assert "No memory injected" in result.summary()
        assert "no candidates" in result.summary()

    def test_summary_reports_counts_and_tokens(self):
        result = select_for_injection(
            _distinct_candidates(10), task="refactor the auth token flow", corpus_size=50
        )
        summary = result.summary()

        assert "Injected 4 of 10" in summary
        assert "withheld" in summary
        assert result.as_dict()["injected"] == 4

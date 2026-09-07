"""Published benchmark values must match the local evaluators exactly.

Measurements and their negative results live in docs/BENCHMARK.md. These tests
check the printed values, recall cost, and counts without loosening the evaluator's
independent behavioral assertions. README links readers to this verification.
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[2]

README = "README.md"
BENCHMARK = "docs/BENCHMARK.md"


def _run_eval(script: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def oracle_gap() -> dict:
    return _run_eval("evaluate_oracle_gap.py")


@pytest.fixture(scope="module")
def poisoning() -> dict:
    return _run_eval("evaluate_poisoning.py")


def _strategy(report: dict, name: str) -> dict:
    return next(s for s in report["strategies"] if s["strategy"].startswith(name))


# ---------------------------------------------------------------------------
# Reading a figure back out of a document
# ---------------------------------------------------------------------------


def _lines(document: str) -> list[str]:
    return (ROOT / document).read_text(encoding="utf-8").splitlines()


def _anchored_line(document: str, anchor: str) -> str:
    """The single line of ``document`` containing ``anchor``.

    An anchor that matches nothing, or more than one line, is itself a failure:
    the document moved under the pin and the pin can no longer say what it checks.
    """
    matches = [line for line in _lines(document) if anchor in line]
    assert matches, f"{document}: no line contains the anchor {anchor!r}"
    assert len(matches) == 1, f"{document}: anchor {anchor!r} matches {len(matches)} lines"
    return matches[0]


def _cells(document: str, anchor: str) -> list[str]:
    """Markdown table cells of the anchored row, with bold markers stripped."""
    line = _anchored_line(document, anchor)
    return [cell.replace("**", "").strip() for cell in line.strip().strip("|").split("|")]


# ---------------------------------------------------------------------------
# The published figures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Figure:
    """One number, where it is measured and everywhere it is printed."""

    label: str
    report: str  # "oracle_gap" or "poisoning"
    measure: Callable[[dict], float]
    render: Callable[[float], str]
    sites: Tuple[Tuple[str, str, int], ...]  # (document, anchor, cell index)

    def printed(self, document: str, anchor: str, index: int) -> str:
        return _cells(document, anchor)[index]


def _ratio(strategy: str, metric: str) -> Callable[[dict], float]:
    return lambda report: _strategy(report, strategy)[metric]


TWO_DP = "{:.2f}".format
THREE_DP = "{:.3f}".format
ONE_DP = "{:.1f}".format
# The poisoning proportions are exact sixteenths. 9/16 is 56.25%, which sits exactly
# on the rounding boundary at one decimal — and the docs had resolved it upward to
# 56.3%, in the direction that made the attack, and so the defence, look stronger.
# Two decimals is exact for this benchmark, so there is no boundary to resolve.
PERCENT_2 = lambda value: f"{value * 100:.2f}%"  # noqa: E731


FIGURES: Tuple[Figure, ...] = (
    Figure(
        "naive retrieval precision",
        "oracle_gap",
        _ratio("unfiltered", "precision"),
        TWO_DP,
        (
            (BENCHMARK, "| unfiltered_top8 |", 1),
        ),
    ),
    Figure(
        "policy precision",
        "oracle_gap",
        _ratio("policy", "precision"),
        TWO_DP,
        (
            (BENCHMARK, "| **policy** |", 1),
        ),
    ),
    Figure(
        "naive retrieval recall",
        "oracle_gap",
        _ratio("unfiltered", "recall"),
        TWO_DP,
        ((BENCHMARK, "| unfiltered_top8 |", 2),),
    ),
    Figure(
        # Three decimals on purpose. At two, 0.625 prints as 0.62 or 0.63
        # depending on the rounding rule, and the derived cost reads as either
        # 38% or 37%. The measurement has an exact value; publish it.
        "policy recall",
        "oracle_gap",
        _ratio("policy", "recall"),
        THREE_DP,
        ((BENCHMARK, "| **policy** |", 2),),
    ),
    Figure(
        "policy F1",
        "oracle_gap",
        _ratio("policy", "f1"),
        TWO_DP,
        ((BENCHMARK, "| **policy** |", 3),),
    ),
    Figure(
        "naive retrieval mean injected tokens",
        "oracle_gap",
        _ratio("unfiltered", "mean_injected_tokens"),
        ONE_DP,
        (
            (BENCHMARK, "| unfiltered_top8 |", 4),
        ),
    ),
    Figure(
        "policy mean injected tokens",
        "oracle_gap",
        _ratio("policy", "mean_injected_tokens"),
        ONE_DP,
        (
            (BENCHMARK, "| **policy** |", 4),
        ),
    ),
    Figure(
        "oracle mean injected tokens",
        "oracle_gap",
        _ratio("oracle", "mean_injected_tokens"),
        ONE_DP,
        ((BENCHMARK, "| oracle |", 4),),
    ),
    Figure(
        "naive retrieval correct silence",
        "oracle_gap",
        _ratio("unfiltered", "silence_accuracy"),
        TWO_DP,
        ((BENCHMARK, "| unfiltered_top8 |", 5),),
    ),
    Figure(
        "policy correct silence",
        "oracle_gap",
        _ratio("policy", "silence_accuracy"),
        TWO_DP,
        ((BENCHMARK, "| **policy** |", 5),),
    ),
    Figure(
        "undefended poisoned-memory retrieval",
        "poisoning",
        lambda report: report["undefended"]["poisoned_retrieval_proportion"],
        PERCENT_2,
        (
            (BENCHMARK, "| Undefended (relevance ranking only) |", 1),
        ),
    ),
    Figure(
        "defended poisoned-memory retrieval",
        "poisoning",
        lambda report: report["defended"]["poisoned_retrieval_proportion"],
        PERCENT_2,
        (
            (BENCHMARK, "| Defended (provenance quarantine) |", 1),
        ),
    ),
)


@pytest.mark.parametrize("figure", FIGURES, ids=lambda figure: figure.label)
def test_published_figure_matches_the_measurement(figure, oracle_gap, poisoning):
    report = {"oracle_gap": oracle_gap, "poisoning": poisoning}[figure.report]
    expected = figure.render(figure.measure(report))

    for document, anchor, index in figure.sites:
        assert figure.printed(document, anchor, index) == expected, (
            f"{document} prints {figure.printed(document, anchor, index)!r} for "
            f"{figure.label}, but the benchmark now measures {expected!r}. "
            "Update the document, or explain the regression."
        )


# ---------------------------------------------------------------------------
# The recall cost, and the habit of rounding it downhill
# ---------------------------------------------------------------------------


RECALL_COST_SITES = (
    (BENCHMARK, "**Policy recall is"),
)


def _recall_counts(report: dict) -> tuple[int, int]:
    """(relevant memories available, relevant memories the policy retrieved)."""
    available = retrieved = 0
    for case in report["per_case"]:
        relevant = set(case["relevant"])
        available += len(relevant)
        retrieved += len(relevant & set(case["policy_selected"]))
    return available, retrieved


def test_the_recall_cost_is_stated_as_a_measured_percentage(oracle_gap):
    """Not "about a third". The measurement is exact, so the prose must be."""
    policy_recall = _strategy(oracle_gap, "policy")["recall"]
    cost = f"{(1 - policy_recall) * 100:.1f}%"

    for document, anchor in RECALL_COST_SITES:
        line = _anchored_line(document, anchor)
        # The claim may wrap, so read the sentence, not just the anchored line.
        lines = _lines(document)
        sentence = " ".join(lines[lines.index(line) : lines.index(line) + 4])
        assert cost in sentence, (
            f"{document} states the recall cost without the measured figure {cost}. "
            f"Got: {sentence.strip()[:200]!r}"
        )


def test_the_recall_cost_is_not_softened_into_a_vague_fraction(oracle_gap):
    """The specific habit: 37.5% published three times as "about a third".

    Rounding down to the nearest comfortable fraction always moved the number in
    the direction that flattered the project. A vague fraction anywhere near the
    recall claim is the shape to catch, whichever way it happens to round.
    """
    vague = re.compile(
        r"\b(?:about|roughly|around|approximately|nearly|almost|some|close to|just)\s+"
        r"(?:a\s+)?(?:third|half|quarter|two[- ]fifths|two[- ]thirds|three[- ]quarters)\b",
        re.IGNORECASE,
    )

    for document, anchor in RECALL_COST_SITES:
        lines = _lines(document)
        start = next(i for i, line in enumerate(lines) if anchor in line)
        sentence = " ".join(lines[start : start + 4])
        match = vague.search(sentence)
        assert match is None, (
            f"{document}: the recall cost is a measured number, published here as "
            f"{match.group(0)!r}. Print the figure the benchmark produces."
        )


def test_the_published_counts_are_the_counts_the_benchmark_used(oracle_gap):
    """ "5 of the 8" is checkable in a way "a third" never was, so check it."""
    available, retrieved = _recall_counts(oracle_gap)
    missed = available - retrieved

    assert retrieved / available == pytest.approx(_strategy(oracle_gap, "policy")["recall"])

    benchmark = " ".join(_lines(BENCHMARK))
    assert f"retrieved {retrieved} of the {available} genuinely relevant" in benchmark
    assert f"left {missed} on the floor" in benchmark


def test_the_oracle_gap_headline_matches_the_report(oracle_gap):
    closed = f"{oracle_gap['oracle_gap_closed'] * 100:.0f}%"

    assert f"**Oracle gap closed: {closed}.**" in _lines(BENCHMARK)


def test_the_token_ratio_headline_matches_the_report(oracle_gap):
    policy = _strategy(oracle_gap, "policy")["mean_injected_tokens"]
    unfiltered = _strategy(oracle_gap, "unfiltered")["mean_injected_tokens"]
    ratio = f"{unfiltered / policy:.1f}×"

    line = _anchored_line(BENCHMARK, "fewer tokens** than naive retrieval")
    assert ratio in line, f"docs/BENCHMARK.md claims a token ratio other than {ratio}: {line!r}"


# ---------------------------------------------------------------------------
# The claim about the claims
# ---------------------------------------------------------------------------


def test_the_readme_does_not_promise_more_than_this_file_delivers():
    """If the reproducibility sentence is ever widened, widen the pin with it."""
    readme = " ".join(_lines(README))

    assert "tests/docs/test_published_figures.py" in readme, (
        "README's 'Measured, not asserted' claim must name the check that makes it "
        "true, so a reader can go and read the check."
    )
    assert "reproduced in CI on each push. See" not in readme, (
        "the old wording claimed reproduction while CI only reran the scripts"
    )

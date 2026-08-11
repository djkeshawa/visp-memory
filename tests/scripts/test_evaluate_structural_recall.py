"""The structural-recall benchmark, pinned so a change to it is visible in a diff.

The exact figures are asserted rather than described. A claim in prose beside a number
nobody re-derives is how this project accumulated seven claims that disk contradicted;
if the mechanism moves these numbers, the test fails and the report is rewritten from
the new ones.
"""

from scripts.evaluate_structural_recall import evaluate


def test_the_benchmark_meets_its_preregistered_expectations():
    metrics = evaluate()

    assert metrics["passed"] is True
    assert metrics["expectations"] == {
        "recall_improves": True,
        "correct_adjacency_beats_a_wrong_one": True,
        "no_task_regresses": True,
        "seed_error_never_removes": True,
        "inert_cohort_unmoved": True,
        "deterministic": True,
        "ceiling_is_visible": True,
    }


def test_the_measured_figures_are_the_ones_reported():
    metrics = evaluate()
    attribution = metrics["attribution"]

    assert metrics["arm_a_no_graph"]["recall_at_10"] == 0.4688
    assert metrics["arm_b_graph"]["recall_at_10"] == 0.7917
    assert metrics["arm_c_misleading_graph"]["recall_at_10"] == 0.625

    # The honest split of the headline gap. A wrong graph buys most of half of it, and
    # only the remainder is attributable to the adjacency being correct.
    assert attribution["any_three_extra_memories"] == 0.1562
    assert attribution["correct_adjacency"] == 0.1667
    assert attribution["admitted_precision_true_graph"] == 0.5
    assert attribution["admitted_precision_misleading_graph"] == 0.2222


def test_the_cost_is_reported_and_not_zero():
    """Recall bought at an unreported cost is not a result."""
    metrics = evaluate()

    assert metrics["arm_b_graph"]["precision_at_5"] < metrics["arm_a_no_graph"]["precision_at_5"]
    assert metrics["arm_b_graph"]["precision_at_5"] == 0.6
    assert metrics["cost"]["mean_results_after"] > metrics["cost"]["mean_results_before"]


def test_the_inert_cohort_is_split_out_and_did_not_move():
    """Two of eight tasks seed from isolated files; a cohort mean would hide them."""
    metrics = evaluate()

    before = metrics["arm_a_no_graph"]["split"]
    after = metrics["arm_b_graph"]["split"]

    assert before["inert"]["tasks"] == 2
    assert after["inert"] == before["inert"]
    assert after["connected"]["recall_at_10"] > before["connected"]["recall_at_10"]


def test_ranking_order_never_inverts():
    """No structural admission outranks an identity match, so MRR cannot move."""
    metrics = evaluate()

    assert metrics["arm_b_graph"]["mrr"] == metrics["arm_a_no_graph"]["mrr"]
    assert metrics["arm_c_misleading_graph"]["mrr"] == metrics["arm_a_no_graph"]["mrr"]

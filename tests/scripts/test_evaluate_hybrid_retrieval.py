from scripts.evaluate_hybrid_retrieval import evaluate


def test_hybrid_retrieval_evaluation_passes():
    metrics = evaluate()

    assert metrics["passed"] is True
    assert metrics["hybrid_recall_at_10"] > metrics["direct_recall_at_10"]
    assert metrics["multi_hop_recovered"] is True
    assert metrics["hub_did_not_override_seed"] is True

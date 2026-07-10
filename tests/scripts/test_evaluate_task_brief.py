from scripts.evaluate_task_brief import evaluate


def test_task_brief_evaluation_meets_quality_and_efficiency_floor():
    result = evaluate()
    assert result["passed"] is True
    assert result["precision"] >= 0.95
    assert result["recall"] >= 0.95
    assert result["token_savings_ratio"] >= 0.2
    assert result["contradictions_recovered"] == 1
    assert result["weak_evidence_abstained"] is True

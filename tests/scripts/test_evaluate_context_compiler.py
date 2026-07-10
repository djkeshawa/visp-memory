from scripts.evaluate_context_compiler import evaluate


def test_context_compiler_evaluation_meets_accuracy_and_savings_floor():
    result = evaluate()
    assert result["passed"] is True
    assert result["precision"] >= 0.95
    assert result["recall"] >= 0.95
    assert result["token_savings_ratio"] >= 0.2

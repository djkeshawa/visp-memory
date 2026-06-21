"""Tests for deterministic token accounting."""

from llm_memory.core.tokens import (
    TokenSavings,
    compute_savings,
    estimate_memory_tokens,
    estimate_tokens,
    estimate_total_tokens,
)


def test_estimate_tokens_empty_and_none():
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0


def test_estimate_tokens_is_positive_and_monotonic():
    short = estimate_tokens("auth token refresh")
    longer = estimate_tokens("auth token refresh " * 20)
    assert short >= 1
    assert longer > short


def test_estimate_tokens_is_deterministic():
    text = "Use JWT tokens because stateless scaling is needed."
    assert estimate_tokens(text) == estimate_tokens(text)


def test_estimate_tokens_coerces_non_strings():
    assert estimate_tokens(12345) >= 1


def test_estimate_memory_tokens_reads_content_field():
    memory = {"id": "a", "content": "database migration completed without downtime"}
    assert estimate_memory_tokens(memory) == estimate_tokens(memory["content"])


def test_estimate_total_tokens_mixes_strings_and_memories():
    total = estimate_total_tokens(
        ["plain string", {"id": "b", "content": "a memory mapping"}]
    )
    expected = estimate_tokens("plain string") + estimate_tokens("a memory mapping")
    assert total == expected


def test_compute_savings_basic():
    source = ["bug in auth flow", "auth flow race condition", "auth retry storm"]
    result = "Auth flow is fragile under concurrency; guard with a mutex."
    savings = compute_savings(source, result)

    assert isinstance(savings, TokenSavings)
    assert savings.source_tokens == estimate_total_tokens(source)
    assert savings.result_tokens == estimate_tokens(result)
    assert savings.saved_tokens == max(
        0, savings.source_tokens - savings.result_tokens
    )
    assert 0.0 <= savings.ratio <= 1.0


def test_compute_savings_never_negative():
    # Result larger than source must not produce negative savings.
    savings = compute_savings("tiny", "a much longer compressed result than the source")
    assert savings.saved_tokens == 0
    assert savings.ratio == 0.0


def test_compute_savings_ratio_for_zero_source():
    savings = compute_savings("", "anything")
    assert savings.source_tokens == 0
    assert savings.ratio == 0.0


def test_token_savings_as_dict_roundtrip():
    savings = compute_savings(["a a a a", "b b b b"], "c")
    payload = savings.as_dict()
    assert set(payload) == {"source_tokens", "result_tokens", "saved_tokens", "ratio"}
    assert payload["saved_tokens"] == savings.saved_tokens

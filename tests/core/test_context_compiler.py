from llm_memory.core.context_compiler import ContextCompiler
from llm_memory.core.storage import LocalStorage


def test_context_compiler_filters_temporal_facts_and_returns_deltas(tmp_path):
    storage = LocalStorage(tmp_path)
    current = storage.store_memory(
        "Authentication uses secure session cookies",
        layer="semantic",
        repo_id="repo-a",
        metadata={
            "files": ["src/auth.py"],
            "symbols": ["login"],
            "confidence": 0.95,
            "valid_from": "2025-01-01T00:00:00+00:00",
        },
        auto_link=False,
    )
    expired = storage.store_memory(
        "Authentication uses local storage tokens",
        layer="semantic",
        repo_id="repo-a",
        metadata={
            "files": ["src/auth.py"],
            "confidence": 0.9,
            "valid_to": "2024-01-01T00:00:00+00:00",
        },
        auto_link=False,
    )
    storage.add_relationship(current, expired, "contradicts")

    compiler = ContextCompiler(storage)
    result = compiler.compile(
        "How does authentication work?",
        repo_id="repo-a",
        files=["src/auth.py"],
        symbols=["login"],
        as_of="2026-01-01T00:00:00+00:00",
        token_budget=200,
    )

    assert [item["id"] for item in result["items"]] == [current]
    assert result["token_count"] <= 200
    assert result["fingerprint"]

    unchanged = compiler.compile(
        "How does authentication work?",
        repo_id="repo-a",
        files=["src/auth.py"],
        symbols=["login"],
        as_of="2026-01-01T00:00:00+00:00",
        token_budget=200,
        previous_fingerprint=result["fingerprint"],
    )
    assert unchanged["unchanged"] is True
    assert unchanged["items"] == []
    assert unchanged["token_count"] == 0

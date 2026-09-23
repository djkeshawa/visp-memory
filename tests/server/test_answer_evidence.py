"""Display previews must not be the model's evidence budget."""

from types import SimpleNamespace

import pytest

from visp_memory.core.answer_prompt import ANSWER_SYSTEM_PROMPT
from visp_memory.core.tokens import estimate_tokens
from visp_memory.server.app import app
from visp_memory.server.routers.ai import (
    ANSWER_EVIDENCE_TOKEN_BUDGET,
    _answer_evidence,
    _evidence_text,
)


@pytest.mark.asyncio
async def test_answer_reader_receives_selected_fact_beyond_preview(client):
    captured = []
    fact = "Invoice IN-2718 total is $417."
    content = "Invoice archive background and billing information. " * 15 + fact
    mid = app.state.storage.store_memory(content, repo_id="repo-a",
                                         tags=["provenance:authored"], auto_link=False)

    def complete(task, prompt, **kwargs):
        captured.append(prompt)
        return {"text": f"$417 [{mid}]", "provider": "stub", "model": "stub"}

    app.state.model_router = SimpleNamespace(configured=True, complete=complete)
    response = await client.post("/ai/ask", json={
        "query": "What is invoice IN-2718 total?", "repo_id": "repo-a",
    }, headers={"X-API-KEY": "test_key"})
    assert response.status_code == 200
    assert response.json()["mode"] == "generated"
    assert len(captured) == 1
    assert fact in captured[0]
    assert len(response.json()["citations"][0]["snippet"]) <= 220


@pytest.mark.asyncio
async def test_reader_context_retains_combined_scope_and_trust_filters(client):
    captured = []
    for text, repo, scope, provenance in [
        ("Invoice total is $417.", "repo-a", "prod", "authored"),
        ("Invoice secret other repo $999.", "repo-b", "prod", "authored"),
        ("Invoice dev-only $888.", "repo-a", "dev", "authored"),
        ("Invoice untrusted $777.", "repo-a", "prod", "external"),
    ]:
        app.state.storage.store_memory(
            text, repo_id=repo, tags=[f"provenance:{provenance}"],
            metadata={"environment": [scope], "task_type": ["billing"]}, auto_link=False,
        )

    def complete(task, prompt, **kwargs):
        captured.append(prompt)
        return {"text": "test", "provider": "stub", "model": "stub"}

    app.state.model_router = SimpleNamespace(configured=True, complete=complete)
    response = await client.post("/ai/ask", json={
        "query": "Invoice total", "repo_id": "repo-a", "environment": "prod",
        "task_type": "billing", "limit": 20,
    }, headers={"X-API-KEY": "test_key"})
    assert response.status_code == 200
    assert len(captured) == 1
    assert "$417" in captured[0]
    assert all(value not in captured[0] for value in ("$999", "$888", "$777"))


def test_answer_evidence_obeys_budget_without_clipping_atomic_fact():
    rows = [{"id": str(i), "content": "Invoice details " * 2000 + f"total ${i}.",
             "relevance_score": .9} for i in range(20)]
    selected = _answer_evidence(rows, "Invoice total")
    assert estimate_tokens(_evidence_text(selected)) <= ANSWER_EVIDENCE_TOKEN_BUDGET
    # A single oversized sentence cannot be made safe by chopping off its ending.
    assert selected == []


@pytest.mark.asyncio
async def test_reader_gets_shared_instructions_and_the_answer_date(client):
    captured = []
    app.state.storage.store_memory("Invoice total is $417.", repo_id="repo-a",
                                   tags=["provenance:authored"], auto_link=False)

    def complete(task, prompt, **kwargs):
        captured.append((prompt, kwargs.get("system_prompt")))
        return {"text": "$417", "provider": "stub", "model": "stub"}

    app.state.model_router = SimpleNamespace(configured=True, complete=complete)
    response = await client.post("/ai/ask", json={
        "query": "What was the invoice total last week?", "repo_id": "repo-a",
        "as_of": "2026-03-04T10:00:00Z",
    }, headers={"X-API-KEY": "test_key"})
    assert response.status_code == 200
    prompt, system_prompt = captured[0]
    assert system_prompt == ANSWER_SYSTEM_PROMPT
    # Without the reference date the reader cannot resolve "last week".
    assert prompt.startswith("Question date: 2026-03-04\n")
    assert "$417" in prompt

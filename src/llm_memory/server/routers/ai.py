from fastapi import APIRouter, Depends, Request

from llm_memory.config import load_config
from llm_memory.core.ranking import rank_memory_results
from llm_memory.server.auth import UserContext, get_current_user
from llm_memory.server.authorization import can_access_scoped_record, require_repo_scope_access
from llm_memory.server.schemas import AskMemoryCitation, AskMemoryRequest, AskMemoryResponse

router = APIRouter(prefix="/ai", tags=["ai"])


def _snippet(content: str, max_length: int = 220) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3]}..."


def _retrieval_answer(query: str, citations: list[AskMemoryCitation]) -> str:
    if not citations:
        return f"No active memories matched: {query}"

    lines = ["I found relevant memory context:"]
    for citation in citations:
        lines.append(f"- [{citation.memory_id}] {citation.snippet}")
    return "\n".join(lines)


@router.post("/ask", response_model=AskMemoryResponse)
async def ask_memory(
    request: Request,
    payload: AskMemoryRequest,
    user: UserContext = Depends(get_current_user),
):
    """Answer from scoped memory retrieval with exact citations."""
    storage = request.app.state.storage
    config = load_config()
    repo_id = payload.repo_id or config.repo_id
    require_repo_scope_access(storage, repo_id, user)

    layers = payload.layers or ["episodic", "semantic", "intent"]
    results = []
    for layer in layers:
        layer_results = storage.search_memories(
            query=payload.query,
            layer=layer,
            repo_id=repo_id,
            category=payload.category,
            limit=payload.limit,
            status="active",
        )
        results.extend(
            result
            for result in layer_results
            if can_access_scoped_record(storage, result, user, scope_field="metadata")
        )

    ranked = rank_memory_results(results, query=payload.query, limit=payload.limit)
    citations = [
        AskMemoryCitation(
            memory_id=result["id"],
            snippet=_snippet(result["content"]),
            layer=result["layer"],
            category=result.get("category"),
            repo_id=result.get("repo_id"),
            relevance_score=result.get("relevance_score") or result.get("similarity"),
        )
        for result in ranked
    ]

    return AskMemoryResponse(
        answer=_retrieval_answer(payload.query, citations),
        citations=citations,
        mode="retrieval_only",
        provider_status="not_configured",
    )

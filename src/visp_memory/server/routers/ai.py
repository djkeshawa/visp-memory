from fastapi import APIRouter, Depends, HTTPException, Request, status

from visp_memory.config import load_config
from visp_memory.core.eligibility import filter_recall_eligible
from visp_memory.core.model_router import ModelUnavailableError
from visp_memory.core.ranking import rank_memory_results
from visp_memory.core.reflection import ReflectionEngine
from visp_memory.core.trust import filter_unsolicited
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.authorization import (
    can_access_scoped_record,
    require_admin,
    require_repo_scope_access,
    require_repo_writable,
)
from visp_memory.server.schemas import (
    AskMemoryCitation,
    AskMemoryRequest,
    AskMemoryResponse,
    ReflectionCreateRequest,
)

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

    eligible = filter_recall_eligible(
        results,
        repo_id=repo_id,
        environment=payload.environment,
        task_type=payload.task_type,
        as_of=payload.as_of,
    )
    guarded = filter_unsolicited(eligible.allowed, now=payload.as_of)
    ranked = rank_memory_results(
        guarded.allowed, query=payload.query, limit=payload.limit
    )
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

    router = request.app.state.model_router
    if not router.configured or not citations:
        return AskMemoryResponse(
            answer=_retrieval_answer(payload.query, citations),
            citations=citations,
            mode="retrieval_only",
            provider_status="not_configured",
        )

    context = "\n".join(
        f"[{citation.memory_id}] {citation.snippet}" for citation in citations
    )
    try:
        generated = router.complete(
            "answer",
            f"Question: {payload.query}\n\nMemory evidence:\n{context}",
            system_prompt=(
                "Answer only from the supplied memory evidence. Cite supporting memory IDs "
                "in square brackets. If evidence is insufficient, say so clearly."
            ),
        )
    except (ModelUnavailableError, ValueError, RuntimeError):
        return AskMemoryResponse(
            answer=_retrieval_answer(payload.query, citations),
            citations=citations,
            mode="retrieval_only",
            provider_status="failed",
        )
    return AskMemoryResponse(
        answer=generated["text"],
        citations=citations,
        mode="generated",
        provider_status="available",
        provider=generated["provider"],
        model=generated["model"],
    )


@router.get("/routing")
async def model_routing_status(
    request: Request,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    return request.app.state.model_router.status()


@router.post("/test")
async def test_model_route(
    request: Request,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    try:
        result = request.app.state.model_router.complete(
            "answer",
            "Reply with exactly: ready",
            system_prompt="This is a connectivity test.",
        )
    except ModelUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return {"status": "connected", **result}


@router.get("/reflections")
async def preview_reflections(
    request: Request,
    repo_id: str,
    min_evidence: int = 3,
    user: UserContext = Depends(get_current_user),
):
    require_repo_scope_access(request.app.state.storage, repo_id, user)
    proposals = ReflectionEngine(
        request.app.state.storage, request.app.state.model_router
    ).preview(repo_id, min_evidence=max(2, min(min_evidence, 20)))
    if user.is_admin:
        return proposals
    return [
        proposal
        for proposal in proposals
        if all(
            can_access_scoped_record(
                request.app.state.storage,
                request.app.state.storage.get_memory(memory_id),
                user,
                scope_field="metadata",
            )
            for memory_id in proposal["evidence_ids"]
        )
    ]


@router.post("/reflections")
async def materialize_reflection(
    request: Request,
    payload: ReflectionCreateRequest,
    user: UserContext = Depends(get_current_user),
):
    require_repo_writable(request.app.state.storage, payload.repo_id, user)
    if not payload.reviewed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reflection proposals must be reviewed before materialization",
        )
    for memory_id in payload.evidence_ids:
        memory = request.app.state.storage.get_memory(memory_id)
        if not memory or not can_access_scoped_record(
            request.app.state.storage, memory, user, scope_field="metadata"
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    try:
        return ReflectionEngine(
            request.app.state.storage, request.app.state.model_router
        ).materialize(
            repo_id=payload.repo_id,
            title=payload.title,
            evidence_ids=payload.evidence_ids,
            actor_id=user.user_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

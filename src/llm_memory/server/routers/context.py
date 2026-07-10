"""Token-budgeted context compilation endpoints."""

from fastapi import APIRouter, Depends, Request

from llm_memory.config import load_config
from llm_memory.core.context_compiler import ContextCompiler
from llm_memory.core.task_brief import TaskMemoryBriefCompiler
from llm_memory.server.auth import UserContext, get_current_user
from llm_memory.server.authorization import can_access_scoped_record, require_repo_scope_access
from llm_memory.server.schemas import ContextCompileRequest, TaskMemoryBriefRequest

router = APIRouter(prefix="/context", tags=["context"])


@router.post("/compile")
async def compile_context(
    request: Request,
    payload: ContextCompileRequest,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    config = load_config()
    repo_id = payload.repo_id or config.repo_id
    require_repo_scope_access(storage, repo_id, user)
    compiler = ContextCompiler(storage)
    return compiler.compile(
        payload.query,
        repo_id=repo_id,
        token_budget=payload.token_budget,
        as_of=payload.as_of,
        files=payload.files,
        symbols=payload.symbols,
        previous_fingerprint=payload.previous_fingerprint,
        min_confidence=payload.min_confidence,
        memory_filter=lambda memory: can_access_scoped_record(
            storage, memory, user, scope_field="metadata"
        ),
    )


@router.post("/brief")
async def prepare_task_brief(
    request: Request,
    payload: TaskMemoryBriefRequest,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    config = load_config()
    repo_id = payload.repo_id or config.repo_id
    require_repo_scope_access(storage, repo_id, user)
    compiler = TaskMemoryBriefCompiler(storage)
    return compiler.prepare(
        payload.task,
        repo_id=repo_id,
        token_budget=payload.token_budget,
        as_of=payload.as_of,
        files=payload.files,
        symbols=payload.symbols,
        intent_id=payload.intent_id,
        constraints=payload.constraints,
        previous_fingerprint=payload.previous_fingerprint,
        min_confidence=payload.min_confidence,
        memory_filter=lambda memory: can_access_scoped_record(
            storage, memory, user, scope_field="metadata"
        ),
    )

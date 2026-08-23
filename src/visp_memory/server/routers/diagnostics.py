import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from visp_memory.config import EmbeddingConfig, load_config
from visp_memory.core.clock import utc_now
from visp_memory.core.embedding_status import (
    DISABLED_STATUS_MESSAGE,
    ENABLE_SEMANTIC_RECALL_REMEDIATION,
)
from visp_memory.core.indexing import (
    ReindexScope,
    inspect_embedding_index,
    rebuild_embedding_index,
    to_plain_dict,
)
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.authorization import require_admin
from visp_memory.server.routers.platform import append_audit_event
from visp_memory.server.schemas import (
    EmbeddingIndexStatus,
    EmbeddingReindexRequest,
    EmbeddingReindexResponse,
    ProviderDiagnosticsResponse,
    ProviderStatus,
    StorageDiagnosticsResponse,
)

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])

PROVIDER_NAMES = ["openrouter", "openai", "ollama", "sentence-transformers", "noop"]
DEFAULT_PROVIDER_MODELS = {
    "openrouter": "openai/text-embedding-3-small",
    "openai": "text-embedding-3-small",
    "ollama": "nomic-embed-text",
    "sentence-transformers": "all-MiniLM-L6-v2",
    "noop": None,
}


@router.get("/storage", response_model=StorageDiagnosticsResponse)
async def get_storage_diagnostics(
    request: Request,
    user: UserContext = Depends(get_current_user),
):
    """Return non-secret backend capabilities and schema compatibility."""
    del user
    storage = request.app.state.storage
    return StorageDiagnosticsResponse(
        backend=request.app.state.storage_backend,
        capabilities=storage.get_capabilities().to_dict(),
        schema_status=storage.get_schema_status(),
    )


def _configured_provider(config_provider: str) -> str:
    return "noop" if config_provider == "none" else config_provider


def _provider_model(config: EmbeddingConfig, provider: str) -> Optional[str]:
    if provider == "noop":
        return None

    configured = _configured_provider(config.provider.lower())
    if provider == configured and config.model:
        if provider == "openrouter" and config.model in {
            "text-embedding-3-small",
            "text-embedding-3-large",
            "text-embedding-ada-002",
        }:
            return f"openai/{config.model}"
        if (
            provider != "sentence-transformers"
            and config.model == DEFAULT_PROVIDER_MODELS["sentence-transformers"]
        ):
            return DEFAULT_PROVIDER_MODELS.get(provider)
        return config.model

    return DEFAULT_PROVIDER_MODELS.get(provider)


def _openrouter_api_base(config: EmbeddingConfig) -> str:
    return config.api_base or os.getenv("EMBEDDING_API_BASE") or "https://openrouter.ai/api/v1"


def _has_provider_config(config: EmbeddingConfig, provider: str) -> bool:
    configured = _configured_provider(config.provider.lower())
    if provider == "openrouter":
        return bool(os.getenv("OPENROUTER_API_KEY")) or (
            bool(config.api_key or os.getenv("EMBEDDING_API_KEY"))
            and "openrouter" in _openrouter_api_base(config).lower()
        )
    if provider == "openai":
        return bool(config.api_key or os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY"))
    if provider == "ollama":
        return configured == provider or bool(
            config.api_base or os.getenv("EMBEDDING_API_BASE") or os.getenv("OLLAMA_HOST")
        )
    if provider == "sentence-transformers":
        return configured == provider
    if provider == "noop":
        return configured == provider
    return configured == provider


def _missing_config_message(provider: str) -> str:
    if provider == "openrouter":
        return "OpenRouter is not configured. Set OPENROUTER_API_KEY."
    if provider == "openai":
        return "OpenAI is not configured. Set OPENAI_API_KEY or EMBEDDING_API_KEY."
    if provider == "ollama":
        return "Ollama is not configured. Set OLLAMA_HOST or select the Ollama provider."
    if provider == "sentence-transformers":
        return "Local sentence-transformers is not selected for server embeddings."
    return "Noop embeddings are not selected."


def _missing_config_hint(provider: str) -> Optional[str]:
    if provider == "openrouter":
        return "Set OPENROUTER_API_KEY and VISP_MEMORY_EMBEDDING_PROVIDER=openrouter or cloud."
    if provider == "openai":
        return "Set OPENAI_API_KEY and VISP_MEMORY_EMBEDDING_PROVIDER=openai."
    if provider == "ollama":
        return "Start Ollama, pull an embedding model, and set OLLAMA_HOST."
    if provider in {"noop", "sentence-transformers"}:
        # The noop row is the one a dashboard or CLI sees marked active when
        # auto-selection fell through, and it used to be the only row with no
        # repair on it. Same sentence the CLI prints.
        return ENABLE_SEMANTIC_RECALL_REMEDIATION
    return None


def _embedding_config_for_provider(config: EmbeddingConfig, provider: str) -> EmbeddingConfig:
    provider_config = config.model_copy(deep=True)
    provider_config.provider = "noop" if provider == "noop" else provider
    provider_config.model = _provider_model(config, provider) or config.model
    return provider_config


def _status_from_error(provider: str, error: Exception) -> tuple[str, str]:
    status_code = getattr(error, "status_code", None)
    provider_label = provider.replace("-", " ").title()
    if status_code == 401:
        return "HTTP 401", f"{provider_label} rejected the configured credentials."
    if status_code == 403:
        return "HTTP 403", f"{provider_label} denied access for the configured credentials."
    return error.__class__.__name__, f"{provider} failed to connect."


def build_provider_status(
    provider: str,
    *,
    config: EmbeddingConfig,
    effective_provider: Optional[str] = None,
    runtime_status: Optional[dict] = None,
    embedding_provider=None,
) -> ProviderStatus:
    configured = _has_provider_config(config, provider)
    configured_provider = _configured_provider(config.provider.lower())
    selected = configured_provider == provider
    active = effective_provider == provider or selected

    if active and runtime_status:
        driver_status = runtime_status.get("embedding_driver_status", "not_checked")
        connected = bool(runtime_status.get("embedding_driver_connected"))
        return ProviderStatus(
            provider=provider,
            configured=configured,
            selected=selected,
            active=active,
            connected=connected,
            status=driver_status,
            model=getattr(embedding_provider, "model", None) or _provider_model(config, provider),
            dimension=getattr(embedding_provider, "dimension", None),
            last_checked_at=runtime_status.get("embedding_last_checked_at"),
            error_code=runtime_status.get("embedding_connection_error"),
            message=runtime_status.get("embedding_status_message")
            or ("Provider connected." if connected else "Provider is not connected."),
            action_hint=None if connected else _missing_config_hint(provider),
        )

    if provider == "noop" and selected:
        return ProviderStatus(
            provider=provider,
            configured=True,
            selected=True,
            active=True,
            connected=False,
            status="disabled",
            message=DISABLED_STATUS_MESSAGE,
        )

    if not configured:
        return ProviderStatus(
            provider=provider,
            configured=False,
            selected=selected,
            active=active,
            connected=False,
            status="not_configured",
            model=_provider_model(config, provider),
            message=_missing_config_message(provider),
            action_hint=_missing_config_hint(provider),
        )

    return ProviderStatus(
        provider=provider,
        configured=True,
        selected=selected,
        active=active,
        connected=False,
        status="not_checked",
        model=_provider_model(config, provider),
        message=f"{provider} has configuration values but has not been tested in this session.",
        action_hint=f"Use the test action to verify {provider}.",
    )


@router.get("/providers", response_model=ProviderDiagnosticsResponse)
async def list_provider_diagnostics(
    request: Request, user: UserContext = Depends(get_current_user)
):
    """Return non-secret provider diagnostics for the dashboard and CLI."""
    del user
    runtime_config = load_config()
    embedding_provider = getattr(request.app.state, "embedding_provider", None)
    runtime_status = getattr(request.app.state, "embedding_runtime_status", None)
    effective_provider = getattr(embedding_provider, "provider_name", None)
    providers = [
        build_provider_status(
            provider,
            config=runtime_config.embedding,
            effective_provider=effective_provider,
            runtime_status=runtime_status,
            embedding_provider=embedding_provider if effective_provider == provider else None,
        )
        for provider in PROVIDER_NAMES
    ]

    return ProviderDiagnosticsResponse(
        active_provider=runtime_config.embedding.provider,
        effective_provider=effective_provider,
        providers=providers,
    )


@router.post("/providers/{provider}/test", response_model=ProviderStatus)
async def test_provider_connection(
    provider: str,
    user: UserContext = Depends(get_current_user),
):
    """Perform a live non-secret provider connection test."""
    # Live connection tests consume provider resources and probe outbound
    # connectivity; restrict to administrators.
    require_admin(user)
    provider = provider.lower()
    if provider not in PROVIDER_NAMES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown provider: {provider}",
        )

    runtime_config = load_config()
    configured = _has_provider_config(runtime_config.embedding, provider)
    selected = _configured_provider(runtime_config.embedding.provider.lower()) == provider

    if provider == "noop":
        return ProviderStatus(
            provider=provider,
            configured=selected,
            selected=selected,
            active=selected,
            connected=False,
            status="disabled",
            last_checked_at=utc_now(),
            message="Noop embeddings do not require a connection.",
        )

    if not configured:
        return ProviderStatus(
            provider=provider,
            configured=False,
            selected=selected,
            active=False,
            connected=False,
            status="not_configured",
            model=_provider_model(runtime_config.embedding, provider),
            last_checked_at=utc_now(),
            message=_missing_config_message(provider),
            action_hint=_missing_config_hint(provider),
        )

    try:
        from visp_memory.core.embeddings import get_embedding_provider

        provider_config = _embedding_config_for_provider(runtime_config.embedding, provider)
        connected_provider = get_embedding_provider(provider_config, verify=True)
        return ProviderStatus(
            provider=provider,
            configured=True,
            selected=selected,
            active=False,
            connected=True,
            status="connected",
            model=getattr(connected_provider, "model", None)
            or _provider_model(provider_config, provider),
            dimension=getattr(connected_provider, "dimension", None),
            last_checked_at=utc_now(),
            message=f"{provider} connected successfully.",
        )
    except Exception as error:
        error_code, message = _status_from_error(provider, error)
        return ProviderStatus(
            provider=provider,
            configured=True,
            selected=selected,
            active=False,
            connected=False,
            status="failed",
            model=_provider_model(runtime_config.embedding, provider),
            last_checked_at=utc_now(),
            error_code=error_code,
            message=message,
            action_hint=_missing_config_hint(provider),
        )


@router.get("/embedding-index", response_model=EmbeddingIndexStatus)
async def get_embedding_index_status(
    request: Request,
    repo_id: str = None,
    layer: str = None,
    category: str = None,
    user: UserContext = Depends(get_current_user),
):
    """Return the current embedding/index compatibility summary."""
    del user
    runtime_config = load_config()
    storage = request.app.state.storage
    embedding_provider = getattr(request.app.state, "embedding_provider", None)
    effective_provider = getattr(embedding_provider, "provider_name", None)
    dimension = getattr(embedding_provider, "dimension", None)
    storage_backend = getattr(request.app.state, "storage_backend", runtime_config.storage.backend)
    report = inspect_embedding_index(
        storage,
        storage_backend=storage_backend,
        provider=runtime_config.embedding.provider,
        effective_provider=effective_provider,
        model=getattr(embedding_provider, "model", None) or runtime_config.embedding.model,
        dimension=dimension,
        scope=ReindexScope(repo_id=repo_id, layer=layer, category=category),
    )
    return EmbeddingIndexStatus(**to_plain_dict(report))


@router.post("/embedding-index/reindex", response_model=EmbeddingReindexResponse)
async def reindex_embedding_index(
    request: Request,
    payload: EmbeddingReindexRequest,
    user: UserContext = Depends(get_current_user),
):
    """Dry-run or rebuild active embedding vectors for a scoped set of memories."""
    # Rebuilding the embedding index mutates stored vectors; require an
    # administrator (a dry-run is also gated since it scans all matched rows).
    require_admin(user)
    result = rebuild_embedding_index(
        request.app.state.storage,
        scope=ReindexScope(
            repo_id=payload.repo_id,
            layer=payload.layer,
            category=payload.category,
        ),
        dry_run=payload.dry_run,
    )
    event_type = (
        "embedding_reindex.dry_run"
        if payload.dry_run
        else f"embedding_reindex.{result.status}"
    )
    append_audit_event(
        request.app.state.storage,
        event_type=event_type,
        actor_id=user.user_id,
        repo_id=payload.repo_id,
        target_type="embedding_index",
        metadata={
            "scope": result.scope,
            "matched_memories": result.matched_memories,
            "reindexed_memories": result.reindexed_memories,
            "failed_memories": result.failed_memories,
        },
    )
    return EmbeddingReindexResponse(**to_plain_dict(result))

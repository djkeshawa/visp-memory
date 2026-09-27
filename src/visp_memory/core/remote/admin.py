from typing import Any, Dict, List, Optional

import requests

from visp_memory.core.indexing import EmbeddingIndexReport, ReindexResult, ReindexScope
from visp_memory.core.remote.errors import RemoteStorageError
from visp_memory.core.remote.scope import scoped_repo_id
from visp_memory.core.storage import StorageCapabilities

LEGACY_REMOTE_CAPABILITIES = StorageCapabilities(
    audit_log=False,
    reindex=False,
    vector_search=False,
)


class RemoteAdminMixin:
    """Remote inspection and owner-maintenance operations."""

    def inspect_embedding_index(
        self,
        *,
        storage_backend: str,
        provider: str,
        effective_provider: str = None,
        model: str = None,
        dimension: int = None,
        scope: ReindexScope = None,
    ) -> EmbeddingIndexReport:
        """Use the server's provider configuration to describe its index."""
        scope = scope or ReindexScope(repo_id=scoped_repo_id(self))
        try:
            response = self.session.get(
                f"{self.server_url}/diagnostics/embedding-index",
                params=scope.as_filter_dict(),
            )
            response.raise_for_status()
            return self._index_report(response, "inspect embedding index", EmbeddingIndexReport)
        except requests.RequestException as error:
            raise self._write_error("inspect embedding index", error) from error

    def rebuild_embedding_index(
        self, *, scope: ReindexScope = None, dry_run: bool = True
    ) -> ReindexResult:
        scope = scope or ReindexScope(repo_id=scoped_repo_id(self))
        try:
            response = self.session.post(
                f"{self.server_url}/diagnostics/embedding-index/reindex",
                json={**scope.as_filter_dict(), "dry_run": dry_run},
            )
            response.raise_for_status()
            return self._index_report(response, "rebuild embedding index", ReindexResult)
        except requests.RequestException as error:
            raise self._write_error("rebuild embedding index", error) from error

    def _index_report(self, response, operation: str, report_type):
        payload = self._response_json(response, operation, dict)
        try:
            return report_type(**payload)
        except (TypeError, ValueError) as error:
            raise RemoteStorageError(
                f"Remote server returned an invalid report while attempting to {operation}"
            ) from error

    def list_audit_logs(
        self,
        actor_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        params = {
            **(self._repo_params(repo_id) or {}),
            "limit": limit,
        }
        if actor_id is not None:
            params["actor_id"] = actor_id
        if event_type is not None:
            params["event_type"] = event_type
        try:
            response = self.session.get(
                f"{self.server_url}/platform/audit-log", params=params,
            )
            response.raise_for_status()
            return self._response_json(response, "list audit logs", list)
        except requests.RequestException as error:
            raise self._write_error("list audit logs", error) from error

    def peek_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        params = self._repo_params()
        try:
            response = self.session.get(
                f"{self.server_url}/memories/{memory_id}/peek",
                params=params,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return self._response_json(response, "peek memory", dict)
        except requests.RequestException as error:
            raise self._write_error("peek memory", error) from error

    def inspect_intent_usage(self, repo_id: str = None) -> Dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.server_url}/intents/usage",
                params=self._repo_params(repo_id),
            )
            response.raise_for_status()
            return self._response_json(response, "inspect intent usage", dict)
        except requests.RequestException as error:
            raise self._write_error("inspect intent usage", error) from error

    def inspect_repository_registration(
        self, repo_id: str = None
    ) -> Dict[str, Any]:
        resolved_repo_id = scoped_repo_id(self, repo_id)
        if not resolved_repo_id:
            raise ValueError("repo_id is required for repository registration inspection")
        try:
            response = self.session.get(
                f"{self.server_url}/repos/{resolved_repo_id}/registration"
            )
            response.raise_for_status()
            return self._response_json(
                response, "inspect repository registration", dict
            )
        except requests.RequestException as error:
            raise self._write_error(
                "inspect repository registration", error
            ) from error

    def get_capabilities(self) -> StorageCapabilities:
        cached = getattr(self, "_capabilities_cache", None)
        if cached is not None:
            return cached
        if getattr(self, "session", None) is None:
            return LEGACY_REMOTE_CAPABILITIES

        try:
            response = self.session.get(
                f"{self.server_url}/diagnostics/capabilities"
            )
            if response.status_code == 404:
                capabilities = LEGACY_REMOTE_CAPABILITIES
            else:
                response.raise_for_status()
                payload = self._response_json(response, "get capabilities", dict)
                known_fields = StorageCapabilities.__dataclass_fields__
                capabilities = StorageCapabilities(
                    **{
                        name: payload[name]
                        for name in known_fields
                        if name in payload
                    }
                )
        except requests.RequestException as error:
            raise self._write_error("get capabilities", error) from error

        self._capabilities_cache = capabilities
        return capabilities

    def _repo_params(self, repo_id: str = None) -> Optional[Dict[str, str]]:
        resolved_repo_id = scoped_repo_id(self, repo_id)
        return {"repo_id": resolved_repo_id} if resolved_repo_id is not None else None

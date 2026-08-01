"""
Remote Storage implementation (Client).

Connects to the Central Memory Server via HTTP.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from visp_memory.core.storage import BaseStorage, MemoryLayer, StorageCapabilities

logger = logging.getLogger(__name__)


class RemoteStorageError(RuntimeError):
    """Raised when the remote server cannot complete a write operation."""


class RemoteStorage(BaseStorage):
    """Storage client that connects to a remote Central Memory Server."""

    def __init__(
        self,
        server_url: str,
        api_key: str = None,
        jwt_token: str = None,
        timeout: float = 30.0,
    ):
        """
        Initialize remote storage client.

        Args:
            server_url: Base URL of the server (e.g., http://localhost:8000)
            api_key: Optional API key for authentication
            jwt_token: Optional JWT token for Bearer authentication (takes precedence)
            timeout: Per-request timeout (seconds) applied to every call so a
                hung endpoint cannot stall the client indefinitely.
        """
        if not REQUESTS_AVAILABLE:
            raise ImportError(
                "requests is required for RemoteStorage. Install with: pip install requests"
            )

        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.jwt_token = jwt_token
        self.timeout = timeout
        self.session = requests.Session()

        # Set authentication headers
        if jwt_token:
            self.session.headers.update({"Authorization": f"Bearer {jwt_token}"})
        elif api_key:
            self.session.headers.update({"X-API-Key": api_key})

        # Warn if credentials would be sent over cleartext to a non-local host.
        if (jwt_token or api_key) and self.server_url.startswith("http://"):
            host = self.server_url.split("://", 1)[-1].split("/")[0].split(":")[0]
            if host not in ("localhost", "127.0.0.1", "::1"):
                logger.warning(
                    "Sending credentials over cleartext HTTP to %s; use https:// to "
                    "protect the api_key/jwt_token.",
                    self.server_url,
                )

        # Wrap session.request once so every call gets a default timeout and so
        # transport errors / auth / 5xx responses are logged centrally — even
        # when an individual read method masks the failure as an empty result.
        self._install_request_guard()

        # Test connection (and surface auth failures up front).
        try:
            probe = self.session.get(f"{self.server_url}/")
            if probe.status_code in (401, 403):
                logger.warning(
                    "Authentication to memory server at %s failed (HTTP %s); "
                    "check api_key/jwt_token.",
                    self.server_url,
                    probe.status_code,
                )
        except requests.RequestException:
            # The request guard already logged the transport failure (with URL);
            # the probe is best-effort and must not fail client construction.
            pass

    def close(self) -> None:
        """Close the underlying HTTP session and its connection pool. Idempotent."""
        session = getattr(self, "session", None)
        if session is not None:
            session.close()
            self.session = None

    def get_capabilities(self) -> StorageCapabilities:
        return StorageCapabilities(audit_log=False, reindex=False, vector_search=False)

    def _install_request_guard(self) -> None:
        """Inject a default timeout and centralized failure logging into the session."""
        original_request = self.session.request
        timeout = self.timeout

        def guarded_request(method, url, **kwargs):
            kwargs.setdefault("timeout", timeout)
            try:
                response = original_request(method, url, **kwargs)
            except requests.RequestException as exc:
                logger.warning("Remote request %s %s failed: %s", method, url, exc)
                raise
            if response.status_code >= 500 or response.status_code in (401, 403):
                logger.warning(
                    "Remote request %s %s returned HTTP %s",
                    method,
                    url,
                    response.status_code,
                )
            return response

        self.session.request = guarded_request

    def store_memory(
        self, content: str, layer: MemoryLayer = "episodic", repo_id: str = None, **kwargs
    ) -> str:
        """Store a memory remotely."""
        try:
            payload = {"content": content, "layer": layer, "repo_id": repo_id, **kwargs}
            # Map kwargs to API schema if needed
            # For now, simplistic mapping
            response = self.session.post(f"{self.server_url}/memories", json=payload)
            response.raise_for_status()
            return self._response_id(response, "store memory")
        except requests.RequestException as e:
            raise self._write_error("store memory", e) from e

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory by ID."""
        try:
            response = self.session.get(f"{self.server_url}/memories/{memory_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return self._response_json(response, "get memory", dict)
        except requests.RequestException as e:
            raise self._write_error("get memory", e) from e

    def search_memories(self, query: str, repo_id: str = None, **kwargs) -> List[Dict[str, Any]]:
        """Search across memories."""
        try:
            filters = {
                key: self._serialize_request_value(value)
                for key, value in kwargs.items()
                if value is not None
            }
            layer = filters.pop("layer", None)
            if layer and "layers" not in filters:
                filters["layers"] = [layer]
            payload = {
                key: value
                for key, value in {"query": query, "repo_id": repo_id, **filters}.items()
                if value is not None
            }
            response = self.session.post(f"{self.server_url}/recall", json=payload)
            response.raise_for_status()
            return self._response_json(response, "search memories", list)
        except requests.RequestException as e:
            raise self._write_error("search memories", e) from e

    @classmethod
    def _serialize_request_value(cls, value: Any) -> Any:
        """Convert runtime scope values to HTTP-client-safe representations."""
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, (list, tuple)):
            return [cls._serialize_request_value(item) for item in value]
        return value

    def list_memories(self, repo_id: str = None, **kwargs) -> List[Dict[str, Any]]:
        """List memories with optional filtering."""
        try:
            params = {
                key: value
                for key, value in {"repo_id": repo_id, **kwargs}.items()
                if value is not None
            }
            response = self.session.get(f"{self.server_url}/memories", params=params)
            response.raise_for_status()
            return self._response_json(response, "list memories", list)
        except requests.RequestException as e:
            raise self._write_error("list memories", e) from e

    def update_memory(self, memory_id: str, **kwargs) -> bool:
        """Update a memory."""
        try:
            response = self.session.patch(f"{self.server_url}/memories/{memory_id}", json=kwargs)
            if response.status_code == 404:
                return False
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            raise self._write_error("update memory", e) from e

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory."""
        try:
            response = self.session.delete(f"{self.server_url}/memories/{memory_id}")
            if response.status_code == 404:
                return False
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            raise self._write_error("delete memory", e) from e

    def get_collection(self, layer: str):
        return None  # Remote storage doesn't expose vector collections directly

    def _response_id(self, response: Any, operation: str) -> str:
        """Extract a created resource ID from a successful response."""
        try:
            resource_id = response.json()["id"]
        except (KeyError, TypeError, ValueError) as e:
            raise RemoteStorageError(
                f"Remote server completed {operation} without returning an id"
            ) from e

        if not resource_id:
            raise RemoteStorageError(f"Remote server returned an empty id for {operation}")

        return str(resource_id)

    def _response_json(self, response: Any, operation: str, expected_type: type) -> Any:
        """Decode and validate JSON returned by a successful remote read."""
        try:
            payload = response.json()
        except (TypeError, ValueError) as error:
            raise RemoteStorageError(
                f"Remote server returned malformed JSON while attempting to {operation}"
            ) from error
        if not isinstance(payload, expected_type):
            raise RemoteStorageError(
                f"Remote server returned an invalid response while attempting to {operation}"
            )
        return payload

    def _write_error(self, operation: str, error: Any) -> RemoteStorageError:
        detail = str(error)
        response = getattr(error, "response", None)
        if response is not None:
            try:
                body = response.json()
                response_detail = body.get("detail") if isinstance(body, dict) else None
            except ValueError:
                response_detail = response.text.strip()

            if response_detail:
                detail = f"HTTP {response.status_code}: {response_detail}"
            else:
                detail = f"HTTP {response.status_code}"

        return RemoteStorageError(f"Failed to {operation} on remote memory server: {detail}")

    # Intent Operations
    def set_intent(
        self,
        description: str,
        priority: int = 0,
        context: Dict[str, Any] = None,
        repo_id: str = None,
    ) -> str:
        try:
            payload = {
                "description": description,
                "priority": priority,
                "context": context or {},
                "repo_id": repo_id,
            }
            response = self.session.post(f"{self.server_url}/intents", json=payload)
            response.raise_for_status()
            return self._response_id(response, "set intent")
        except requests.RequestException as e:
            raise self._write_error("set intent", e) from e

    def get_active_intents(
        self, repo_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        """Get intents filtered by status."""
        try:
            params = {"status": status}
            if repo_id:
                params["repo_id"] = repo_id
            response = self.session.get(f"{self.server_url}/intents", params=params)
            response.raise_for_status()
            payload = self._response_json(response, "list intents", list)
            if status == "all":
                return payload
            return [i for i in payload if i.get("status") == status]
        except requests.RequestException as e:
            raise self._write_error("list intents", e) from e

    def complete_intent(self, intent_id: str) -> bool:
        """Keep the legacy surface without requesting a status transition."""
        return False

    def update_intent(self, intent_id: str, **kwargs) -> bool:
        """Update an intent on the remote server."""
        payload = {
            key: value
            for key, value in kwargs.items()
            if key != "status" and value is not None
        }
        if not payload:
            return False
        try:
            response = self.session.patch(f"{self.server_url}/intents/{intent_id}", json=payload)
            if response.status_code == 404:
                return False
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            raise self._write_error("update intent", e) from e

    # Relationship Operations
    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        strength: float = 1.0,
        evidence: Dict[str, Any] = None,
    ) -> str:
        """Create a relationship."""
        try:
            payload = {
                "source_id": source_id,
                "target_id": target_id,
                "relationship": relationship,
                "strength": strength,
            }
            if evidence is not None:
                payload["evidence"] = evidence
            response = self.session.post(f"{self.server_url}/relationships", json=payload)
            response.raise_for_status()
            return self._response_id(response, "add relationship")
        except requests.RequestException as e:
            raise self._write_error("add relationship", e) from e

    def get_related_memories(
        self,
        memory_id: str,
        relationship: str = None,
        environment: Any = None,
        task_type: Any = None,
        as_of: Any = None,
    ) -> List[Dict[str, Any]]:
        try:
            params = {
                key: self._serialize_request_value(value)
                for key, value in {
                    "relationship": relationship,
                    "environment": environment,
                    "task_type": task_type,
                    "as_of": as_of,
                }.items()
                if value is not None
            }
            response = self.session.get(
                f"{self.server_url}/memories/{memory_id}/related",
                params=params or None,
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return self._response_json(response, "get related memories", list)
        except requests.RequestException as e:
            raise self._write_error("get related memories", e) from e

    def get_all_relationships(self, repo_id: str = None) -> List[Dict[str, Any]]:
        """Get all relationships."""
        try:
            params = {}
            if repo_id:
                params["repo_id"] = repo_id
            response = self.session.get(f"{self.server_url}/relationships", params=params)
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return self._response_json(response, "list relationships", list)
        except requests.RequestException as e:
            raise self._write_error("list relationships", e) from e

    def delete_relationship(self, relationship_id: str) -> bool:
        try:
            response = self.session.delete(
                f"{self.server_url}/relationships/{relationship_id}"
            )
            if response.status_code == 404:
                return False
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            raise self._write_error("delete relationship", e) from e

    # Session Operations
    def start_session(self) -> str:
        try:
            response = self.session.post(f"{self.server_url}/sessions")
            response.raise_for_status()
            return self._response_id(response, "start session")
        except requests.RequestException as e:
            raise self._write_error("start session", e) from e

    def end_session(self, session_id: str, summary: str, memory_ids: List[str]):
        try:
            response = self.session.post(
                f"{self.server_url}/sessions/{session_id}/complete",
                json={"summary": summary, "memory_ids": memory_ids},
            )
            if response.status_code == 404:
                return False
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            raise self._write_error("complete session", e) from e

    # Stats
    def get_stats(self, repo_id: str = None) -> Dict[str, Any]:
        """Get server stats."""
        try:
            params = {"repo_id": repo_id} if repo_id else None
            response = self.session.get(f"{self.server_url}/", params=params)
            response.raise_for_status()
            data = self._response_json(response, "get stats", dict)
            return data.get("stats", data)
        except requests.RequestException as e:
            raise self._write_error("get stats", e) from e

    # Repository Operations
    def store_repository(self, repo: Dict[str, Any]) -> str:
        try:
            response = self.session.post(f"{self.server_url}/repos", json=repo)
            response.raise_for_status()
            return self._response_id(response, "store repository")
        except requests.RequestException as e:
            raise self._write_error("store repository", e) from e

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.server_url}/repos/{repo_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return self._response_json(response, "get repository", dict)
        except requests.RequestException as e:
            raise self._write_error("get repository", e) from e

    def list_repositories(self, team_id: str = None) -> List[Dict[str, Any]]:
        try:
            params = {}
            if team_id:
                params["team_id"] = team_id
            response = self.session.get(f"{self.server_url}/repos", params=params)
            response.raise_for_status()
            return self._response_json(response, "list repositories", list)
        except requests.RequestException as e:
            raise self._write_error("list repositories", e) from e

    def update_repository(self, repo_id: str, **kwargs) -> bool:
        action = "archive" if kwargs.get("status") == "archived" else "restore"
        try:
            response = self.session.post(f"{self.server_url}/repos/{repo_id}/{action}")
            if response.status_code == 404:
                return False
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            raise self._write_error(f"{action} repository", e) from e

    def delete_repository(self, repo_id: str) -> bool:
        try:
            response = self.session.delete(
                f"{self.server_url}/repos/{repo_id}", params={"confirmation": repo_id}
            )
            if response.status_code == 404:
                return False
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            raise self._write_error("purge repository", e) from e

    def list_project_ids(self) -> List[str]:
        try:
            response = self.session.get(f"{self.server_url}/repos/scopes")
            response.raise_for_status()
            payload = self._response_json(response, "list project scopes", list)
            return [item["id"] for item in payload if item.get("id")]
        except requests.RequestException as e:
            raise self._write_error("list project scopes", e) from e

    def add_repo_dependency(
        self, source_id: str, target_id: str, dep_type: str, version: str = None, notes: str = None
    ) -> str:
        try:
            payload = {
                "target_repo_id": target_id,
                "dependency_type": dep_type,
                "version": version,
                "notes": notes,
            }
            response = self.session.post(
                f"{self.server_url}/repos/{source_id}/dependencies", json=payload
            )
            response.raise_for_status()
            return self._response_id(response, "add repository dependency")
        except requests.RequestException as e:
            raise self._write_error("add repository dependency", e) from e

    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.server_url}/repos/{repo_id}/dependencies")
            response.raise_for_status()
            return self._response_json(response, "get repository dependencies", list)
        except requests.RequestException as e:
            raise self._write_error("get repository dependencies", e) from e

    # Team and User Operations
    def store_user(self, user: Dict[str, Any]) -> str:
        try:
            response = self.session.post(f"{self.server_url}/teams/users", json=user)
            response.raise_for_status()
            return self._response_id(response, "store user")
        except requests.RequestException as e:
            raise self._write_error("store user", e) from e

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.server_url}/teams/users/{user_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return self._response_json(response, "get user", dict)
        except requests.RequestException as e:
            raise self._write_error("get user", e) from e

    def store_team(self, team: Dict[str, Any]) -> str:
        try:
            response = self.session.post(f"{self.server_url}/teams", json=team)
            response.raise_for_status()
            return self._response_id(response, "store team")
        except requests.RequestException as e:
            raise self._write_error("store team", e) from e

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.server_url}/teams/{team_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return self._response_json(response, "get team", dict)
        except requests.RequestException as e:
            raise self._write_error("get team", e) from e

    def add_team_member(self, team_id: str, user_id: str) -> bool:
        try:
            payload = {"user_id": user_id}
            response = self.session.post(f"{self.server_url}/teams/{team_id}/members", json=payload)
            if response.status_code == 404:
                return False
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            raise self._write_error("add team member", e) from e

    def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.server_url}/teams/users/{user_id}/teams")
            response.raise_for_status()
            return self._response_json(response, "get user teams", list)
        except requests.RequestException as e:
            raise self._write_error("get user teams", e) from e

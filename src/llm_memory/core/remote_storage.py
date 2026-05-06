"""
Remote Storage implementation (Client).

Connects to the Central Memory Server via HTTP.
"""

import logging
from typing import Any, Dict, List, Optional

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from llm_memory.core.storage import BaseStorage, MemoryLayer

logger = logging.getLogger(__name__)

class RemoteStorage(BaseStorage):
    """Storage client that connects to a remote Central Memory Server."""

    def __init__(self, server_url: str, api_key: str = None, jwt_token: str = None):
        """
        Initialize remote storage client.

        Args:
            server_url: Base URL of the server (e.g., http://localhost:8000)
            api_key: Optional API key for authentication
            jwt_token: Optional JWT token for Bearer authentication (takes precedence)
        """
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests is required for RemoteStorage. Install with: pip install requests")

        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.jwt_token = jwt_token
        self.session = requests.Session()

        # Set authentication headers
        if jwt_token:
            self.session.headers.update({"Authorization": f"Bearer {jwt_token}"})
        elif api_key:
            self.session.headers.update({"X-API-Key": api_key})

        # Test connection
        try:
            self.session.get(f"{self.server_url}/")
        except requests.RequestException as e:
            logger.warning(f"Could not connect to memory server at {server_url}: {e}")


    def store_memory(self, content: str, layer: MemoryLayer = "episodic", repo_id: str = None, **kwargs) -> str:
        """Store a memory remotely."""
        try:
            payload = {
                "content": content,
                "layer": layer,
                "repo_id": repo_id,
                **kwargs
            }
            # Map kwargs to API schema if needed
            # For now, simplistic mapping
            response = self.session.post(f"{self.server_url}/memories", json=payload)
            response.raise_for_status()
            return response.json()["id"]
        except requests.RequestException as e:
            logger.error(f"Failed to store memory: {e}")
            return "error_id"

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory by ID."""
        try:
            response = self.session.get(f"{self.server_url}/memories/{memory_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return None

    def search_memories(self, query: str, repo_id: str = None, **kwargs) -> List[Dict[str, Any]]:
        """Search across memories."""
        try:
            payload = {"query": query, "repo_id": repo_id, **kwargs}
            response = self.session.post(f"{self.server_url}/recall", json=payload)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return []

    def list_memories(self, repo_id: str = None, **kwargs) -> List[Dict[str, Any]]:
        """List memories with optional filtering."""
        try:
            params = {"repo_id": repo_id, **kwargs}
            response = self.session.get(f"{self.server_url}/memories", params=params)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return []

    def update_memory(self, memory_id: str, **kwargs) -> bool:
        """Update a memory."""
        try:
            response = self.session.patch(f"{self.server_url}/memories/{memory_id}", json=kwargs)
            return response.status_code == 200
        except requests.RequestException:
            return False

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory."""
        try:
            response = self.session.delete(f"{self.server_url}/memories/{memory_id}")
            return response.status_code == 200
        except requests.RequestException:
            return False

    def get_collection(self, layer: str):
        return None  # Remote storage doesn't expose vector collections directly

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
            return response.json()["id"]
        except requests.RequestException:
            return "error"

    def get_active_intents(self, repo_id: str = None) -> List[Dict[str, Any]]:
        """Get active intents."""
        try:
            params = {}
            if repo_id:
                params["repo_id"] = repo_id
            response = self.session.get(f"{self.server_url}/intents", params=params)
            response.raise_for_status()
            # Filter for active ones client-side if needed, but endpoint returns list
            return [i for i in response.json() if i.get("status") == "active"]
        except requests.RequestException:
            return []

    def complete_intent(self, intent_id: str) -> bool:
        """Mark intent as complete."""
        # Mapping to update_memory since intents are memories
        return self.update_memory(intent_id, metadata={"status": "completed"})

    # Relationship Operations
    def add_relationship(self, source_id: str, target_id: str, relationship: str, strength: float = 1.0) -> str:
        """Create a relationship."""
        try:
            payload = {
                "source_id": source_id,
                "target_id": target_id,
                "relationship": relationship,
                "strength": strength
            }
            response = self.session.post(f"{self.server_url}/relationships", json=payload)
            response.raise_for_status()
            return response.json()["id"]
        except requests.RequestException:
            return "error"

    def get_related_memories(self, memory_id: str, relationship: str = None) -> List[Dict[str, Any]]:
        # Not currently exposed via API explicitly
        return []

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
            return response.json()
        except requests.RequestException:
            return []

    # Session Operations
    def start_session(self) -> str:
        return "session_remote"

    def end_session(self, session_id: str, summary: str, memory_ids: List[str]):
        pass

    # Stats
    def get_stats(self) -> Dict[str, Any]:
        """Get server stats."""
        try:
            response = self.session.get(f"{self.server_url}/")
            if response.status_code == 200:
                data = response.json()
                return data.get("stats", data)
            return {}
        except requests.RequestException:
            return {}

    # Repository Operations
    def store_repository(self, repo: Dict[str, Any]) -> str:
        try:
            response = self.session.post(f"{self.server_url}/repos", json=repo)
            response.raise_for_status()
            return response.json()["id"]
        except requests.RequestException:
            return "error"

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.server_url}/repos/{repo_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return None

    def list_repositories(self, team_id: str = None) -> List[Dict[str, Any]]:
        try:
            params = {}
            if team_id:
                params["team_id"] = team_id
            response = self.session.get(f"{self.server_url}/repos", params=params)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return []

    def add_repo_dependency(self, source_id: str, target_id: str, dep_type: str, version: str = None, notes: str = None) -> str:
        try:
            payload = {
                "target_repo_id": target_id,
                "dependency_type": dep_type,
                "version": version,
                "notes": notes
            }
            response = self.session.post(f"{self.server_url}/repos/{source_id}/dependencies", json=payload)
            response.raise_for_status()
            return response.json().get("id", "ok")
        except requests.RequestException:
            return "error"

    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.server_url}/repos/{repo_id}/dependencies")
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return []

    # Team and User Operations
    def store_user(self, user: Dict[str, Any]) -> str:
        try:
            response = self.session.post(f"{self.server_url}/teams/users", json=user)
            response.raise_for_status()
            return response.json()["id"]
        except requests.RequestException:
            return "error"

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.server_url}/teams/users/{user_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return None

    def store_team(self, team: Dict[str, Any]) -> str:
        try:
            response = self.session.post(f"{self.server_url}/teams", json=team)
            response.raise_for_status()
            return response.json()["id"]
        except requests.RequestException:
            return "error"

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.server_url}/teams/{team_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return None

    def add_team_member(self, team_id: str, user_id: str) -> bool:
        try:
            payload = {"user_id": user_id}
            response = self.session.post(f"{self.server_url}/teams/{team_id}/members", json=payload)
            return response.status_code == 200
        except requests.RequestException:
            return False

    def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.server_url}/teams/users/{user_id}/teams")
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return []

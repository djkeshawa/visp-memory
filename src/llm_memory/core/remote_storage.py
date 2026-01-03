"""
Remote Storage implementation (Client).

Connects to the Central Memory Server via HTTP.
"""

from typing import Optional, List, Dict, Any
import logging
from abc import ABC

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from llm_memory.core.storage import BaseStorage, MemoryLayer

logger = logging.getLogger(__name__)

class RemoteStorage(BaseStorage):
    """Storage client that connects to a remote Central Memory Server."""

    def __init__(self, server_url: str, api_key: str = None):
        """
        Initialize remote storage client.

        Args:
            server_url: Base URL of the server (e.g., http://localhost:8000)
            api_key: Optional API key for authentication
        """
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests is required for RemoteStorage. Install with: pip install requests")

        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        if api_key:
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
    def set_intent(self, description: str, priority: int = 0, context: Dict[str, Any] = None) -> str:
        try:
            payload = {
                "description": description,
                "priority": priority,
                "context": context or {}
            }
            response = self.session.post(f"{self.server_url}/intents", json=payload)
            response.raise_for_status()
            return response.json()["id"]
        except requests.RequestException:
            return "error"

    def get_active_intents(self) -> List[Dict[str, Any]]:
        """Get active intents."""
        try:
            response = self.session.get(f"{self.server_url}/intents")
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
                return response.json()
            return {}
        except requests.RequestException:
            return {}

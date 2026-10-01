from typing import Any, Dict, List

import requests

from visp_memory.core.remote.compatibility import route_missing
from visp_memory.core.remote.errors import RemoteStorageError
from visp_memory.core.remote.scope import scoped_repo_id
from visp_memory.core.storage import RecallEventType
from visp_memory.core.turn_keys import BRIEF_TURN_KEYS


class RemoteRecallMixin:
    """Remote recall utility and turn-key read operations."""

    def log_recall_event(
        self,
        memory_id: str,
        event_type: RecallEventType,
        repo_id: str = None,
        query: str = None,
        task_id: str = None,
        outcome: str = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        payload = {
            "memory_id": memory_id,
            "event_type": event_type,
            "repo_id": scoped_repo_id(self, repo_id),
            "query": query,
            "task_id": task_id,
            "outcome": outcome,
            "metadata": metadata or {},
        }
        try:
            response = self.session.post(
                f"{self.server_url}/recall-events", json=payload
            )
            response.raise_for_status()
            return self._response_id(response, "log recall event")
        except requests.RequestException as error:
            raise self._write_error("log recall event", error) from error

    def inspect_recall_utility(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        params = self._recall_event_params(
            memory_id=memory_id,
            repo_id=repo_id,
            event_type=event_type,
        )
        params["limit"] = limit
        try:
            response = self.session.get(
                f"{self.server_url}/recall-events/utility", params=params
            )
            response.raise_for_status()
            return self._response_json(response, "inspect recall utility", dict)
        except requests.RequestException as error:
            raise self._write_error("inspect recall utility", error) from error

    def verify_recall_utility(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
    ) -> Dict[str, Any]:
        params = self._recall_event_params(
            memory_id=memory_id,
            repo_id=repo_id,
            event_type=event_type,
        )
        try:
            response = self.session.get(
                f"{self.server_url}/recall-events/verify", params=params
            )
            response.raise_for_status()
            return self._response_json(response, "verify recall utility", dict)
        except requests.RequestException as error:
            raise self._write_error("verify recall utility", error) from error

    def reset_recall_utility(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
    ) -> int:
        params = self._recall_event_params(
            memory_id=memory_id,
            repo_id=repo_id,
            event_type=event_type,
        )
        try:
            response = self.session.delete(
                f"{self.server_url}/recall-events", params=params
            )
            response.raise_for_status()
            payload = self._response_json(response, "reset recall utility", dict)
        except requests.RequestException as error:
            raise self._write_error("reset recall utility", error) from error

        deleted = payload.get("deleted")
        if not isinstance(deleted, int) or isinstance(deleted, bool):
            raise RemoteStorageError(
                "Remote server returned an invalid response while attempting to "
                "reset recall utility"
            )
        return deleted

    def search_turn_keys(
        self,
        query: str,
        *,
        repo_id: str = None,
        limit: int = BRIEF_TURN_KEYS,
        status: str = "active",
    ) -> List[Dict[str, Any]]:
        payload = {
            "query": query,
            "repo_id": scoped_repo_id(self, repo_id),
            "limit": limit,
            "status": status,
        }
        try:
            response = self.session.post(
                f"{self.server_url}/turn-keys/search", json=payload
            )
            if route_missing(response):
                return []
            response.raise_for_status()
            return self._response_json(response, "search turn keys", list)
        except requests.RequestException as error:
            raise self._write_error("search turn keys", error) from error

    def _recall_event_params(
        self,
        *,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
    ) -> Dict[str, Any]:
        return {
            key: value
            for key, value in {
                "memory_id": memory_id,
                "repo_id": scoped_repo_id(self, repo_id),
                "event_type": event_type,
            }.items()
            if value is not None
        }

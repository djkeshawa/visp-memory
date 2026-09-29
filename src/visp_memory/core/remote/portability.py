from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from visp_memory.core.remote.scope import scoped_repo_id


class RemotePortabilityMixin:
    """Keep graph serialization and transactional import at the storage owner."""

    def export_graph(self, repo_id: str = None) -> Dict[str, Any]:
        repo_id = self._portability_repo_id(repo_id)
        try:
            response = self.session.get(f"{self.server_url}/repos/{quote(repo_id, safe='')}/export")
            response.raise_for_status()
            return self._response_json(response, "export graph", dict)
        except requests.RequestException as error:
            raise self._write_error("export graph", error) from error

    def import_graph(
        self, data: Dict[str, Any], *, default_repo_id: str
    ) -> Dict[str, Any]:
        repo_id = self._portability_repo_id(default_repo_id)
        try:
            response = self.session.post(
                f"{self.server_url}/repos/{quote(repo_id, safe='')}/import", json=data,
            )
            response.raise_for_status()
            return self._response_json(response, "import graph", dict)
        except requests.RequestException as error:
            raise self._write_error("import graph", error) from error

    def get_authority_attestation(self, belief_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.session.get(
                f"{self.server_url}/memories/{quote(belief_id, safe='')}/attestation",
                params=self._repo_params(),
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return self._response_json(response, "get authority attestation", dict)
        except requests.RequestException as error:
            raise self._write_error("get authority attestation", error) from error

    def _portability_repo_id(self, repo_id: str = None) -> str:
        resolved = scoped_repo_id(self, repo_id)
        if not isinstance(resolved, str) or not resolved.strip():
            raise ValueError("repo_id is required for graph portability")
        return resolved

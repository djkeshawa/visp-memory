"""ArcadeDB storage backend boundary.

The full storage contract is implemented in later Visp tasks. This module keeps
optional dependency loading lazy so default installs do not require ArcadeDB.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from llm_memory.core.storage import BaseStorage, MemoryLayer

ARCADEDB_INSTALL_MESSAGE = (
    "ArcadeDB storage requires the optional ArcadeDB extra. "
    'Install it with: pip install "llm-memory[arcadedb]"'
)


class ArcadeDbDependencyError(ImportError):
    """Raised when ArcadeDB storage is selected without the optional extra."""


def load_arcadedb_driver():
    """Load the optional ArcadeDB embedded driver."""
    try:
        import arcadedb_embedded as arcadedb
    except ImportError as exc:
        raise ArcadeDbDependencyError(ARCADEDB_INSTALL_MESSAGE) from exc
    return arcadedb


class ArcadeDbStorage(BaseStorage):
    """ArcadeDB-backed storage.

    T001 establishes backend selection and dependency behavior. Later tasks add
    concrete persistence for the BaseStorage contract.
    """

    def __init__(
        self,
        data_dir: Path,
        embedding_fn=None,
        embedding_dimension: int | None = None,
    ):
        self._arcadedb = load_arcadedb_driver()
        self.data_dir = Path(data_dir) / "arcadedb"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._embedding_fn = embedding_fn
        self._embedding_dimension = embedding_dimension

    def _not_implemented(self):
        raise NotImplementedError(
            "ArcadeDB storage backend selection is available, but the full storage "
            "contract is implemented in the next ArcadeDB feature tasks."
        )

    def store_memory(
        self, content: str, layer: MemoryLayer = "episodic", repo_id: str = None, **kwargs
    ) -> str:
        self._not_implemented()

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        self._not_implemented()

    def search_memories(self, query: str, repo_id: str = None, **kwargs) -> List[Dict[str, Any]]:
        self._not_implemented()

    def list_memories(self, repo_id: str = None, **kwargs) -> List[Dict[str, Any]]:
        self._not_implemented()

    def update_memory(self, memory_id: str, **kwargs) -> bool:
        self._not_implemented()

    def delete_memory(self, memory_id: str) -> bool:
        self._not_implemented()

    def get_collection(self, layer: str):
        self._not_implemented()

    def set_intent(
        self,
        description: str,
        priority: int = 0,
        context: Dict[str, Any] = None,
        repo_id: str = None,
    ) -> str:
        self._not_implemented()

    def get_active_intents(
        self, repo_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        self._not_implemented()

    def complete_intent(self, intent_id: str) -> bool:
        self._not_implemented()

    def update_intent(self, intent_id: str, **kwargs) -> bool:
        self._not_implemented()

    def add_relationship(
        self, source_id: str, target_id: str, relationship: str, strength: float = 1.0
    ) -> str:
        self._not_implemented()

    def get_related_memories(
        self, memory_id: str, relationship: str = None
    ) -> List[Dict[str, Any]]:
        self._not_implemented()

    def start_session(self) -> str:
        self._not_implemented()

    def end_session(self, session_id: str, summary: str, memory_ids: List[str]):
        self._not_implemented()

    def get_all_relationships(self, repo_id: str = None) -> List[Dict[str, Any]]:
        self._not_implemented()

    def get_stats(self, repo_id: str = None) -> Dict[str, Any]:
        self._not_implemented()

    def store_repository(self, repo: Dict[str, Any]) -> str:
        self._not_implemented()

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        self._not_implemented()

    def list_repositories(self, team_id: str = None) -> List[Dict[str, Any]]:
        self._not_implemented()

    def list_project_ids(self) -> List[str]:
        self._not_implemented()

    def add_repo_dependency(
        self, source_id: str, target_id: str, dep_type: str, version: str = None, notes: str = None
    ) -> str:
        self._not_implemented()

    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        self._not_implemented()

    def store_user(self, user: Dict[str, Any]) -> str:
        self._not_implemented()

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        self._not_implemented()

    def store_team(self, team: Dict[str, Any]) -> str:
        self._not_implemented()

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        self._not_implemented()

    def add_team_member(self, team_id: str, user_id: str) -> bool:
        self._not_implemented()

    def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        self._not_implemented()


__all__ = [
    "ARCADEDB_INSTALL_MESSAGE",
    "ArcadeDbDependencyError",
    "ArcadeDbStorage",
    "load_arcadedb_driver",
]

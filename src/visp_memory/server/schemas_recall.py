from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from visp_memory.core.api_limits import MAX_QUERY_LIMIT
from visp_memory.core.turn_keys import BRIEF_TURN_KEYS


class RecallEventCreate(BaseModel):
    memory_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    repo_id: Optional[str] = None
    query: Optional[str] = None
    task_id: Optional[str] = None
    outcome: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RecallEventCreated(BaseModel):
    id: str


class RecallUtilityResetResponse(BaseModel):
    deleted: int = Field(ge=0)


class TurnKeySearchRequest(BaseModel):
    query: str = Field(min_length=1)
    repo_id: Optional[str] = None
    limit: int = Field(default=BRIEF_TURN_KEYS, ge=1, le=MAX_QUERY_LIMIT)
    status: Literal[
        "active", "pending", "archived", "superseded", "merged", "deleted", "all"
    ] = "active"

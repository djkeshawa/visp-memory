"""Mirror explicit external workflow reports; never infer a task decision."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from visp_memory.core.clock import utc_now_iso

WORKFLOW_CONTEXT_KEYS = ("external_workflow", "workflow_history")


class WorkflowEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field(min_length=1, max_length=1000)
    url: HttpUrl | None = None


class AcceptanceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field(min_length=1, max_length=500)
    status: Literal["passed", "failed", "pending"]


class IntentWorkflowReport(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    task_id: str = Field(min_length=1, max_length=200)
    event_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1, strict=True)
    status: Literal["active", "completed", "closed"]
    summary: str = Field(min_length=1, max_length=2000)
    evidence: list[WorkflowEvidence] = Field(default_factory=list, max_length=50)
    checks: list[AcceptanceCheck] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def consistent_completion(self):
        from visp_memory.quality.secrets import scan

        if scan(self.model_dump_json()) or any(
            item.url and (item.url.username or item.url.password) for item in self.evidence
        ):
            raise ValueError("Remove credentials from workflow reports and evidence links")
        if self.status == "completed":
            if not self.evidence:
                raise ValueError("Completed reports must include the source's completion evidence")
            if any(check.status != "passed" for check in self.checks):
                raise ValueError("Completed reports cannot contain failed or pending checks")
        return self


def apply_workflow_report(
    connection, intent_id: str, report: IntentWorkflowReport, actor_id: str, channel: str
) -> dict:
    """Apply one ordered report atomically, bound to its original reporter/task."""
    connection.execute("BEGIN IMMEDIATE")
    row = connection.execute("SELECT * FROM intents WHERE id = ?", (intent_id,)).fetchone()
    if row is None:
        raise LookupError("Intent not found")
    context = json.loads(row["context"] or "{}")
    previous = context.get("external_workflow") or {}
    payload = report.model_dump(mode="json")
    if previous:
        binding = (previous["source"], previous["task_id"], previous["reported_by"])
        if binding != (report.source, report.task_id, actor_id):
            raise ValueError("This intent is already linked to a different workflow or reporter")
        if report.revision <= previous["revision"]:
            same = all(previous.get(key) == value for key, value in payload.items())
            if same:
                connection.rollback()
                return {"status": row["status"], "applied": False, "report": previous}
            raise ValueError("Report is stale or conflicts with an already recorded revision")
        if any(
            item.get("event_id") == report.event_id for item in context.get("workflow_history", [])
        ):
            raise ValueError("Event ID has already been used")
    stored = {
        **payload,
        "reported_by": actor_id,
        "channel": channel,
        "recorded_at": utc_now_iso(),
        "authoritative": False,
    }
    history = context.get("workflow_history", [])
    context["workflow_history"] = [*history, stored]
    context["external_workflow"] = stored
    context.pop("completion_evaluation", None)
    connection.execute(
        "UPDATE intents SET status = ?, context = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (report.status, json.dumps(context), intent_id),
    )
    connection.commit()
    return {"status": report.status, "applied": True, "report": stored}

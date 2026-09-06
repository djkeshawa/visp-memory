import json

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.interfaces.mcp import handle_tool
from visp_memory.interfaces.mcp_tools import build_tool_definitions


@pytest.mark.asyncio
async def test_mcp_report_follows_external_completion_and_keeps_generic_edits_advisory(tmp_path):
    config = MemoryConfig(repo_id="sample")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    intent_id = memory.goal("Deliver login")
    await handle_tool(
        "memory_update_intent", {"intent_id": intent_id, "status": "completed"}, memory
    )
    assert memory._storage.get_active_intents()[0]["status"] == "active"
    report = {
        "source": "assistant",
        "task_id": "task-1",
        "event_id": "event-1",
        "revision": 1,
        "status": "completed",
        "summary": "Login delivered",
        "evidence": [{"description": "Acceptance tests passed"}],
    }
    result = await handle_tool(
        "memory_update_intent", {"intent_id": intent_id, "workflow_report": report}, memory
    )
    assert json.loads(result)["applied"]
    assert memory._storage.get_active_intents() == []
    with pytest.raises(ValueError, match="separately"):
        await handle_tool(
            "memory_update_intent",
            {"intent_id": intent_id, "workflow_report": report, "status": "completed"},
            memory,
        )


def test_nested_report_schema_validates_real_payload(monkeypatch):
    import jsonschema

    monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
    tool = next(tool for tool in build_tool_definitions() if tool.name == "memory_update_intent")
    jsonschema.validate(
        {
            "intent_id": "intent",
            "workflow_report": {
                "source": "assistant",
                "task_id": "task",
                "event_id": "event",
                "revision": 1,
                "status": "completed",
                "summary": "Done",
                "evidence": [{"description": "Tests"}],
            },
        },
        tool.inputSchema,
    )

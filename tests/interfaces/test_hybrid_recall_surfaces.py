"""The public recall switches reach real reranking at the requested output limit."""

import pytest
from typer.testing import CliRunner

from visp_memory import Memory, MemoryConfig
from visp_memory.config import EmbeddingConfig, StorageConfig

pytest.importorskip("mcp")


@pytest.fixture
def ranked_memory(tmp_path, monkeypatch):
    memory = Memory(
        config=MemoryConfig(
            repo_id="repo",
            embedding=EmbeddingConfig(provider="none"),
            storage=StorageConfig(data_dir=tmp_path),
        )
    )
    rows = [
        {"id": "generic", "content": "festival film event", "similarity": 0.9},
        {"id": "specific", "content": "Seattle International Film Festival", "similarity": 0.8},
        {"id": "noise", "content": "festival tickets event", "similarity": 0.7},
    ]
    rows = [
        {
            **r,
            "repo_id": "repo",
            "layer": "episodic",
            "status": "active",
            "retrieval_method": "semantic",
            "importance": 0.5,
        }
        for r in rows
    ]
    monkeypatch.setattr(memory._storage, "search_memories", lambda **kw: rows[: kw["limit"]])
    yield memory
    memory.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["hybrid", "hybrid_union"])
async def test_mcp_hybrid_changes_top_result_and_keeps_output_limit(ranked_memory, strategy):
    from visp_memory.interfaces.mcp import handle_tool

    args = {"query": "Seattle International Film Festival", "limit": 1}
    default = await handle_tool("memory_recall", args, ranked_memory)
    hybrid = await handle_tool(
        "memory_recall", {**args, "ranking_strategy": strategy}, ranked_memory
    )
    assert "[generic]" in default and "[specific]" not in default
    assert "[specific]" in hybrid and "[generic]" not in hybrid
    assert "Found 1 memories" in hybrid
    assert "Hybrid ranking" in hybrid
    invalid = await handle_tool(
        "memory_recall", {**args, "ranking_strategy": "bogus"}, ranked_memory
    )
    assert invalid.startswith("Error:") and "ranking_strategy" in invalid


@pytest.mark.parametrize("strategy", ["hybrid", "hybrid_union"])
def test_cli_hybrid_changes_top_result_and_rejects_invalid_choice(
    ranked_memory, monkeypatch, strategy
):
    from visp_memory.interfaces import cli

    monkeypatch.setattr(cli, "get_memory", lambda: ranked_memory)
    runner = CliRunner()
    args = ["recall", "Seattle International Film Festival", "--limit", "1"]
    default = runner.invoke(cli.app, args)
    hybrid = runner.invoke(cli.app, args + ["--ranking-strategy", strategy])
    assert default.exit_code == 0 and "generic" in default.output
    assert hybrid.exit_code == 0, hybrid.output
    assert "specific" in hybrid.output and "generic" not in hybrid.output
    assert "Hybrid ranking" in hybrid.output
    invalid = runner.invoke(cli.app, args + ["--ranking-strategy", "bogus"])
    assert invalid.exit_code == 2


def test_mcp_schema_advertises_default_and_hybrid():
    from visp_memory.interfaces.mcp_tools import build_tool_definitions

    tool = next(t for t in build_tool_definitions() if t.name == "memory_recall")
    option = tool.inputSchema["properties"]["ranking_strategy"]
    assert option["enum"] == ["default", "hybrid", "hybrid_union"]
    assert option["default"] == "default"

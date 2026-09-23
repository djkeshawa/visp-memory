"""Public context surfaces carry ranking and time into the native compiler."""

import json

import pytest
from typer.testing import CliRunner

from visp_memory import Memory, MemoryConfig
from visp_memory.config import EmbeddingConfig, StorageConfig
from visp_memory.core.trust import WriteChannel
from visp_memory.interfaces import cli
from visp_memory.interfaces.mcp import _handle_context, _handle_task_brief
from visp_memory.interfaces.mcp_tools import build_tool_definitions


@pytest.fixture
def memory(tmp_path):
    with Memory(config=MemoryConfig(
        repo_id="repo", embedding=EmbeddingConfig(provider="none"),
        storage=StorageConfig(data_dir=tmp_path),
    )) as memory:
        memory.record(
            "Use secure session cookies for browser authentication.",
            _write_channel=WriteChannel.CLI,
        )
        yield memory


@pytest.mark.parametrize(
    "handler,field", [(_handle_context, "query"), (_handle_task_brief, "task")]
)
@pytest.mark.parametrize("ranking", ["hybrid", "hybrid_union"])
def test_mcp_native_context_uses_ranking_and_time(memory, handler, field, ranking):
    result = json.loads(handler({
        field: "secure session cookies", "ranking_strategy": ranking, "format": "json",
        "as_of": "2030-01-01T00:00:00Z", "context_selection": "coverage",
    }, memory))
    assert result["retrieval"]["context_selection"] == "coverage"
    assert result["retrieval"]["direct_ranking_strategy"] == ranking
    assert result["as_of"].startswith("2030-01-01")
    assert not result["abstained"]


def test_context_without_query_does_not_silently_ignore_hybrid(memory):
    with pytest.raises(ValueError, match="query"):
        _handle_context({"ranking_strategy": "hybrid"}, memory)


@pytest.mark.parametrize("ranking", ["hybrid", "hybrid_union"])
def test_cli_brief_uses_native_hybrid(memory, monkeypatch, ranking):
    monkeypatch.setattr(cli, "get_memory", lambda: memory)
    result = CliRunner().invoke(cli.app, [
        "brief", "secure session cookies", "--ranking-strategy", ranking,
        "--as-of", "2030-01-01T00:00:00Z", "--format", "json",
        "--context-selection", "coverage",
    ])
    assert result.exit_code == 0, result.output
    brief = json.loads(result.output)
    assert brief["retrieval"]["direct_ranking_strategy"] == ranking
    assert brief["as_of"].startswith("2030-01-01")


def test_mcp_advertises_native_ranking_and_time():
    tools = {tool.name: tool for tool in build_tool_definitions()}
    for name in ("memory_context", "memory_prepare_task"):
        properties = tools[name].inputSchema["properties"]
        assert properties["ranking_strategy"]["enum"] == ["default", "hybrid", "hybrid_union"]
        assert properties["context_selection"]["enum"] == ["default", "coverage"]
        assert "as_of" in properties

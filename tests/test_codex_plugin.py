import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "llm-memory"


def test_codex_plugin_manifest_is_local_first():
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text())

    assert manifest["name"] == "llm-memory"
    assert manifest["skills"] == "./skills/"
    assert "mcpServers" not in manifest
    assert "apps" not in manifest
    assert "without requiring cloud AI credentials" in manifest["interface"]["longDescription"]


def test_codex_plugin_declares_mcp_server():
    mcp_config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())

    server = mcp_config["mcpServers"]["llm-memory"]
    assert server["command"] == "python3"
    assert server["args"] == ["-m", "llm_memory.interfaces.mcp"]
    assert server["env"]["LLM_MEMORY_STORAGE_MODE"] == "client"
    assert server["env"]["LLM_MEMORY_STORAGE_SERVER_URL"] == "http://127.0.0.1:8001"
    assert server["env"]["LLM_MEMORY_REPO_ID"] == "llm-memory"
    assert server["env"]["LLM_MEMORY_EMBEDDING_PROVIDER"] == "noop"


def test_codex_plugin_skills_cover_required_workflows():
    skills = {
        path.parent.name: path.read_text()
        for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")
    }

    assert set(skills) == {
        "llm-memory-capture",
        "llm-memory-diagnostics",
        "llm-memory-maintenance",
        "llm-memory-recall",
    }
    assert "local direct mode" in skills["llm-memory-recall"]
    assert "llm-memory remember" in skills["llm-memory-recall"]
    assert "memory_remember" in skills["llm-memory-recall"]
    assert "Do not require cloud AI credentials" in skills["llm-memory-recall"]
    assert "Never print API keys" in skills["llm-memory-diagnostics"]

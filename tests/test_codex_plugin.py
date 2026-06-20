import json
from pathlib import Path
from types import SimpleNamespace

from llm_memory.hooks.codex import CodexAdapter

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "llm-memory"
DOCS_ROOT = Path(__file__).resolve().parents[1] / "docs"


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
    # Must match the server's default port (8000) used by config / `serve` /
    # the Codex adapter default, so the plugin connects out of the box.
    assert server["env"]["LLM_MEMORY_STORAGE_SERVER_URL"] == "http://127.0.0.1:8000"
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


def test_codex_generated_guidance_is_scoped_and_current(tmp_path):
    memory = SimpleNamespace(config=SimpleNamespace(repo_id="repo-a"))
    guidance = CodexAdapter(memory, project_root=tmp_path)._workflow_instructions()

    assert "llm-memory remember --repo repo-a" in guidance
    assert 'llm-memory recall "<task>" --repo repo-a' in guidance
    assert "llm-memory inject --file <path> --task" in guidance
    assert "memory_remember" in guidance
    assert "memory_recall" in guidance
    assert "memory_file_context" in guidance
    assert "memory_after_work" in guidance
    assert "repo `repo-a`" in guidance
    assert "Do not print secrets" in guidance
    assert len([line for line in guidance.splitlines() if line.startswith("- ")]) <= 6


def test_memory_governance_docs_cover_required_topics():
    guidance = (DOCS_ROOT / "development" / "ASSISTANT_GUIDANCE.md").read_text()
    governance = (DOCS_ROOT / "development" / "MEMORY_GOVERNANCE.md").read_text()
    release = (DOCS_ROOT / "deployment" / "RELEASE_CHECKLIST.md").read_text()
    combined = f"{guidance}\n{governance}".lower()

    for phrase in [
        "stored data",
        "secrets risk",
        "provider boundaries",
        "local and server modes",
        "retention and deletion",
        "auth defaults",
        "team access",
        "safe capture",
    ]:
        assert phrase in combined

    assert "llm-memory remember" in guidance
    assert "llm-memory recall" in guidance
    assert "memory_after_work" in guidance
    assert "python3 -m pytest tests/server/test_auth.py tests/server/test_collaboration.py" in (
        governance + release
    )

import json
from pathlib import Path
from types import SimpleNamespace

from visp_memory.core.verbs import CAPTURE_VERBS, INTENT_VERBS
from visp_memory.hooks.codex import CodexAdapter

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "visp-memory"
DOCS_ROOT = Path(__file__).resolve().parents[1] / "docs"


def test_codex_plugin_manifest_is_local_first():
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text())

    assert manifest["name"] == "visp-memory"
    assert manifest["skills"] == "./skills/"
    assert "mcpServers" not in manifest
    assert "apps" not in manifest
    assert "without requiring cloud AI credentials" in manifest["interface"]["longDescription"]


def test_codex_plugin_declares_mcp_server():
    mcp_config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())

    server = mcp_config["mcpServers"]["visp-memory"]
    assert server["command"] == "python3"
    assert server["args"] == ["-m", "visp_memory.interfaces.mcp"]
    assert server["env"]["VISP_MEMORY_STORAGE_MODE"] == "client"
    # Must match the server's default port (8000) used by config / `serve` /
    # the Codex adapter default, so the plugin connects out of the box.
    assert server["env"]["VISP_MEMORY_STORAGE_SERVER_URL"] == "http://127.0.0.1:8000"
    assert server["env"]["VISP_MEMORY_REPO_ID"] == "visp-memory"
    assert server["env"]["VISP_MEMORY_EMBEDDING_PROVIDER"] == "noop"


def test_codex_plugin_skills_cover_required_workflows():
    skills = {
        path.parent.name: path.read_text()
        for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")
    }

    assert set(skills) == {
        "visp-memory-capture",
        "visp-memory-diagnostics",
        "visp-memory-maintenance",
        "visp-memory-recall",
    }
    assert "local direct mode" in skills["visp-memory-recall"]
    assert "visp-memory remember" in skills["visp-memory-recall"]
    assert "memory_remember" in skills["visp-memory-recall"]
    assert "Do not require cloud AI credentials" in skills["visp-memory-recall"]
    assert "Never print API keys" in skills["visp-memory-diagnostics"]


def test_codex_generated_guidance_is_scoped_and_current(tmp_path):
    memory = SimpleNamespace(config=SimpleNamespace(repo_id="repo-a"))
    guidance = CodexAdapter(memory, project_root=tmp_path)._workflow_instructions()

    assert "visp-memory remember --repo repo-a" in guidance
    assert 'visp-memory recall "<task>" --repo repo-a' in guidance
    assert "visp-memory inject --file <path> --task" in guidance
    assert "memory_remember" in guidance
    assert "memory_recall" in guidance
    assert "memory_file_context" in guidance
    assert "memory_after_work" in guidance
    assert "repo `repo-a`" in guidance
    assert "Do not print secrets" in guidance
    assert len([line for line in guidance.splitlines() if line.startswith("- ")]) <= 6


def test_memory_governance_docs_cover_required_topics():
    guidance = (DOCS_ROOT / "development" / "MCP.md").read_text()
    governance = (DOCS_ROOT / "TRUST.md").read_text()
    release = (DOCS_ROOT / "deployment" / "RELEASING.md").read_text()
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

    assert "visp-memory remember" in guidance
    assert "visp-memory recall" in guidance
    assert "memory_after_work" in guidance
    assert "python3 -m pytest tests/server/test_auth.py tests/server/test_collaboration.py" in (
        governance + release
    )


def test_codex_generated_guidance_names_every_intent_verb(tmp_path):
    """The block said "follow-up goals" in prose and named no intent verb.

    An assistant reading its own AGENTS.md could not learn the lifecycle existed,
    which is why no round ever set an intent.
    """
    memory = SimpleNamespace(config=SimpleNamespace(repo_id="repo-a"))
    guidance = CodexAdapter(memory, project_root=tmp_path)._workflow_instructions()

    for verb in INTENT_VERBS:
        assert f"visp-memory {verb.name}" in guidance, verb.name


def test_codex_generated_guidance_names_the_intent_mcp_tools_it_has(tmp_path):
    memory = SimpleNamespace(config=SimpleNamespace(repo_id="repo-a"))
    guidance = CodexAdapter(memory, project_root=tmp_path)._workflow_instructions()

    for verb in INTENT_VERBS:
        if verb.mcp_tool:
            assert verb.mcp_tool in guidance, verb.mcp_tool


def test_codex_generated_guidance_does_not_invent_an_mcp_tool_for_focus(tmp_path):
    """`focus` is CLI-only. Naming a tool that does not exist is a dead end."""
    memory = SimpleNamespace(config=SimpleNamespace(repo_id="repo-a"))
    guidance = CodexAdapter(memory, project_root=tmp_path)._workflow_instructions()

    assert "memory_focus" not in guidance


def test_codex_generated_guidance_says_which_verbs_have_no_mcp_tool(tmp_path):
    """Four verbs against three tools would otherwise be paired off in order."""
    memory = SimpleNamespace(config=SimpleNamespace(repo_id="repo-a"))
    guidance = CodexAdapter(memory, project_root=tmp_path)._workflow_instructions()

    assert "`focus` is CLI-only" in guidance


def test_codex_generated_guidance_names_the_intent_group_commands(tmp_path):
    memory = SimpleNamespace(config=SimpleNamespace(repo_id="repo-a"))
    guidance = CodexAdapter(memory, project_root=tmp_path)._workflow_instructions()

    assert "visp-memory intent list|update|complete|close" in guidance


def test_codex_generated_guidance_names_every_capture_verb(tmp_path):
    memory = SimpleNamespace(config=SimpleNamespace(repo_id="repo-a"))
    guidance = CodexAdapter(memory, project_root=tmp_path)._workflow_instructions()

    for verb in CAPTURE_VERBS:
        assert f"visp-memory {verb.name}" in guidance, verb.name


def test_codex_generated_guidance_never_claims_an_intent_grants_authority(tmp_path):
    """Rule 9: memory is non-authoritative. Direction is not permission."""
    memory = SimpleNamespace(config=SimpleNamespace(repo_id="repo-a"))
    guidance = CodexAdapter(memory, project_root=tmp_path)._workflow_instructions()

    assert "direction, not permission" in guidance
    assert "does not grant" in guidance


def test_codex_installed_agents_md_carries_the_intent_verbs(tmp_path):
    """The rendered file, not just the helper, is what an assistant reads."""
    memory = SimpleNamespace(config=SimpleNamespace(repo_id="repo-a"))
    adapter = CodexAdapter(
        memory, project_root=tmp_path, config_path=tmp_path / "codex-config.toml"
    )

    adapter.install()
    written = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    for verb in INTENT_VERBS:
        assert f"visp-memory {verb.name}" in written, verb.name


def test_assistant_guidance_doc_describes_the_intent_lifecycle():
    guidance = (DOCS_ROOT / "development" / "MCP.md").read_text()

    for verb in INTENT_VERBS:
        assert f"visp-memory {verb.name}" in guidance, verb.name
    assert "direction, not permission" in guidance.lower()


def test_capture_and_recall_skills_both_reach_the_intent_lifecycle():
    """Only the capture skill mentioned intents, and only after the fact."""
    skills = {
        path.parent.name: path.read_text()
        for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")
    }

    recall = skills["visp-memory-recall"]
    for verb in ("goal", "focus", "working"):
        assert f"visp-memory {verb}" in recall, verb
    assert "direction, not permission" in recall

    capture = skills["visp-memory-capture"]
    assert "visp-memory done" in capture
    assert "visp-memory intent complete" in capture
    # `done` records completion IN MEMORY; it does not make a task done.
    assert "does not make the task done" in capture

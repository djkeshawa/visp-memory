"""Generated client instructions must use the actual default tool surface."""

import re
from types import SimpleNamespace

from visp_memory.hooks.codex import CodexAdapter
from visp_memory.interfaces.mcp_tools import CORE_TOOL_NAMES


def test_generated_guidance_uses_callable_core_tools(tmp_path):
    adapter = CodexAdapter(SimpleNamespace(config=SimpleNamespace(repo_id="r")),
                           project_root=tmp_path, config_path=tmp_path / "config.toml")
    adapter.install()
    instructions = (tmp_path / "AGENTS.md").read_text()
    tools = set(re.findall(r"`(memory_[a-z_]+)`", instructions))
    assert tools <= CORE_TOOL_NAMES
    assert "memory_prepare_task" in tools

"""LC-90 — the agent-facing surface names its score, because the agent quotes it.

THE DEFECT THIS PINS. `memory_recall` returned "(similarity: 0.67)" whether the
number came from a vector comparison or from `text_similarity`. An agent reading
that has no other source for what the number means, and two battle-ground rounds
duly wrote lexical overlap into their notes as semantic similarity.

This matters more here than on the CLI. A person may notice that a paraphrase
search comes back empty; an agent takes the number at face value and passes it
on as a measurement.
"""

import tempfile
from pathlib import Path

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.embedding_status import LEXICAL_SCORE_LABEL

pytest.importorskip("mcp")


def _memory(tmpdir: str) -> Memory:
    config = MemoryConfig(repo_id="lc90-repo")
    config.storage.data_dir = Path(tmpdir)
    config.storage.backend = "sqlite"
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    # noop is what got built; `auto` is what was asked for. Reporting the
    # difference is the whole ticket, and the banner only fires for degradation
    # the operator did not choose.
    memory.config.embedding.provider = "auto"
    memory.learn("Auth tokens rotate every 15 minutes")
    return memory


class TestRecallOverMcpUnderNoopEmbeddings:
    @pytest.mark.asyncio
    async def test_the_score_is_named_lexical_overlap_not_similarity(self):
        from visp_memory.interfaces.mcp import handle_tool

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            result = await handle_tool("memory_recall", {"query": "auth tokens"}, _memory(tmpdir))

        assert LEXICAL_SCORE_LABEL in result
        assert "(similarity:" not in result

    @pytest.mark.asyncio
    async def test_the_results_carry_the_reason_and_the_repair(self):
        from visp_memory.interfaces.mcp import handle_tool

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            result = await handle_tool("memory_recall", {"query": "auth tokens"}, _memory(tmpdir))

        assert "not semantic similarity" in result
        assert "visp-memory[local-embeddings]" in result

    @pytest.mark.asyncio
    async def test_an_empty_result_says_why_it_may_be_empty(self):
        # "No memories found" over a store that holds the answer under a synonym
        # is the failure mode with no other symptom.
        from visp_memory.interfaces.mcp import handle_tool

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            result = await handle_tool(
                "memory_recall", {"query": "credential lifetime policy"}, _memory(tmpdir)
            )

        assert result.startswith("No memories found matching query.")
        assert "matching keywords" in result

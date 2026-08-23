"""LC-90 — the CLI and the server say the same sentence about the same state.

THE DEFECT THIS PINS. Three surfaces described the noop fallback and all three
wrote their own words for it: the CLI said "Fallback provider in use." with no
repair, the server said "No embedding driver connected; using noop embeddings.",
and the only text that actually told anyone what to install lived in a
`logger.info` nothing on the CLI path configures.

Duplicated status text drifts, and the half that drifted was the actionable
half. The wording now lives in `visp_memory.core.embedding_status`; these tests
fail if a surface goes back to inventing its own.
"""

from visp_memory.config import EmbeddingConfig
from visp_memory.core.embedding_status import (
    DISABLED_STATUS_MESSAGE,
    ENABLE_SEMANTIC_RECALL_REMEDIATION,
    FALLBACK_STATUS_MESSAGE,
)
from visp_memory.server.routers.diagnostics import build_provider_status


class TestTheServerOffersTheSameRepairAsTheCli:
    def test_the_noop_row_carries_the_remediation(self):
        # This is the row a dashboard or a host marks active when auto-selection
        # fell through, and it was the one row with no repair attached.
        status = build_provider_status(
            "noop",
            config=EmbeddingConfig(provider="auto"),
            effective_provider="noop",
            runtime_status={
                "embedding_driver_status": "fallback",
                "embedding_driver_connected": False,
                "embedding_status_message": FALLBACK_STATUS_MESSAGE,
            },
        )

        assert status.connected is False
        assert status.action_hint == ENABLE_SEMANTIC_RECALL_REMEDIATION

    def test_a_deliberately_selected_noop_reports_the_shared_disabled_message(self):
        status = build_provider_status("noop", config=EmbeddingConfig(provider="noop"))

        assert status.status == "disabled"
        assert status.message == DISABLED_STATUS_MESSAGE

    def test_the_server_fallback_message_is_the_one_the_cli_prints(self):
        from visp_memory.config import MemoryConfig
        from visp_memory.server.app import get_server_embedding_runtime

        config = MemoryConfig()
        config.embedding = EmbeddingConfig(provider="noop")
        _, runtime = get_server_embedding_runtime(config)

        assert runtime["embedding_status_message"] == DISABLED_STATUS_MESSAGE

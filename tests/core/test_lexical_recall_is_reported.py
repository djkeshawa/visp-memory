"""LC-90 — Memory can say that its own scores are keyword overlap.

THE DEFECT THIS PINS. `visp-memory doctor` reported `configured=auto,
effective=noop` for the whole life of this project and nothing downstream asked
the difference. Every recall ranked on `text_similarity`, and the numbers it
produced were quoted in two battle-ground rounds as semantic similarity.

Nothing could label them honestly because nothing could be asked. `Memory` kept
the embedding function and threw the provider away, so the one fact that decides
whether a score means anything was unavailable to every surface above it.

Two independent ways to end up ranking on keywords, and both must answer True —
guarding only the noop provider would leave a connected provider with no vector
index reporting semantic scores it never computed.
"""

import tempfile
from pathlib import Path

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.embedding_status import (
    ENABLE_SEMANTIC_RECALL_REMEDIATION,
    ENABLE_VECTOR_INDEX_REMEDIATION,
    PROVIDER_INIT_FAILED,
    is_noop_provider,
    produces_no_vectors,
)


def _memory(provider: str) -> Memory:
    config = MemoryConfig(repo_id="lc90")
    config.storage.data_dir = Path(tempfile.mkdtemp())
    config.storage.backend = "sqlite"
    config.embedding.provider = provider
    return Memory(config=config)


def _fell_through_to_noop() -> Memory:
    """The ticket's state: `auto` was asked for and noop is what got built."""
    memory = _memory("noop")
    memory.config.embedding.provider = "auto"
    return memory


class TestMemoryReportsHowItRanked:
    def test_a_noop_provider_reports_its_scores_as_lexical(self):
        memory = _memory("noop")

        assert memory.recall_scores_are_lexical is True

    def test_the_provider_actually_built_is_reported_not_the_one_configured(self):
        memory = _memory("noop")

        assert memory.embedding_provider_name == "noop"

    def test_disabling_embeddings_outright_also_reports_lexical(self):
        # "none" leaves embedding_fn unset rather than yielding a constant vector.
        # Different mechanism, same consequence for a score.
        memory = _memory("none")

        assert memory.recall_scores_are_lexical is True

    def test_a_connected_provider_without_a_vector_index_still_reports_lexical(self):
        memory = _memory("noop")
        # A real provider name, so the noop guard does not fire, over a backend
        # reporting no vector search. This is a live configuration: sqlite with
        # sentence-transformers installed but ChromaDB absent.
        memory._embedding_provider_name = "sentence-transformers"

        assert memory.recall_scores_are_lexical is True

    def test_client_mode_declines_to_answer_rather_than_guessing(self):
        memory = _memory("noop")
        # The server picks its own provider; this process cannot speak for it,
        # and a confident False here would be the original defect inverted.
        memory._embedding_provider_name = None

        assert memory.recall_scores_are_lexical is None
        assert memory.lexical_recall_remediation is None


class TestTheRemediationMatchesTheCause:
    def test_a_missing_provider_is_told_to_get_a_provider(self):
        memory = _fell_through_to_noop()

        assert memory.lexical_recall_remediation == ENABLE_SEMANTIC_RECALL_REMEDIATION

    def test_a_missing_vector_index_is_not_told_to_reinstall_the_provider(self):
        memory = _fell_through_to_noop()
        memory._embedding_provider_name = "sentence-transformers"

        assert memory.lexical_recall_remediation == ENABLE_VECTOR_INDEX_REMEDIATION

    def test_a_provider_the_operator_pinned_off_gets_no_repair(self):
        # `none` is documented as the correct way to turn vector search off on a
        # backend that runs its own index. Telling that operator to install
        # embeddings is advice that cannot work, repeated on every recall.
        memory = _memory("none")

        assert memory.recall_scores_are_lexical is True
        assert memory.lexical_recall_remediation is None

    def test_a_provider_that_failed_to_start_is_not_reported_as_a_choice(self):
        # A host reading this field must be able to raise a fault as a fault.
        memory = _fell_through_to_noop()
        memory._embedding_provider_name = PROVIDER_INIT_FAILED

        assert memory.embedding_provider_name == PROVIDER_INIT_FAILED
        assert memory.recall_scores_are_lexical is True
        assert memory.lexical_recall_remediation == ENABLE_SEMANTIC_RECALL_REMEDIATION

    def test_the_remediation_names_the_extra_a_user_must_install(self):
        # Rich deletes unescaped square brackets, so this exact substring is what
        # the CLI test asserts survives to the terminal.
        assert "visp-memory[local-embeddings]" in ENABLE_SEMANTIC_RECALL_REMEDIATION


@pytest.mark.parametrize("name", ["noop", "none", "NOOP", " None "])
def test_every_spelling_of_no_vectors_is_recognised(name):
    assert is_noop_provider(name) is True


@pytest.mark.parametrize("name", ["openai", "sentence-transformers", "ollama", None, ""])
def test_a_real_provider_is_not_mistaken_for_noop(name):
    assert is_noop_provider(name) is False


@pytest.mark.parametrize("name", ["noop", "none", PROVIDER_INIT_FAILED])
def test_every_state_without_vectors_is_recognised_as_such(name):
    assert produces_no_vectors(name) is True


def test_a_failed_provider_is_not_confused_with_a_chosen_one():
    # Both rank on keywords; only one of them is a fault.
    assert is_noop_provider(PROVIDER_INIT_FAILED) is False

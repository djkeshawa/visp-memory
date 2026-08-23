"""The one wording for what the active embedding provider means for recall.

Three surfaces reported the same fact in three different ways and none of them
reached a person running the CLI. `_warn_noop_fallback()` in
:mod:`visp_memory.core.embeddings` already carried a good remediation -- install
the local-embeddings extra, set an API key, or run Ollama -- but it is a
``logger.info`` that nothing on the CLI path configures, so in practice it only
ever appeared in server stdout. The CLI diagnostics said "Fallback provider in
use." and stopped at the status word, and recall said nothing at all while
printing lexical overlap under a column called "Score".

The text lives here so the log line, the diagnostics payload, the recall banner
and the server's provider hints are the same sentence, and so a number produced
without vectors is never presented as if it had them.
"""

from typing import Optional

#: Provider names that mean "no vectors are in play".
#:
#: ``noop`` yields a constant vector and ``none`` disables embeddings outright.
#: Every local backend routes recall through its keyword/text path for both, so
#: for the purpose of labelling a score they are the same state.
NOOP_PROVIDER_NAMES = frozenset({"noop", "none"})

#: What is actually happening. Stated without alarm: this is a supported
#: configuration, not a fault -- results are still ranked.
LEXICAL_FALLBACK_NOTICE = (
    "Semantic embeddings are not configured; recall is using keyword search. "
    "Results are still ranked, but paraphrase matching is unavailable."
)

#: What to do about it. The single remediation string; every surface that
#: reports the fallback ends here rather than inventing its own wording.
ENABLE_SEMANTIC_RECALL_REMEDIATION = (
    "To enable semantic recall: pip install 'visp-memory[local-embeddings]', or set "
    "OPENROUTER_API_KEY / OPENAI_API_KEY (or EMBEDDING_API_KEY), or run Ollama."
)

#: The other way to end up ranking on keywords, which needs a different repair:
#: the provider connects, but nothing indexes what it produces. Offering the
#: provider remediation here would send someone to install what they already have.
ENABLE_VECTOR_INDEX_REMEDIATION = (
    "An embedding provider is connected but this store has no vector index, so recall "
    "still ranks on keywords: install ChromaDB with pip install 'visp-memory[chroma]' "
    "on the sqlite backend, or use a backend that does vector search."
)

#: The process-level log line, unchanged from what it has always said.
NOOP_FALLBACK_LOG_MESSAGE = f"{LEXICAL_FALLBACK_NOTICE} {ENABLE_SEMANTIC_RECALL_REMEDIATION}"

#: Diagnostics status messages. The status word alone ("fallback") told nobody
#: what it cost them, which is how lexical scores were read as semantic ones.
FALLBACK_STATUS_MESSAGE = "Fallback provider in use; recall matches keywords, not meaning."
DISABLED_STATUS_MESSAGE = "Embeddings disabled; recall matches keywords, not meaning."

#: The one-line banner `recall` prints when it is running degraded, and the
#: header for the score column underneath it. The banner is deliberately scoped
#: to commands whose own output is affected: a notice on every unrelated
#: invocation is the defect `_warn_noop_fallback` was demoted to INFO to fix.
LEXICAL_RECALL_BANNER = (
    "Degraded: recall is matching keywords, not meaning — scores are lexical overlap "
    "with the query, not semantic similarity."
)
LEXICAL_SCORE_HEADER = "Lexical"

#: Inline label for a single score on the MCP surface, where an agent reads the
#: number and may quote it onward.
LEXICAL_SCORE_LABEL = "lexical overlap"
DEFAULT_SCORE_LABEL = "similarity"


def is_noop_provider(provider_name: Optional[str]) -> bool:
    """Report whether a provider name means recall is ranking on keywords."""
    return (provider_name or "").strip().lower() in NOOP_PROVIDER_NAMES

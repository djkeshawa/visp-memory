"""Task-scoped recall for the machine contract.

:meth:`Memory.recall` ranks by text. A coordinator knows more than the words it
typed — which task it is on, which files it is about to touch, which constraints
must survive — and :mod:`visp_memory.core.hybrid_retrieval` has been able to use
that since 0.5.0, fusing intel's code-graph projection into retrieval so a memory
recorded against a file the task *imports* can surface even when no vocabulary
overlaps. Only :class:`~visp_memory.core.context_compiler.ContextCompiler` and its
callers could reach it. The machine contract — the one surface the Visp
coordinator speaks — could not, so every contract recall was decided by text
similarity alone.

This module is the wiring, and the bounds on it. What it may do:

* **Add, never remove or reorder.** The text recall is returned first, in its own
  order, with its own scores. Admissions are appended. The result is a superset of
  the no-files result, so naming a file cannot cost the coordinator a memory.
* **Admit only what the task's own code reached.** A memory is admitted only if the
  retriever's *entity* channel (identity: the memory is tagged with a file or symbol
  in scope) or its *structure* channel (within two import/test hops, per intel's
  snapshot) found it. The retriever's text channel opens at 0.16 while recall's
  floor is 0.56; admitting text hits from that gap would make ``--file`` a silent
  precision switch rather than a structural signal.
* **Stay bounded.** At most :data:`MAX_ADMISSIONS` per recall — the retriever's own
  literal, because that is where this bound is already stated and argued.

Everything else degrades to the text path exactly: no files, no projection, an
unreadable or superseded projection, an empty corpus. Each of those says which it
was, on :meth:`TaskScopedRecall.diagnostics`, because a silent ``None`` is how an
untestable claim gets made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from visp_memory.core.clock import parse_utc, utc_now
from visp_memory.core.eligibility import assess_recall_eligibility, require_repo_id
from visp_memory.core.hybrid_retrieval import STRUCTURAL_MAX_ADMISSIONS, HybridRetriever

#: The retrieval channels that mean "the task's own code reached this memory".
#: ``direct`` (text) and ``graph`` (the associative walk over memory relationships)
#: are deliberately absent: neither is evidence about the files in scope, and the
#: first would lower this path's relevance floor without saying so.
CODE_CHANNELS = frozenset({"entity", "structure"})

#: How many memories one task-scoped recall may add on top of the text recall.
MAX_ADMISSIONS = STRUCTURAL_MAX_ADMISSIONS

#: How deep to let the retriever rank before admissions are chosen. Wide enough
#: that a low-scoring identity match is still visible, bounded so a large corpus
#: cannot turn one contract call into an unbounded scan.
RETRIEVAL_WINDOW = 80

STRATEGY_TEXT = "text"
STRATEGY_TEXT_AND_CODE = "text+code"


@dataclass(frozen=True)
class TaskScopedRecall:
    """What a task-scoped recall returned, and how much of it was structural."""

    results: list[dict[str, Any]]
    strategy: str
    admissions: list[dict[str, Any]] = field(default_factory=list)
    code_graph: Optional[dict[str, Any]] = None

    def diagnostics(self) -> dict[str, Any]:
        """The account a coordinator can act on when structure did nothing."""
        report: dict[str, Any] = {
            "strategy": self.strategy,
            "textResults": len(self.results) - len(self.admissions),
            "admitted": len(self.admissions),
        }
        if self.code_graph is not None:
            report["codeGraph"] = self.code_graph
        return report


def admission_reason(memory: dict[str, Any]) -> str:
    """Why this memory is in the result, in the words a caller can pass on."""
    channels = set(memory.get("retrieval_channels") or ())
    if "entity" in channels:
        return "file-match"
    if "structure" in channels:
        return "import-proximity"
    return "text"


def structural_caveat(memory: dict[str, Any]) -> Optional[str]:
    """The warning an import-proximity admission must always carry.

    A memory one import hop away is *not* about the file being edited. Presenting
    it without saying so is the failure mode this whole signal has, so the caveat
    is produced here rather than left to each caller to remember.
    """
    if "structure" not in set(memory.get("retrieval_channels") or ()):
        return None
    factors = memory.get("retrieval_factors") or {}
    hops = factors.get("structural_hops")
    snapshot = str(factors.get("code_graph_snapshot") or "")
    where = f"{hops} import/test hop{'s' if hops != 1 else ''}" if hops else "an import edge"
    trailer = f", per intel snapshot …{snapshot[-12:]}" if snapshot else ""
    return (
        f"reached through the code graph — {where} from the task's files{trailer}. "
        "It is near this work, not necessarily about it."
    )


def recall_for_task(
    memory,
    query: str,
    *,
    repo_id: Optional[str],
    limit: int = 10,
    min_score: Optional[float] = None,
    task: Optional[str] = None,
    files: Optional[list[str]] = None,
    symbols: Optional[list[str]] = None,
    constraints: Optional[list[str]] = None,
    session_id: Optional[str] = None,
    environment: Any = None,
    task_type: Any = None,
    as_of: Any = None,
) -> TaskScopedRecall:
    """Recall by text, then add what the task's own code reached and text missed."""
    repo_id = require_repo_id(repo_id)
    files = _clean(files)
    symbols = _clean(symbols)
    as_of_time = _resolve_as_of(as_of)

    recall_kwargs = {} if min_score is None else {"min_score": min_score}
    results = memory.recall(
        query,
        repo_id=repo_id,
        limit=limit,
        task=task,
        files=files or None,
        constraints=_clean(constraints) or None,
        session_id=session_id,
        environment=environment or None,
        task_type=task_type or None,
        # Only forwarded when the caller asked for a point in time. `recall`
        # defaults to now on its own, and a remote backend puts whatever arrives
        # here on the wire — so "no as_of" must stay absent, not become a
        # timestamp this module invented.
        as_of=None if as_of is None else as_of_time,
        **recall_kwargs,
    )
    if not (files or symbols):
        return TaskScopedRecall(results=list(results), strategy=STRATEGY_TEXT)

    graph = memory.code_graph(repo_id)
    admissions = _code_admissions(
        memory,
        query,
        repo_id=repo_id,
        files=files,
        symbols=symbols,
        graph=graph,
        already={str(item.get("id")) for item in results},
        environment=environment,
        task_type=task_type,
        as_of=as_of_time,
    )
    return TaskScopedRecall(
        results=list(results) + admissions,
        strategy=STRATEGY_TEXT_AND_CODE,
        admissions=admissions,
        code_graph=dict(memory.last_code_graph_load or {}),
    )


def _code_admissions(
    memory,
    query: str,
    *,
    repo_id: str,
    files: list[str],
    symbols: list[str],
    graph,
    already: set[str],
    environment: Any,
    task_type: Any,
    as_of: Any,
) -> list[dict[str, Any]]:
    ranked = HybridRetriever(memory._storage, code_graph=graph).retrieve(
        query,
        repo_id=repo_id,
        files=files,
        symbols=symbols,
        limit=RETRIEVAL_WINDOW,
        candidate_filter=_eligibility_gate(
            repo_id=repo_id, environment=environment, task_type=task_type, as_of=as_of
        ),
        environment=environment or None,
        task_type=task_type or None,
        as_of=as_of,
    )
    candidates = [
        item
        for item in ranked
        if str(item.get("id")) not in already
        and CODE_CHANNELS.intersection(item.get("retrieval_channels") or ())
    ]
    # Identity outranks proximity without a special case: the retriever floors an
    # exact match at ENTITY_EXACT_FLOOR and caps a structural admission strictly
    # below it, so score order already means "about this file, then near it".
    candidates.sort(key=lambda item: (-float(item.get("relevance_score") or 0.0), str(item["id"])))
    return candidates[:MAX_ADMISSIONS]


def _eligibility_gate(*, repo_id: str, environment: Any, task_type: Any, as_of: Any):
    """The read-eligibility contract as a candidate filter.

    The retriever reads storage directly, so without this the structural channel
    would be a way around the trust and scope controls `Memory.recall` applies —
    the admissions would be the only rows in the result nobody had checked.
    """

    def gate(candidate: dict[str, Any]) -> bool:
        return assess_recall_eligibility(
            candidate,
            repo_id=repo_id,
            environment=environment or None,
            task_type=task_type or None,
            as_of=as_of,
        ).eligible

    return gate


def _clean(values: Optional[list[str]]) -> list[str]:
    return [str(value).strip() for value in (values or []) if str(value).strip()]


def _resolve_as_of(as_of: Any):
    if as_of is None:
        return utc_now()
    parsed = parse_utc(as_of)
    if parsed is None:
        raise ValueError("as_of must be a valid timestamp")
    return parsed

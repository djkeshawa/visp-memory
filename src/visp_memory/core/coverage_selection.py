"""Deterministic, verbatim passage selection with source-coordinate lineage."""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from typing import Any, Callable

from visp_memory.core.ranking import _LEXICAL_STOPWORDS
from visp_memory.core.source_passages import MAX_TURN_CHARS, attributed_passage, turn_ranges


def is_calendar_date(value: str) -> bool:
    value = value.rstrip(".")
    if not re.fullmatch(r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}/\d{1,2}/\d{4})", value):
        return False
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False


def validate_context_selection(value: str) -> None:
    if value not in ("default", "coverage"):
        raise ValueError("context_selection must be 'default' or 'coverage'")


def terms(text: str) -> set[str]:
    stopwords = (_LEXICAL_STOPWORDS - {"no", "not"}) | {
        "question", "date", "user", "assistant", "system", "session",
        "do", "does", "did", "can", "could", "would", "should", "were", "be",
        "which", "who", "many", "much", "my", "me", "you", "your", "have",
    }
    return {term for term in re.findall(r"[a-z0-9_]+", text.casefold())
            if len(term) > 1 and term not in stopwords}


@lru_cache(maxsize=4096)
def _word_forms(word: str) -> frozenset[str]:
    """Soft lexical matches only; never rewrite evidence or its identifiers."""
    forms = {word}
    if word.isalpha() and len(word) > 4:
        if word.endswith("ies"):
            forms.add(word[:-3] + "y")
        elif word.endswith(("sses", "ches", "shes", "xes", "zes")):
            forms.add(word[:-2])
        elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
            forms.add(word[:-1])
        if word.endswith("ed"):
            forms.update((word[:-2], word[:-1]))
        elif word.endswith("ing") and len(word) > 5:
            forms.update((word[:-3], word[:-3] + "e"))
    return frozenset(forms)


def _matching_terms(text: str, query_terms: set[str]) -> set[str]:
    forms = {form for word in terms(text) for form in _word_forms(word)}
    return {word for word in query_terms if _word_forms(word) & forms}


def _query_terms(query: str) -> set[str]:
    # A supplied reference clock is interpretation context, not evidence to fill
    # the brief with. Dates explicitly asked about in the question remain terms.
    return terms(re.sub(r"(?im)^Question date:[^\n]*\n?", "", query))


def _spans(text: str) -> list[tuple[int, int]]:
    # Sentence boundaries keep amounts and negation intact; oversized sentences
    # stay intact rather than pretending a clipped clause is complete evidence.
    boundaries = [0, *(m.end() for m in re.finditer(r"\n|(?<=[.!?])\s+(?=[A-Z])", text)), len(text)]
    return [(a, b) for a, b in zip(boundaries, boundaries[1:]) if text[a:b].strip()]


def coverage_candidates(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    query_terms = _query_terms(query)
    result = []
    source_rows = {row["id"]: row for row in rows}
    for row in _coalesce_source_copies(rows):
        if row.get("passage_spans"):
            result.append(row)
            continue
        text = row.get("content", "")
        spans = []
        for a, b in turn_ranges(text):
            if b - a <= MAX_TURN_CHARS:
                spans.append((a, b))
            else:
                sentences = [(a + c, a + d) for c, d in _spans(text[a:b])]
                for index, sentence in enumerate(sentences):
                    left = sentences[max(0, index - 1)][0]
                    right = sentences[min(len(sentences) - 1, index + 1)][1]
                    spans.append((left, right) if right - left <= MAX_TURN_CHARS else sentence)
        scored = sorted(
            set(spans),
            key=lambda s: (
                -len(_matching_terms(text[s[0] : s[1]], query_terms)),
                s[0],
            ),
        )
        has_matching = any(_matching_terms(text[a:b], query_terms) for a, b in spans)
        # Bound CPU work while allowing distinct relevant turns to compete for
        # the final budget instead of discarding all but three sentence matches.
        for a, b in scored[:32]:
            result.append({
                **attributed_passage(row, a, b),
                "_focus_span": {"start": a, "end": b, "text": text[a:b]},
                "_source_focus": _focus_origin(row, a, b, source_rows),
                "_has_matching_passage": has_matching,
            })
    return result


def _focus_origin(row, start, end, source_rows):
    """Map only unique verbatim matches through an eligible direct source."""
    sources = row.get("source_ids") or []
    source = source_rows.get(sources[0]) if len(sources) == 1 else None
    if source and not source.get("passage_spans"):
        text = row["content"][start:end]
        original = source.get("content", "")
        offset = original.find(text)
        if offset >= 0 and original.find(text, offset + 1) < 0:
            return source["id"], offset, offset + len(text)
    return row["id"], start, end


def _passage_identity(row):
    offsets = tuple((s["start"], s["end"]) for s in row.get("passage_spans", []))
    return str(row["id"]), offsets, row.get("content", "")


def _evidence_key(row):
    origin = row.get("_source_focus")
    return (tuple(origin), row.get("content", "")) if origin else None


def _coalesce_source_copies(rows):
    """Share budget only for identical text with explicit, eligible source lineage."""
    by_id = {row["id"]: row for row in rows}
    grouped = {}
    for row in rows:
        sources = row.get("source_ids") or []
        source = by_id.get(sources[0]) if len(sources) == 1 else None
        canonical = source if source and source.get("content") == row.get("content") else row
        key = canonical["id"]
        prior = grouped.get(key, canonical)
        grouped[key] = {
            **prior,
            "relevance_score": max(float(r.get("relevance_score") or r.get("similarity") or 0)
                                   for r in (prior, row)),
            "retrieval_channels": sorted(set(prior.get("retrieval_channels") or [])
                                         | set(row.get("retrieval_channels") or [])),
        }
    return list(grouped.values())


def _new_focus_text(candidate, selected):
    focus = candidate.get("_focus_span")
    if focus is None:
        return candidate.get("content", "")
    ranges = [(focus["start"], focus["end"])]
    for prior in selected:
        if prior["id"] != candidate["id"]:
            continue
        for span in prior.get("passage_spans", []):
            remaining = []
            for a, b in ranges:
                if span["end"] <= a or span["start"] >= b:
                    remaining.append((a, b))
                else:
                    if a < span["start"]:
                        remaining.append((a, span["start"]))
                    if span["end"] < b:
                        remaining.append((span["end"], b))
            ranges = remaining
    return " ".join(focus["text"][a - focus["start"]:b - focus["start"]] for a, b in ranges)


def is_direct(row: dict[str, Any]) -> bool:
    return "direct" in (row.get("retrieval_channels") or [])


def _append_passage(selected, candidate):
    """Merge overlapping source spans under one memory citation."""
    prior = next((r for r in selected if r["id"] == candidate["id"]), None)
    if prior is None or not candidate.get("passage_spans"):
        return [*selected, candidate]
    spans = sorted(
        [*prior["passage_spans"], *candidate["passage_spans"]], key=lambda s: (s["start"], s["end"])
    )
    merged = []
    for span in spans:
        if merged and span["start"] <= merged[-1]["end"]:
            old = merged[-1]
            overlap = max(0, old["end"] - span["start"])
            old["text"] += span["text"][overlap:]
            old["end"] = max(old["end"], span["end"])
        else:
            merged.append(dict(span))
    replacement = {
        **prior,
        "passage_spans": merged,
        "content": "\n[…]\n".join(s["text"] for s in merged),
    }
    return [replacement if r is prior else r for r in selected]


def select_coverage(
    rows: list[dict[str, Any]],
    query: str,
    budget: int,
    cost: Callable[[list[dict[str, Any]]], int],
) -> list[dict[str, Any]]:
    """Reserve half the evidence budget for direct matches, then share capacity.

    ``cost`` may render the whole brief, so it is never called once per candidate
    per step. Candidates are valued by a cached marginal cost measured against at
    most one prior passage of the same memory, and the exact ``cost`` is checked
    only for the candidate about to be taken. Rendered cost never shrinks as the
    selection grows, so a candidate that does not fit stays excluded for the rest
    of the phase. The budget therefore remains exact.
    """
    selected: list[dict[str, Any]] = []
    covered: set[str] = set()
    pending = list(rows)
    query_terms = _query_terms(query)
    base = cost([])
    direct_limit = base + max(0, budget - base) // 2
    seen: set[tuple[Any, ...]] = set()
    selected_evidence: set[tuple[Any, ...]] = set()
    matches_by_text: dict[str, set[str]] = {}
    source_matches: dict[str, set[str]] = {}
    single_costs: dict[tuple[Any, ...], int] = {(): base}
    marginal_costs: dict[tuple[Any, ...], int] = {}

    def cost_of(items: list[dict[str, Any]]) -> int:
        key = tuple(_passage_identity(item) for item in items)
        if key not in single_costs:
            single_costs[key] = cost(items)
        return single_costs[key]

    def marginal(row: dict[str, Any]) -> int:
        prior = next((r for r in selected if r["id"] == row["id"]), None)
        before = [prior] if prior else []
        key = (_passage_identity(row), _passage_identity(prior) if prior else None)
        if key not in marginal_costs:
            marginal_costs[key] = cost_of(_append_passage(before, row)) - cost_of(before)
        return marginal_costs[key]

    identities = {id(row): _passage_identity(row) for row in rows}
    for direct_only, limit in ((True, direct_limit), (False, budget)):
        too_large: set[tuple[Any, ...]] = set()
        while pending:
            eligible = [r for r in pending if not direct_only or is_direct(r)]
            if not eligible:
                break

            selected_identities = {_passage_identity(r) for r in selected}
            choices = []
            for row in eligible:
                identity = identities[id(row)]
                if identity in too_large or identity in selected_identities:
                    continue
                if _evidence_key(row) in selected_evidence:
                    continue
                focus_text = _new_focus_text(row, selected)
                if focus_text not in matches_by_text:
                    matches_by_text[focus_text] = _matching_terms(focus_text, query_terms)
                matching = matches_by_text[focus_text]
                if not focus_text.strip() or (row.get("_has_matching_passage") and not matching):
                    continue
                content = row.get("content", "")
                novelty = len(matching - covered)
                relevance = float(row.get("relevance_score") or row.get("similarity") or 0)
                # Repeated query words can describe different events. Relevance
                # remains the main signal after query-term coverage saturates.
                value = (
                    relevance * (0.25 + len(matching) / max(1, len(query_terms)))
                    + 0.15 * novelty / max(1, len(query_terms))
                ) / (max(1, marginal(row)) ** 0.5)
                # Repeated query matches in one source have diminishing value.
                # Other sources can contribute independent events using the same
                # vocabulary; do not apply this discount across source IDs.
                prior_matches = source_matches.get(str(row["id"]), set())
                if prior_matches and matching <= prior_matches:
                    value *= 0.25
                choices.append(((-value, str(row.get("id")), content), row))
            choices.sort(key=lambda choice: choice[0])

            chosen = None
            for _, row in choices:
                trial = _append_passage(selected, row)
                if trial == selected:
                    continue
                if cost(trial) > limit:
                    too_large.add(identities[id(row)])
                    continue
                chosen = row, trial
                break
            if chosen is None:
                break
            candidate, selected = chosen
            pending.remove(candidate)
            seen.add(identities[id(candidate)])
            evidence = _evidence_key(candidate)
            if evidence is not None:
                selected_evidence.add(evidence)
            covered.update(_matching_terms(candidate.get("content", ""), query_terms))
            source_matches.setdefault(str(candidate["id"]), set()).update(
                _matching_terms(candidate.get("content", ""), query_terms)
            )
        if direct_only:
            # Reconsider large direct passages using the shared budget.
            pending = [r for r in rows if identities[id(r)] not in seen]
    return selected

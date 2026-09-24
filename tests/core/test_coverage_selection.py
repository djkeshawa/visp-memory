"""Coverage selection preserves small decisive facts and their source spans."""

import pytest

from visp_memory.core.task_brief import TaskMemoryBriefCompiler


@pytest.mark.parametrize("query,fact", [
    ("Which workshops did I attend?", "user: I attended a workshop on May 10."),
    ("How many classes do I take?", "user: I take a pottery class on Mondays."),
    ("Which tools were serviced?", "user: I service the drill every month."),
])
def test_inflected_evidence_beats_question_boilerplate(query, fact):
    from visp_memory.core.coverage_selection import coverage_candidates, select_coverage

    rows = [
        {"id": "noise", "content": "assistant: How do I help?",
         "relevance_score": 0.9},
        {"id": "fact", "content": fact, "relevance_score": 0.8},
    ]
    selected = select_coverage(coverage_candidates(rows, query), query, len(fact) + 5,
                              lambda items: sum(len(row["content"]) for row in items))
    assert [row["id"] for row in selected] == ["fact"]


def test_matching_preserves_numbers_negation_and_exact_identifiers():
    from visp_memory.core.coverage_selection import terms

    result = terms("Do not change TLS1_3 or HTTP2; fees are $25, not $50.")
    assert {"not", "tls1_3", "http2", "25", "50"} <= result


def test_repeated_source_matches_do_not_crowd_out_an_independent_event():
    from visp_memory.core.coverage_selection import select_coverage

    chunks = ["Workshop booking cost $20. "] + ["Workshop booking needs planning. "] * 6
    rows, offset = [], 0
    for chunk in chunks:
        span = {"start": offset, "end": offset + len(chunk), "text": chunk}
        rows.append({"id": "a", "content": chunk, "passage_spans": [span],
                     "_focus_span": span, "relevance_score": 0.9})
        offset += len(chunk)
    rows.append({"id": "b", "content": "Workshop cost $35 for the second event.",
                 "relevance_score": 0.7})
    selected = select_coverage(rows, "workshop booking cost", 90,
                              lambda items: sum(len(row["content"]) for row in items))
    assert {row["id"] for row in selected} == {"a", "b"}


def test_all_relevant_turns_can_supply_evidence_beyond_first_three_windows():
    from visp_memory.core.coverage_selection import coverage_candidates

    text = "Session date: 2024/05/10\n" + "\n".join(
        f"user: Workshop ticket {n} cost ${n * 10}.\n"
        "assistant: Keep the receipt.\nuser: Thanks for the advice."
        for n in range(1, 7)
    )
    passages = coverage_candidates([{"id": "a", "content": text}], "workshop ticket cost")
    for n in range(1, 7):
        assert any(f"ticket {n} cost ${n * 10}" in p["content"] for p in passages)


def test_assistant_list_keeps_question_and_list_context():
    from visp_memory.core.coverage_selection import coverage_candidates

    text = (
        "Session date: 2024/05/10\nuser: Which quiet venues can host our workshop?\n"
        "assistant: These venues have separate rooms:\n"
        "- Cedar Hall\n- Willow House\n- Oak Centre\n- Birch Rooms\n"
        "- Maple Lodge\nuser: Thank you."
    )
    passages = coverage_candidates([{"id": "a", "content": text}], "quiet workshop venues")
    assert any(all(s in p["content"] for s in (
        "user: Which quiet", "assistant: These venues", "Maple Lodge"
    )) for p in passages)


def test_selection_uses_incremental_rendered_cost_not_just_passage_length():
    from visp_memory.core.coverage_selection import select_coverage

    rows = [
        {"id": key, "content": f"Workshop deposit ${amount}.", "relevance_score": 0.8}
        for key, amount in (("a", 10), ("b", 20))
    ]
    def cost(items):
        # The first memory needs a long conflict warning in the rendered brief.
        return sum(100 if r["id"] == "a" else 10 for r in items)
    selected = select_coverage(rows, "workshop deposit", 100, cost)
    assert [row["id"] for row in selected] == ["b"]


def test_full_brief_is_rendered_per_pick_not_per_candidate_per_pick():
    from visp_memory.core.coverage_selection import select_coverage

    rows = [
        {"id": str(i), "content": f"user: Workshop number {i} cost ${i}.",
         "relevance_score": 0.5 + i / 200, "retrieval_channels": ["direct"]}
        for i in range(60)
    ]
    renders = []
    def cost(items):
        # A brief renderer is not additive: a header appears once there is content.
        if len(items) > 1:
            renders.append(len(items))
        return (20 if items else 0) + sum(12 for _ in items)
    selected = select_coverage(rows, "workshop cost", 400, cost)
    assert cost(selected) <= 400
    assert len(selected) == 31
    # Re-rendering every candidate at every step made large budgets quadratic
    # (about 1,800 renders here); each candidate now fails at most once per phase.
    assert len(renders) <= 2 * len(rows) + len(selected)


def test_derived_copy_does_not_spend_budget_on_its_episode_twice():
    from visp_memory.core.coverage_selection import coverage_candidates

    text = "Session date: 2024/05/10\nuser: Workshop tickets cost $25."
    rows = [
        {"id": "episode", "content": text, "retrieval_channels": ["direct"]},
        {"id": "fact", "content": text, "source_ids": ["episode"],
         "retrieval_channels": ["graph"]},
    ]
    candidates = coverage_candidates(rows, "workshop tickets cost")
    assert len(candidates) == 1
    assert set(candidates[0]["retrieval_channels"]) == {"direct", "graph"}


def test_unrelated_reply_does_not_borrow_relevance_from_its_question():
    from visp_memory.core.coverage_selection import coverage_candidates, select_coverage
    from visp_memory.core.tokens import estimate_tokens

    rows = [
        {"id": "a", "content": "user: What did the workshop cost?\nassistant: "
         + "Some unrelated scheduling advice. " * 50, "relevance_score": 0.9},
        {"id": "b", "content": "user: The workshop cost $25.", "relevance_score": 0.8},
    ]
    selected = select_coverage(coverage_candidates(rows, "workshop cost"), "workshop cost", 180,
                               lambda items: sum(estimate_tokens(r["content"]) for r in items))
    assert "$25" in " ".join(r["content"] for r in selected)
    assert "unrelated scheduling advice" not in " ".join(r["content"] for r in selected)


def test_brief_reselects_small_passages_within_final_rendered_budget(tmp_path):
    from visp_memory.core.storage import LocalStorage

    text = "Session date: 2024/05/10\n" + "\n".join(
        f"user: Workshop ticket {n} cost ${n * 10}, " + "with booking details retained " * 12 + "."
        for n in range(1, 8)
    )
    with LocalStorage(tmp_path) as storage:
        mid = storage.store_memory(text, repo_id="repo", tags=["provenance:authored"])
        brief = TaskMemoryBriefCompiler(storage).prepare(
            "What did the workshop tickets cost?", repo_id="repo",
            context_selection="coverage", token_budget=400,
        )
        assert not brief["abstained"]
        assert "Workshop ticket" in brief["context"]
        assert brief["token_count"] <= 400
        for citation in brief["citations"]:
            assert citation["memory_id"] == mid
            for span in citation["passage_spans"]:
                assert text[span["start"]:span["end"]] == span["text"]


@pytest.mark.parametrize("date", ["2023/05/30", "05/30/2023", "30/05/2023", "2024-02-29"])
def test_dates_are_not_inferred_files(date):
    profile = TaskMemoryBriefCompiler(None)._task_profile(
        f"Question date: `{date}`. Inspect logs/2023/05/30/run.txt and src/auth.py",
        files=[date],
        symbols=[],
    )
    assert profile["files"] == [date, "logs/2023/05/30/run.txt", "src/auth.py"]
    inferred = TaskMemoryBriefCompiler(None)._task_profile(
        f"Question date: `{date}`",
        files=[],
        symbols=[],
    )
    assert inferred["files"] == []


def test_sentence_period_does_not_turn_date_into_a_path():
    profile = TaskMemoryBriefCompiler(None)._task_profile(
        "Answer as of 2023/05/30.", files=[], symbols=[],
    )
    assert profile["files"] == []


def test_coverage_preserves_distinct_amounts_and_verbatim_offsets():
    from visp_memory.core.coverage_selection import coverage_candidates

    rows = [
        {
            "id": "a",
            "content": "Session date: 2023/05/01\nuser: Lola's vet visit cost $50.\n"
            + "assistant: Grooming advice is extensive. " * 200,
            "retrieval_channels": ["direct"],
            "relevance_score": 0.8,
        },
        {
            "id": "b",
            "content": "Session date: 2023/05/02\nuser: Lola's flea medication cost $25, not $50.",
            "retrieval_channels": ["graph"],
            "relevance_score": 0.7,
        },
    ]
    passages = coverage_candidates(rows, "total cost Lola vet flea medication")
    assert any("$50" in p["content"] and p["id"] == "a" for p in passages)
    assert any("$25, not $50" in p["content"] for p in passages)
    for p in passages:
        original = next(r["content"] for r in rows if r["id"] == p["id"])
        for span in p["passage_spans"]:
            assert original[span["start"] : span["end"]] == span["text"]
    assert coverage_candidates(rows, "total cost Lola vet flea medication") == passages


def test_coverage_rejects_unknown_selection():
    from visp_memory.core.context_compiler import ContextCompiler

    with pytest.raises(ValueError, match="context_selection"):
        ContextCompiler(None).compile("task", repo_id="repo", context_selection="typo")


def test_identical_wording_in_distinct_events_keeps_both_citations():
    from visp_memory.core.coverage_selection import coverage_candidates, select_coverage

    rows = [{"id": str(day), "content": "user: I paid $25 for a vet visit.",
             "metadata": {"event_date": f"2024/05/{day:02d}"},
             "retrieval_channels": ["direct"], "relevance_score": 0.8}
            for day in (10, 20)]
    selected = select_coverage(coverage_candidates(rows, "vet visit cost"),
                               "vet visit cost", 1000,
                               lambda items: sum(len(r["content"]) for r in items))
    assert {r["id"] for r in selected} == {"10", "20"}


def test_coverage_shares_unused_direct_budget_and_merges_spans():
    from visp_memory.core.coverage_selection import coverage_candidates, select_coverage

    rows = [
        {
            "id": "a",
            "content": "Session date: 2023/05/01\nuser: Vet fee $50. Flea cost $25.",
            "retrieval_channels": ["direct"],
            "relevance_score": 0.8,
        },
        {
            "id": "b",
            "content": "Session date: 2023/05/02\nuser: Another vet visit cost $50.",
            "retrieval_channels": ["graph"],
            "relevance_score": 0.6,
        },
    ]
    selected = select_coverage(
        coverage_candidates(rows, "vet flea cost"),
        "vet flea cost",
        1000,
        lambda items: sum(len(r["content"]) for r in items),
    )
    assert {r["id"] for r in selected} == {"a", "b"}
    assert len(selected) == 2
    assert "$25" in selected[0]["content"]


def test_source_headers_are_never_standalone_evidence_candidates():
    from visp_memory.core.coverage_selection import coverage_candidates

    rows = [{"id": "a", "content": (
        "Session date: 2023/05/01\nExcerpt starts at character 0\n"
        "user: The consultation fee for Lola was $50.\n"
        "assistant: Regular grooming can help pets."
    ), "relevance_score": 0.8, "retrieval_channels": ["direct"]}]
    passages = coverage_candidates(rows, "Question date: 2023/05/01. Total vet cost for Lola?")
    for passage in passages:
        assert "$50" in passage["content"] or "Regular grooming" in passage["content"]


@pytest.mark.parametrize("mode", ["default", "coverage"])
def test_native_scope_and_supersession_survive_selection(tmp_path, mode):
    from visp_memory.core.storage import LocalStorage

    with LocalStorage(tmp_path) as storage:
        current = storage.store_memory(
            "The cache TTL is now 30 minutes.",
            repo_id="repo",
            tags=["provenance:authored"],
            auto_link=False,
        )
        old = storage.store_memory(
            "The cache TTL was 10 minutes.",
            repo_id="repo",
            status="superseded",
            tags=["provenance:authored"],
            auto_link=False,
        )
        foreign = storage.store_memory(
            "The cache TTL is 90 minutes.",
            repo_id="other",
            tags=["provenance:authored"],
            auto_link=False,
        )
        storage.add_relationship(current, old, "related_to")
        with pytest.raises(ValueError, match="cross repository"):
            storage.add_relationship(current, foreign, "related_to")
        brief = TaskMemoryBriefCompiler(storage).prepare(
            "What is the cache TTL?",
            repo_id="repo",
            context_selection=mode,
            token_budget=500,
        )
        assert current in {c["memory_id"] for c in brief["citations"]}
        assert old not in {c["memory_id"] for c in brief["citations"]}
        assert foreign not in {c["memory_id"] for c in brief["citations"]}
        assert brief["token_count"] <= 500


def test_derived_memories_share_an_identical_selected_source_passage():
    from visp_memory.core.coverage_selection import coverage_candidates, select_coverage
    header = "Session date: 2024/05/10\n"
    event = "user: The workshop deposit cost $25.\n"
    reply = "assistant: Keep the receipt.\n"
    source = header + event + reply + "user: Thank you.\n"
    rows = [
        {"id": "source", "content": source, "relevance_score": 0.7},
        {"id": "fact-a", "content": header + event, "source_ids": ["source"],
         "relevance_score": 0.9},
        {"id": "fact-b", "content": header + event + reply, "source_ids": ["source"],
         "relevance_score": 0.8},
    ]
    selected = select_coverage(coverage_candidates(rows, "workshop deposit cost"),
                               "workshop deposit cost", 2000,
                               lambda items: sum(len(r["content"]) for r in items))
    assert sum(r["content"].count(event.strip()) for r in selected) == 1


def test_identical_events_at_different_offsets_in_one_episode_survive():
    from visp_memory.core.coverage_selection import coverage_candidates, select_coverage
    filler = "assistant: " + "Unrelated advice. " * 110 + "\n"
    event = "user: The workshop deposit cost $25.\n"
    source = "Session date: 2024/05/10\n" + filler + event + filler + event
    selected = select_coverage(coverage_candidates([{"id": "episode", "content": source,
                                "relevance_score": 0.9}], "workshop deposit cost"),
                               "workshop deposit cost", 2000,
                               lambda items: sum(len(r["content"]) for r in items))
    assert sum(r["content"].count(event.strip()) for r in selected) == 2
    for row in selected:
        for span in row["passage_spans"]:
            assert source[span["start"]:span["end"]] == span["text"]


@pytest.mark.parametrize("source_available", [False, True])
def test_derived_passages_without_a_unique_eligible_origin_remain_distinct(source_available):
    from visp_memory.core.coverage_selection import coverage_candidates, select_coverage
    event = "user: The workshop deposit cost $25.\n"
    rows = [dict(id=name, content=event, source_ids=["source"], relevance_score=0.9)
            for name in ["a", "b"]]
    if source_available:
        rows.append({"id": "source", "content": event + "assistant: Recorded.\n" + event})
    selected = select_coverage(coverage_candidates(rows, "workshop deposit cost"),
                               "workshop deposit cost", 2000,
                               lambda items: sum(len(r["content"]) for r in items))
    assert {"a", "b"} <= {r["id"] for r in selected}


def test_cited_passages_are_not_folded_into_their_memory_row():
    from visp_memory.core.coverage_selection import coverage_candidates

    content = "user: first turn about pricing.\nuser: second turn about my old job.\n"
    memory = {"id": "m", "content": content, "relevance_score": 0.9}
    start = content.index("user: second")
    passage = {**memory, "content": content[start:],
               "passage_spans": [{"start": start, "end": len(content),
                                  "text": content[start:]}]}
    rows = coverage_candidates([memory, passage], "pricing")
    # The cited passage survives alongside the memory's own word-matched windows.
    assert any(row.get("passage_spans") == passage["passage_spans"] for row in rows)

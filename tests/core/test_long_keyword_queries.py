"""Long source ingestion must not overflow SQLite's SQL expression depth."""

from visp_memory.core.storage import LocalStorage


def test_keyword_query_with_many_distinct_terms_still_finds_tail(tmp_path):
    with LocalStorage(tmp_path) as storage:
        expected = storage.store_memory("special_tail_identifier", repo_id="r", auto_link=False)
        query = " ".join([f"unmatched{i}" for i in range(1200)] + ["special_tail_identifier"])
        matches = storage.search_memories(query, repo_id="r", limit=3)
        assert [row["id"] for row in matches] == [expected]


def test_recording_long_episode_keeps_full_source_and_auto_linking(tmp_path):
    with LocalStorage(tmp_path) as storage:
        source = "Background sentence. " * 2000 + "End fact: invoice total $123."
        mid = storage.store_memory(source, repo_id="r")
        assert storage.get_memory(mid)["content"] == source

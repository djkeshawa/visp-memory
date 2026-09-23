import pytest

from visp_memory import Memory, MemoryConfig


@pytest.mark.parametrize(
    "metadata",
    [
        {"valid_to": "2020-01-01T00:00:00+00:00"},
        {"valid_from": "2099-01-01T00:00:00+00:00"},
        {"environment": "dev"},
        {"task_type": "review"},
        {"valid_to": "invalid"},
    ],
)
def test_rejected_candidates_do_not_consume_recall_limit(tmp_path, metadata):
    config = MemoryConfig(repo_id="review")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    for index in range(9):
        memory._storage.store_memory(
            f"database migration ineligible {index}",
            repo_id="review",
            importance=1.0,
            metadata=metadata,
            auto_link=False,
        )
    valid_id = memory._storage.store_memory(
        "database migration current guidance",
        repo_id="review",
        importance=0.8,
        auto_link=False,
    )
    rows = memory.recall(
        "database migration",
        layers=["episodic"],
        limit=1,
        min_score=0,
        environment="prod",
        task_type="deploy",
    )
    assert [row["id"] for row in rows] == [valid_id]
    assert len(memory.last_recall_eligibility_result.rejected) == 9


def test_exhausted_recall_stays_empty_and_reports_rejections(tmp_path):
    config = MemoryConfig(repo_id="review")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    memory._storage.store_memory(
        "expired database migration",
        repo_id="review",
        metadata={"valid_to": "2020-01-01T00:00:00+00:00"},
        auto_link=False,
    )
    assert memory.recall("database migration", limit=1, min_score=0) == []
    assert memory.last_recall_eligibility["rejection_counts"] == {"expired": 1}


def test_capped_backend_cannot_cause_an_endless_refill():
    from visp_memory.core.recall_candidates import recall_candidates

    class CappedStorage:
        requests = 0

        def search_memories(self, **kwargs):
            self.requests += 1
            assert self.requests < 5, "Recall must stop when the backend makes no progress"
            return [
                {
                    "id": "expired",
                    "repo_id": "review",
                    "metadata": {"valid_to": "2020-01-01T00:00:00+00:00"},
                }
            ] * kwargs["limit"]

    result = recall_candidates(
        CappedStorage(),
        "migration",
        repo_id="review",
        layers=["episodic"],
        limit=1,
    )
    assert result.allowed == []


@pytest.mark.parametrize("min_score", [0, 0.56])
def test_exact_match_is_not_starved_by_high_importance_partial_matches(tmp_path, min_score):
    config = MemoryConfig(repo_id="review")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    for index in range(25):
        memory._storage.store_memory(
            f"database unrelated warning {index}", repo_id="review",
            importance=1, auto_link=False,
        )
    content = "database migration rollback recovery procedure"
    valid_id = memory._storage.store_memory(
        content, repo_id="review", importance=0.5, auto_link=False,
    )
    rows = memory.recall(
        content, layers=["episodic"], limit=1, min_score=min_score, log_utility=False,
    )
    assert [row["id"] for row in rows] == [valid_id]


def test_refill_continues_until_final_contextual_ranking_accepts_a_candidate(tmp_path, monkeypatch):
    config = MemoryConfig(repo_id="review")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    weak = {"id": "weak", "repo_id": "review", "content": "database", "importance": 0.1}
    relevant = {
        "id": "relevant", "repo_id": "review", "content": "database migration",
        "importance": 0.8, "similarity": 0.9,
    }
    monkeypatch.setattr(
        memory._storage, "search_memories",
        lambda **kwargs: [weak, relevant][:kwargs["limit"]],
    )
    rows = memory.recall(
        "database migration", layers=["episodic"], limit=1, log_utility=False,
    )
    assert [row["id"] for row in rows] == ["relevant"]

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.task_brief import TaskMemoryBriefCompiler
from visp_memory.recall.proactive import ProactiveRecall


@pytest.fixture
def historical_memory(tmp_path):
    config = MemoryConfig(repo_id="historical-audit")
    config.storage.data_dir = tmp_path / "memory"
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    memory_id = memory._storage.store_memory(
        "Use HttpOnly cookies for secure login in src/login.py", repo_id=config.repo_id,
        source="authored", tags=["provenance:authored"],
        category="architecture_decision",
        metadata={"files": ["src/login.py"]}, auto_link=False,
        created_at="2020-01-01T00:00:00+00:00",
    )
    yield memory, memory_id
    memory.close()


def test_historical_context_assesses_trust_at_the_requested_time(historical_memory):
    memory, memory_id = historical_memory

    context = memory.context(
        include_knowledge=False, include_intent=False, format="json",
        as_of="2020-01-02T00:00:00+00:00",
    )

    assert [item["event"] for item in context["history"]["recent_events"]] == [
        memory._storage.peek_memory(memory_id)["content"]
    ]


def test_historical_brief_assesses_trust_at_the_requested_time(historical_memory):
    memory, memory_id = historical_memory

    brief = TaskMemoryBriefCompiler(memory._storage).prepare(
        "Review secure login", repo_id=memory.config.repo_id,
        files=["src/login.py"], as_of="2020-01-02T00:00:00+00:00",
    )

    assert [citation["memory_id"] for citation in brief["citations"]] == [memory_id]


def test_historical_relevance_assesses_trust_at_the_requested_time(historical_memory):
    memory, memory_id = historical_memory

    result = memory.relevant_for(
        task="secure login", files=["src/login.py"], as_of="2020-01-02T00:00:00+00:00",
    )

    assert [item["id"] for item in result["history"]] == [memory_id]


def test_historical_proactive_recall_assesses_trust_at_the_requested_time(historical_memory):
    memory, memory_id = historical_memory
    recall = ProactiveRecall(memory, repo_id=memory.config.repo_id, as_of="2020-01-02T00:00:00Z")

    result = recall.on_file_open("src/login.py")

    assert [item["id"] for item in result["decisions"]] == [memory_id]

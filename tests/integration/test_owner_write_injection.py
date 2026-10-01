"""Client-mode owner writes pass the same injection gates as local MCP writes."""

from visp_memory import Memory, MemoryConfig
from visp_memory.core.injection import select_for_injection
from visp_memory.core.trust import WriteChannel

pytest_plugins = ["tests.integration.shared_server"]


def test_client_owner_record_is_auto_injectable_like_local_mcp(client_memory, tmp_path):
    memory = client_memory("owner-injection")
    content = "Deployment migration rollback requires staging verification"
    memory_id = memory.record(content)
    record = memory._storage.get_memory(memory_id)
    config = MemoryConfig(repo_id="owner-injection")
    config.storage.data_dir = tmp_path / "local-mcp"
    config.embedding.provider = "noop"
    with Memory(config=config) as local:
        local_id = local.record(content, _write_channel=WriteChannel.MCP)
        local_mcp = local._storage.get_memory(local_id)
    for item in (record, local_mcp):
        result = select_for_injection(
            [{**item, "relevance_score": 0.9}],
            repo_id="owner-injection",
            task="deployment migration rollback verification",
        )
        assert [row["id"] for row in result.memories] == [item["id"]]
        assert result.dropped_quarantined == 0

    assert record["metadata"]["write_channel"] == "local_owner"
    assert record["source"] == "assisted"

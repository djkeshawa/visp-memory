"""Neo4j adoption diagnostics must be read-only and distinguish failure."""

from unittest.mock import MagicMock

from visp_memory.config import MemoryConfig
from visp_memory.core.intent_usage import check_intent_usage


def test_neo4j_intent_diagnostic_counts_without_initializing_storage(monkeypatch):
    from visp_memory.core import neo4j_storage

    config = MemoryConfig()
    config.storage.backend = "neo4j"
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value.single.return_value = {
        "memories": 4, "active_intents": 1, "total_intents": 2,
    }
    graph = MagicMock()
    graph.driver.return_value.__enter__.return_value = driver
    monkeypatch.setattr(neo4j_storage, "GraphDatabase", graph)
    def refuse_initialization(*args, **kwargs):
        raise AssertionError("must not initialize")
    monkeypatch.setattr(neo4j_storage.Neo4jStorage, "__init__", refuse_initialization)
    result = check_intent_usage(config)
    assert result.status == "in_use"
    assert (result.memories, result.active_intents, result.total_intents) == (4, 1, 2)
    assert driver.session.call_args.kwargs["default_access_mode"] == "READ"
    query = session.run.call_args.args[0].upper()
    assert all(verb not in query for verb in ("CREATE ", "MERGE ", "DELETE ", "SET "))


def test_neo4j_intent_diagnostic_failure_is_not_zero_usage(monkeypatch):
    from visp_memory.core import neo4j_storage

    config = MemoryConfig()
    config.storage.backend = "neo4j"
    graph = MagicMock()
    graph.driver.side_effect = RuntimeError("unreachable test database")
    monkeypatch.setattr(neo4j_storage, "GraphDatabase", graph)
    report = check_intent_usage(config)
    assert report.status == "unreadable"
    assert not report.never_used

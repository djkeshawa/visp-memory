from types import SimpleNamespace

import pytest

from visp_memory.server.auth import UserContext
from visp_memory.server.scoped_storage import ScopedStorageView


def test_turn_key_search_refills_past_multiple_spans_of_one_hidden_memory():
    hidden = {"id": "hidden", "repo_id": "project", "metadata": {"team_id": "beta"}}
    visible = {"id": "visible", "repo_id": "project", "metadata": {"team_id": "alpha"}}
    hits = [
        {"memory": hidden, "span": (index, index + 1)} for index in range(4)
    ] + [{"memory": visible, "span": (0, 1)}]
    storage = SimpleNamespace(search_turn_keys=lambda query, **args: hits[:args["limit"]])
    view = ScopedStorageView(
        storage, UserContext(user_id="alice", username="alice", team_id="alpha"), "project"
    )

    assert view.search_turn_keys("login", limit=1) == [hits[-1]]


@pytest.mark.parametrize("method", ["store_memory", "store_evidence"])
def test_scoped_writes_pin_repository_and_authenticated_ownership(method):
    calls = []
    storage = SimpleNamespace(**{method: lambda **args: calls.append(args) or "created"})
    view = ScopedStorageView(
        storage, UserContext(user_id="alice", username="alice", team_id="alpha"), "project"
    )
    metadata = {
        "author_id": "mallory", "team_id": "beta", "write_channel": "mcp", "keep": True,
    }

    assert getattr(view, method)(content="Useful content", metadata=metadata) == "created"
    assert calls[0]["repo_id"] == "project"
    assert calls[0]["metadata"] == {
        "author_id": "alice", "team_id": "alpha", "write_channel": "mcp", "keep": True,
    }
    assert metadata["author_id"] == "mallory"
    assert metadata["team_id"] == "beta"
    with pytest.raises(ValueError, match="authorized repository"):
        getattr(view, method)(content="Other scope", repo_id="other")
    assert len(calls) == 1


@pytest.mark.parametrize("method", ["store_memory", "store_evidence"])
def test_scoped_write_without_a_team_drops_untrusted_team_metadata(method):
    calls = []
    storage = SimpleNamespace(**{method: lambda **args: calls.append(args) or "created"})
    view = ScopedStorageView(
        storage, UserContext(user_id="admin", username="admin", is_admin=True), "project"
    )

    getattr(view, method)(content="Admin content", metadata={"team_id": "spoofed"})

    assert calls[0]["metadata"] == {"author_id": "admin"}

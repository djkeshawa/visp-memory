from types import SimpleNamespace

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

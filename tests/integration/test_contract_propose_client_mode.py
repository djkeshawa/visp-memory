"""`contract propose` and the review lifecycle against a live shared server.

THE DEFECT THIS PINS. In client mode `contract propose` recorded the proposal
ACTIVE, then asked the server to quarantine it. The REST schema had no
"quarantined" status, so that PATCH failed with HTTP 422: the command reported
failure while the unreviewed proposal stayed active and recall served it.
Every retry added another active copy. Client mode is what `visp-memory
connect` sets up, and Hyper's `visp learn` spawns exactly this command.
"""

import json

import pytest
from typer.testing import CliRunner

import visp_memory.interfaces.cli as cli_module
from visp_memory.interfaces.cli import app
from visp_memory.server.app import app as server_app

pytest_plugins = ["tests.integration.shared_server"]

REPO = "proposals-repo"
runner = CliRunner()


@pytest.fixture
def client_cli(client_memory, monkeypatch):
    memory = client_memory(REPO)
    monkeypatch.setattr(cli_module, "_memory", memory)
    return memory


def _envelope(output: str) -> dict:
    return json.loads(output.strip().splitlines()[-1])


def _propose(content: str) -> str:
    result = runner.invoke(app, ["contract", "propose", content])
    assert result.exit_code == 0, result.output
    envelope = _envelope(result.output)
    assert envelope["success"] is True
    assert envelope["status"] == "quarantined"
    return envelope["proposalId"]


def _recalled(query: str) -> list:
    result = runner.invoke(app, ["contract", "recall", query])
    assert result.exit_code == 0, result.output
    return [entry["content"] for entry in _envelope(result.output)["entries"]]


def _server_rows() -> list:
    storage = server_app.state.storage
    return [
        row
        for status in ("active", "quarantined", "rejected")
        for row in storage.list_memories(repo_id=REPO, status=status, limit=100)
    ]


def test_propose_stores_one_quarantined_row_that_recall_never_serves(client_cli):
    phrase = "the deploy window closes at nine"

    proposal_id = _propose(phrase)

    rows = _server_rows()
    assert [(row["id"], row["status"]) for row in rows] == [(proposal_id, "quarantined")]
    assert all(phrase not in content for content in _recalled("deploy window"))


def test_review_accept_makes_the_proposal_recallable(client_cli):
    phrase = "releases are cut from the release branch"
    proposal_id = _propose(phrase)

    listed = runner.invoke(app, ["review", "list", "--repo", REPO])
    assert listed.exit_code == 0, listed.output
    assert proposal_id in listed.output

    accepted = runner.invoke(app, ["review", "accept", proposal_id, "--repo", REPO])
    assert accepted.exit_code == 0, accepted.output
    assert server_app.state.storage.peek_memory(proposal_id)["status"] == "active"
    assert phrase in _recalled("release branch")


def test_review_reject_keeps_the_proposal_out_of_recall(client_cli):
    phrase = "this proposal was mistaken"
    proposal_id = _propose(phrase)

    rejected = runner.invoke(app, ["review", "reject", proposal_id, "--repo", REPO])

    assert rejected.exit_code == 0, rejected.output
    assert server_app.state.storage.peek_memory(proposal_id)["status"] == "rejected"
    assert all(phrase not in content for content in _recalled("proposal mistaken"))

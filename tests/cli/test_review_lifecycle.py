"""The reviewed lifecycle exists, end to end.

THE DEFECT THIS PINS. `visp learn` and `visp-memory contract propose` both
promised: "It becomes durable only after Memory's reviewed lifecycle accepts
it." No command anywhere implemented that lifecycle — an exhaustive --help
sweep found no review, accept, or approve surface. Every proposal was
permanently stuck at status='quarantined', and `visp-memory audit`, whose
stated purpose is quarantine visibility, never showed them either: it counts
provenance-quarantine (rows that can never be auto-injected) and is blind to
lifecycle-quarantine (rows awaiting review).

The review surface must NOT become a laundering path: rows in the reserved
`__visp_unscoped__` bucket carry Provenance.UNKNOWN and stay out of reach —
tests/core/test_evidence_contract.py pins that trust control. Review operates
only inside a real repository scope, on rows that already belong to it.
"""

import json

import pytest
from typer.testing import CliRunner

from visp_memory.interfaces.cli import app

runner = CliRunner()


def _invoke(*args: str):
    return runner.invoke(app, list(args))


def _propose(content: str, repo: str = "demo-repo") -> str:
    result = _invoke("contract", "propose", content, "--repo", repo)
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output.strip().splitlines()[-1])
    assert envelope["success"] is True
    return envelope["proposalId"]


@pytest.mark.usefixtures("cli_env")
class TestReviewLifecycle:
    def test_list_shows_the_pending_proposal(self):
        proposal_id = _propose("the retry limit is five")

        listed = _invoke("review", "list", "--repo", "demo-repo")

        assert listed.exit_code == 0, listed.output
        assert proposal_id in listed.output
        assert "retry limit" in listed.output

    def test_accept_makes_the_proposal_recallable(self):
        proposal_id = _propose("deploys happen from the release branch")
        before = _invoke("recall", "release branch", "--repo", "demo-repo")
        assert "release branch" not in before.output.replace(
            "Search Results for 'release branch'", ""
        )

        accepted = _invoke("review", "accept", proposal_id, "--repo", "demo-repo")
        assert accepted.exit_code == 0, accepted.output

        after = _invoke("recall", "release branch", "--repo", "demo-repo")
        assert after.exit_code == 0
        assert "release branch" in after.output

    def test_reject_keeps_the_proposal_out_of_recall(self):
        proposal_id = _propose("this idea was wrong")

        rejected = _invoke("review", "reject", proposal_id, "--repo", "demo-repo")
        assert rejected.exit_code == 0, rejected.output

        recalled = _invoke("recall", "idea was wrong", "--repo", "demo-repo")
        assert "this idea was wrong" not in recalled.output
        # and it is no longer pending either
        listed = _invoke("review", "list", "--repo", "demo-repo")
        assert proposal_id not in listed.output

    def test_accept_refuses_a_proposal_from_another_repo(self):
        proposal_id = _propose("belongs elsewhere", repo="other-repo")

        accepted = _invoke("review", "accept", proposal_id, "--repo", "demo-repo")

        assert accepted.exit_code != 0
        recalled = _invoke("recall", "belongs elsewhere", "--repo", "other-repo")
        assert "belongs elsewhere" not in recalled.output

    def test_review_never_touches_the_unscoped_quarantine(self):
        # The reserved bucket is a trust control, not a review queue.
        listed = _invoke("review", "list", "--repo", "__visp_unscoped__")
        assert listed.exit_code != 0

    def test_audit_counts_pending_proposals(self):
        _propose("waiting for a human")

        audited = _invoke("audit", "--repo", "demo-repo")

        assert audited.exit_code == 0, audited.output
        assert "pending review" in audited.output.lower()
        assert "review list" in audited.output


@pytest.mark.usefixtures("cli_env")
class TestContractRecallIntentSignpost:
    def test_the_envelope_carries_the_intent_match_count(self):
        # The human CLI already signposts matching intents on an empty recall
        # (F7). The machine contract dropped that signal, so the coordinator
        # answered "there was nothing to say" for the identical query one
        # command after visp-memory itself pointed at the goal layer.
        set_intent = _invoke(
            "goal", "migrate due-date storage to ISO strings", "--repo", "demo-repo"
        )
        assert set_intent.exit_code == 0, set_intent.output

        result = _invoke("contract", "recall", "migrate due-date storage", "--repo", "demo-repo")

        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output.strip().splitlines()[-1])
        assert envelope["success"] is True
        assert envelope["entries"] == []
        assert envelope.get("intentMatches", 0) >= 1

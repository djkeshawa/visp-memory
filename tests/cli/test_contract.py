"""P10-US-07: the versioned machine contract (`visp-memory contract ...`).

The contract is what the Visp coordinator speaks. Two commands only:
recall (read) and propose (a QUARANTINED proposal through the reviewed
lifecycle — never a direct durable write). These tests pin the envelope
shape, the quarantine behavior, and the fact that a proposal is invisible
to recall until the lifecycle accepts it.
"""

import json

from typer.testing import CliRunner

from visp_memory.interfaces.cli import app

runner = CliRunner()


def _envelope(output: str) -> dict:
    # The envelope is the last JSON line of output.
    lines = [line for line in output.strip().splitlines() if line.strip().startswith("{")]
    assert lines, f"no JSON envelope in output: {output!r}"
    return json.loads(lines[-1])


class TestContractRecall:
    def test_recall_returns_contract_envelope(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["learn", "The build uses pnpm, not npm."])

        result = runner.invoke(app, ["contract", "recall", "build tool", "--json"])
        assert result.exit_code == 0
        envelope = _envelope(result.output)
        assert envelope["contractVersion"] == "1.0"
        assert envelope["success"] is True
        assert isinstance(envelope["entries"], list)

    def test_recall_entries_carry_kind_and_content(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["learn", "Deployments require the staging gate."])

        result = runner.invoke(app, ["contract", "recall", "deployments staging gate"])
        envelope = _envelope(result.output)
        assert envelope["success"] is True
        for entry in envelope["entries"]:
            assert isinstance(entry["kind"], str) and entry["kind"]
            assert isinstance(entry["content"], str)


class TestContractPropose:
    def test_propose_returns_proposal_id_and_quarantines(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])

        result = runner.invoke(
            app, ["contract", "propose", "The retry limit should be 5, not 3."]
        )
        assert result.exit_code == 0
        envelope = _envelope(result.output)
        assert envelope["contractVersion"] == "1.0"
        assert envelope["success"] is True
        proposal_id = envelope["proposalId"]

        # The proposal exists but is quarantined: the reviewed lifecycle owns it.
        import visp_memory.interfaces.cli as cli_module

        memory = cli_module.get_memory()
        row = memory._storage.peek_memory(proposal_id)
        assert row is not None
        assert row["status"] == "quarantined"

    def test_proposal_is_invisible_to_recall_until_accepted(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        phrase = "the flux capacitor voltage is exactly eleven"
        result = runner.invoke(app, ["contract", "propose", phrase])
        assert _envelope(result.output)["success"] is True

        recall = runner.invoke(app, ["contract", "recall", "flux capacitor voltage"])
        envelope = _envelope(recall.output)
        assert envelope["success"] is True
        assert all(phrase not in entry["content"] for entry in envelope["entries"])


class TestProposeDoesNotBrickTheStore:
    """P12: `contract propose` once made every SUBSEQUENT read fail.

    It set epistemic_status on a non-semantic row, and the storage integrity
    check refuses that shape — so the damage was not scoped to the proposal,
    it disabled the whole database. These tests exist because the failure was
    invisible at the call site: propose itself reported success.
    """

    def test_recall_still_works_after_a_proposal(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["record", "the build uses pnpm", "--repo", "demo"])

        before = _envelope(
            runner.invoke(app, ["contract", "recall", "build", "--repo", "demo"]).output
        )
        assert before["success"] is True

        proposed = _envelope(
            runner.invoke(
                app, ["contract", "propose", "retry limit should be 5", "--repo", "demo"]
            ).output
        )
        assert proposed["success"] is True

        after = _envelope(
            runner.invoke(app, ["contract", "recall", "build", "--repo", "demo"]).output
        )
        assert after["success"] is True, f"propose bricked the store: {after.get('reason')}"
        assert any("pnpm" in entry["content"] for entry in after["entries"])

    def test_a_quarantined_row_carries_no_semantic_belief_fields(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        result = runner.invoke(app, ["contract", "propose", "a proposal", "--repo", "demo"])
        proposal_id = _envelope(result.output)["proposalId"]

        import visp_memory.interfaces.cli as cli_module

        row = cli_module.get_memory()._storage.peek_memory(proposal_id)
        assert row["status"] == "quarantined"
        # The exact shape the integrity check refuses on a non-semantic row.
        assert row.get("epistemic_status") is None
        assert row.get("belief_type") is None

    def test_propose_discloses_that_the_proposal_is_not_retrievable(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        envelope = _envelope(
            runner.invoke(app, ["contract", "propose", "a proposal", "--repo", "demo"]).output
        )
        # Reporting a bare success invited integrators to build on content that
        # no command can return. The envelope must say so itself.
        assert envelope["durable"] is False
        assert envelope["status"] == "quarantined"
        assert "recall" in envelope["note"]

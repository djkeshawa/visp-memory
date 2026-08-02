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

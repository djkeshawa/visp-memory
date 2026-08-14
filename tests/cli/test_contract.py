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


class TestContractRecallCanSeeTheTaskAndItsFiles:
    """The contract's route to `hybrid_retrieval`, at the CLI.

    Retrieval could fuse the code graph since 0.5.0, but only through
    `ContextCompiler`. A coordinator that spawns `visp-memory contract recall`
    had no way to say which files the task touches, so every machine recall was
    text similarity alone. These tests exercise the flags Hyper forwards.
    """

    @staticmethod
    def _record_on_file(content: str, files: list[str], repo: str = "demo") -> str:
        import visp_memory.interfaces.cli as cli_module

        memory = cli_module.get_memory()
        evidence_id = memory._storage.store_evidence(content, repo_id=repo)
        return memory._storage.store_memory(
            content,
            layer="semantic",
            repo_id=repo,
            evidence_ids=[evidence_id],
            auto_link=False,
            metadata={"files": list(files), "confidence": 0.9},
        )

    def test_naming_the_files_surfaces_what_the_words_missed(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        self._record_on_file("Pagination cursors are opaque here", ["src/api.py"])
        self._record_on_file("Writes must hold the advisory lock", ["src/api.py"])

        without = _envelope(
            runner.invoke(
                app, ["contract", "recall", "pagination cursors", "--repo", "demo"]
            ).output
        )
        assert all("advisory lock" not in entry["content"] for entry in without["entries"])
        assert "retrieval" not in without, "the text path must stay byte-identical"

        with_files = _envelope(
            runner.invoke(
                app,
                [
                    "contract",
                    "recall",
                    "pagination cursors",
                    "--repo",
                    "demo",
                    "--task",
                    "add a cursor to the writer",
                    "--file",
                    "src/api.py",
                ],
            ).output
        )
        contents = [entry["content"] for entry in with_files["entries"]]
        assert any("advisory lock" in content for content in contents)
        assert with_files["retrieval"]["strategy"] == "text+code"
        assert with_files["retrieval"]["admitted"] >= 1

    def test_an_admitted_entry_says_how_it_was_reached(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        self._record_on_file("Pagination cursors are opaque here", ["src/api.py"])
        self._record_on_file("Writes must hold the advisory lock", ["src/api.py"])

        envelope = _envelope(
            runner.invoke(
                app,
                [
                    "contract",
                    "recall",
                    "pagination cursors",
                    "--repo",
                    "demo",
                    "--file",
                    "src/api.py",
                ],
            ).output
        )
        admitted = [
            entry for entry in envelope["entries"] if "advisory lock" in entry["content"]
        ]
        assert admitted and admitted[0]["reachedBy"] == "file-match"

    def test_the_file_scoped_result_is_a_superset(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        self._record_on_file("Pagination cursors are opaque here", ["src/api.py"])
        self._record_on_file("Writes must hold the advisory lock", ["src/api.py"])
        self._record_on_file("Telemetry batches every thirty seconds", ["src/other.py"])

        def contents(extra: list[str]) -> list[str]:
            envelope = _envelope(
                runner.invoke(
                    app,
                    ["contract", "recall", "pagination cursors", "--repo", "demo", *extra],
                ).output
            )
            assert envelope["success"] is True
            return [entry["content"] for entry in envelope["entries"]]

        text_only = contents([])
        scoped = contents(["--file", "src/api.py"])

        assert scoped[: len(text_only)] == text_only

    def test_the_envelope_says_why_structure_did_nothing(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        self._record_on_file("Pagination cursors are opaque here", ["src/api.py"])

        envelope = _envelope(
            runner.invoke(
                app,
                [
                    "contract",
                    "recall",
                    "pagination cursors",
                    "--repo",
                    "demo",
                    "--file",
                    "src/api.py",
                ],
            ).output
        )
        # No projection is configured in this project, which is the ordinary
        # case. It has to be reported rather than look like "the graph found
        # nothing", because those are different facts.
        assert envelope["retrieval"]["codeGraph"]["state"] == "unconfigured"

    def test_a_malformed_as_of_is_refused_in_the_envelope_not_a_traceback(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])

        result = runner.invoke(
            app,
            ["contract", "recall", "anything", "--repo", "demo", "--as-of", "yesterday-ish"],
        )
        envelope = _envelope(result.output)

        assert envelope["success"] is False
        assert "as_of" in envelope["reason"]
        assert result.exit_code == 1

    def test_the_scopes_the_coordinator_forwards_are_honoured(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        import visp_memory.interfaces.cli as cli_module

        memory = cli_module.get_memory()
        memory._storage.store_memory(
            "Retry budgets belong to the caller",
            layer="semantic",
            repo_id="demo",
            evidence_ids=[memory._storage.store_evidence("Retry budgets", repo_id="demo")],
            auto_link=False,
            metadata={
                "files": ["src/api.py"],
                "confidence": 0.9,
                "environment": ["production"],
            },
        )

        envelope = _envelope(
            runner.invoke(
                app,
                [
                    "contract",
                    "recall",
                    "retry budgets",
                    "--repo",
                    "demo",
                    "--file",
                    "src/api.py",
                    "--environment",
                    "staging",
                ],
            ).output
        )
        assert all("Retry budgets" not in entry["content"] for entry in envelope["entries"])


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


class TestTheContractSurfaceDescribesItselfTruthfully:
    """Every statement the contract makes about itself, checked against behaviour.

    `propose` denied a working feature. The same audit applied to the rest of
    the surface: `--endpoint` is accepted and then discarded, so the help text
    must not suggest a remote store is ever consulted.
    """

    def test_endpoint_is_documented_as_ignored_and_is_ignored(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["record", "the build uses pnpm", "--repo", "demo"])

        help_text = runner.invoke(app, ["contract", "recall", "--help"]).output.casefold()
        assert "ignored" in help_text

        # An unreachable endpoint changes nothing, because none is contacted.
        envelope = _envelope(
            runner.invoke(
                app,
                [
                    "contract",
                    "recall",
                    "build",
                    "--repo",
                    "demo",
                    "--endpoint",
                    "https://memory.invalid",
                ],
            ).output
        )
        assert envelope["success"] is True
        assert any("pnpm" in entry["content"] for entry in envelope["entries"])


class TestProposeTellsTheTruthAboutAcceptance:
    """The note `propose` returns is the only documentation an integrator reads.

    It said "No CLI command currently accepts a proposal" for as long as
    `review accept` existed and worked. A product that denies its own working
    feature costs the integrator the feature; these tests hold the note to
    what the CLI can actually do, by *running* what the note names.
    """

    def test_the_note_does_not_deny_a_feature_that_exists(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        note = _envelope(
            runner.invoke(app, ["contract", "propose", "a proposal", "--repo", "demo"]).output
        )["note"].casefold()

        assert "no cli command" not in note
        assert "until the reviewed lifecycle is implemented" not in note

    def test_the_note_names_the_command_and_that_command_works(self, cli_env):
        """Pin the message to the behaviour, not to a string.

        Asserting the note *mentions* `review accept` would keep passing if the
        command were deleted tomorrow. So the note is read, the command it names
        is run, and recall is asked whether the promise was kept.
        """
        runner.invoke(app, ["init", "--type", "code"])
        phrase = "the flux capacitor voltage is exactly eleven"
        envelope = _envelope(
            runner.invoke(app, ["contract", "propose", phrase, "--repo", "demo"]).output
        )
        proposal_id = envelope["proposalId"]

        assert "review accept" in envelope["note"]
        assert "review list" in envelope["note"]

        accepted = runner.invoke(app, ["review", "accept", proposal_id, "--repo", "demo"])
        assert accepted.exit_code == 0, accepted.output

        recall = _envelope(
            runner.invoke(
                app, ["contract", "recall", "flux capacitor voltage", "--repo", "demo"]
            ).output
        )
        assert any(phrase in entry["content"] for entry in recall["entries"]), recall

    def test_rejection_is_offered_too_and_keeps_the_proposal_out_of_recall(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        phrase = "the flux capacitor voltage is exactly eleven"
        envelope = _envelope(
            runner.invoke(app, ["contract", "propose", phrase, "--repo", "demo"]).output
        )
        assert "review reject" in envelope["note"]

        rejected = runner.invoke(
            app, ["review", "reject", envelope["proposalId"], "--repo", "demo"]
        )
        assert rejected.exit_code == 0, rejected.output

        recall = _envelope(
            runner.invoke(
                app, ["contract", "recall", "flux capacitor voltage", "--repo", "demo"]
            ).output
        )
        assert all(phrase not in entry["content"] for entry in recall["entries"])

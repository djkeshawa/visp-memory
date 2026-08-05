"""P13 — record and recall must agree about repository scope.

Written behaviour-first, against the product's promise rather than its code.

THE DEFECT THESE PIN. In a directory where `visp-memory init` was never run,
`record` reported success and printed an ID, and every later `recall` raised an
unhandled ValueError with a full Rich traceback — frames, source lines and every
local variable in scope. `list` still displayed the row, so the store looked
healthy while holding something no recall would ever return.

WHAT IS *NOT* THE DEFECT, WHICH TOOK A RUN OF THE SUITE TO ESTABLISH.
The storage layer sends an unscoped write to the reserved `__visp_unscoped__`
bucket, and `require_repo_id` refuses to read that bucket. It is tempting to
call that an accidental disagreement between two components and to "repair" it
by requiring a scope at the write, or by letting recall serve the bucket.

Both are wrong. The bucket is a QUARANTINE and the refusal is a trust control:
rows land there carrying Provenance.UNKNOWN, from a v2->v3 migration or from an
unattributed write, and surfacing them to a model as trusted project knowledge
is precisely what must not happen. tests/core/test_evidence_contract.py pins
that intent directly. An earlier attempt at this fix changed the storage rule
and taught `import` to re-scope quarantined rows into a real project; the
suite rejected it, correctly — that is not data recovery, it is laundering.

So the bug is in the SURFACE. The engine was right to quarantine; the CLI was
wrong to call a quarantined write "Recorded:" and hand back an ID, and wrong to
answer the reader with a stack dump. Both are fixed in the CLI, which is where
the user-facing promise is made.
"""

import pytest
from typer.testing import CliRunner

from visp_memory.interfaces.cli import app

runner = CliRunner()

CONTENT = "the retry limit is five"


def _invoke(*args: str):
    return runner.invoke(app, list(args))


@pytest.mark.usefixtures("cli_env")
class TestUnscopedRoundTrip:
    """A project where `init` was never run — the reachable, ordinary case."""

    def test_record_and_recall_agree_about_scope(self):
        """Whatever `record` accepts, `recall` must be able to serve.

        This is the invariant, and it is satisfied two ways:
          - `record` refuses, so nothing was promised; or
          - `record` accepts, and `recall` finds it.
        The only forbidden outcome is the current one: accepted, then lost.
        """
        recorded = _invoke("record", CONTENT)

        if recorded.exit_code != 0:
            # Refusing is a legitimate resolution — but it must be a REFUSAL,
            # not a crash, and it must say what to do about it.
            assert recorded.exception is None or not isinstance(
                recorded.exception, SystemExit
            ) or recorded.exit_code == 1, "record failed in an unrecognisable way"
            message = (recorded.stdout or "") + str(recorded.exception or "")
            assert "repo" in message.lower() or "scope" in message.lower(), (
                "record refused without telling the user that a repository scope is the problem. "
                f"Got: {message!r}"
            )
            return

        # `record` accepted the memory and printed an ID. It is now owed back.
        found = _invoke("recall", "retry")
        assert found.exit_code == 0, (
            "record accepted the memory, and recall then failed with "
            f"{found.exception!r}. A store that accepts a write it can never serve is worse "
            "than one that refuses the write: the user is told it worked."
        )
        assert CONTENT in found.stdout.replace("\n", " ").replace("  ", " ") or "retry" in found.stdout, (
            "recall succeeded but did not return the memory record just accepted. "
            f"Output: {found.stdout!r}"
        )

    def test_recall_never_raises_an_unhandled_traceback(self):
        """A foreseeable input error must be a message, not a stack dump.

        The observed failure printed source lines and local variables — including
        `self` and the whole Memory object — to a user who simply forgot to run
        `init`. Whatever the scope fix turns out to be, this must not happen.
        """
        _invoke("record", CONTENT)
        found = _invoke("recall", "retry")

        assert not isinstance(found.exception, ValueError), (
            "recall leaked an unhandled ValueError to the user: "
            f"{found.exception!r}. Foreseeable bad input deserves a refusal, not a traceback."
        )

    def test_naming_the_reserved_scope_is_refused_cleanly(self):
        """The sentinel is not a scope a user may pass, and saying so is fine.

        What is not fine is answering with an unhandled traceback — which is
        also, today, the only thing a user could possibly try in order to reach
        their own data.
        """
        found = _invoke("recall", "retry", "--repo", "__visp_unscoped__")

        assert not isinstance(found.exception, ValueError), (
            "Passing the reserved scope crashed instead of being refused: "
            f"{found.exception!r}"
        )


@pytest.mark.usefixtures("cli_env")
class TestScopedPathStillWorks:
    """The converse. A fix must not become 'refuse everything'."""

    def test_explicit_scope_round_trips(self):
        recorded = _invoke("record", CONTENT, "--repo", "demo")
        assert recorded.exit_code == 0, (
            f"record refused an explicitly scoped write: {recorded.stdout!r} {recorded.exception!r}"
        )

        found = _invoke("recall", "retry", "--repo", "demo")
        assert found.exit_code == 0, f"recall failed for an explicit scope: {found.exception!r}"
        assert "retry" in found.stdout, (
            f"An explicitly scoped memory did not come back. Output: {found.stdout!r}"
        )

    def test_scope_still_isolates_projects(self):
        """Scoping must keep meaning what it says: one project cannot read another.

        Without this, 'fixing' the round trip by making recall ignore scope
        would pass every other test in this file while destroying the isolation
        the scope exists to provide.
        """
        assert _invoke("record", CONTENT, "--repo", "project-a").exit_code == 0

        found = _invoke("recall", "retry", "--repo", "project-b")
        assert "retry limit" not in found.stdout, (
            "A memory recorded under project-a was served to project-b. Repository scope "
            "no longer isolates anything."
        )

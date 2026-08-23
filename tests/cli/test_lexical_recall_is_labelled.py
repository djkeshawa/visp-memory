"""LC-90 — a score produced without vectors is never printed as if it had them.

THE DEFECT THIS PINS. `recall` printed a column headed "Score" holding
`text_similarity` output, and `doctor` reported the reason for it as the single
word "fallback" followed by "Fallback provider in use." — a status with no
repair, on the documented lean-install path, where the fallback is the norm.

Both halves cost the same thing. Two battle-ground rounds quoted 0.60 and 0.67
from that column as semantic similarity, because nothing on the page said the
numbers were lexical overlap and nothing said semantic recall was off.

The banner is deliberately on `recall` and not on provider construction. A
warning that fires on every CLI invocation on the lean path is a defect this
codebase already fixed once (see `_warn_noop_fallback`); a label attached to the
numbers it qualifies is not.
"""

import pytest
from typer.testing import CliRunner

from visp_memory.core.embedding_status import LEXICAL_SCORE_HEADER
from visp_memory.interfaces.cli import app

runner = CliRunner()

REPO = "lc90-repo"
FACT = "Due dates are stored as plain YYYY-MM-DD strings in todos.json"


def _invoke(*args: str):
    return runner.invoke(app, list(args))


def _flat(output: str) -> str:
    """Collapse Rich's soft wrapping so an assertion reads as a sentence."""
    return " ".join(output.split())


@pytest.mark.usefixtures("cli_env")
class TestRecallSaysWhatItsScoresAre:
    def test_the_score_column_is_headed_lexical_not_score(self):
        assert _invoke("record", FACT, "--repo", REPO).exit_code == 0

        result = _invoke("recall", "due dates in todos.json", "--repo", REPO)

        assert result.exit_code == 0, result.output
        assert LEXICAL_SCORE_HEADER in result.output
        assert "Score" not in result.output

    def test_a_banner_says_the_results_are_keyword_matches(self):
        assert _invoke("record", FACT, "--repo", REPO).exit_code == 0

        result = _invoke("recall", "due dates in todos.json", "--repo", REPO)

        assert "not semantic similarity" in _flat(result.output)

    def test_the_banner_survives_an_empty_result(self):
        # A lexical-only search is at its most misleading when a paraphrase
        # returns nothing: without the banner that reads as an empty store.
        assert _invoke("record", FACT, "--repo", REPO).exit_code == 0

        result = _invoke("recall", "how are deadlines represented", "--repo", REPO)

        assert "No results found" in result.output
        assert "matching keywords" in _flat(result.output)

    def test_the_banner_carries_the_pip_extra_intact(self):
        # Rich reads square brackets as style tags. Unescaped, this advice
        # printed as `pip install 'visp-memory'`, which installs the wrong thing.
        assert _invoke("record", FACT, "--repo", REPO).exit_code == 0

        result = _invoke("recall", "due dates", "--repo", REPO)

        assert "visp-memory[local-embeddings]" in _flat(result.output)


@pytest.fixture
def auto_falls_through_to_noop(monkeypatch):
    """The ticket's exact state: `configured=auto`, `effective=noop`.

    Drives the real auto-selection loop rather than stubbing its result — the
    candidates are made genuinely unavailable, exactly as they are on a lean
    install with no network, and `get_embedding_provider` decides for itself.
    """
    monkeypatch.setenv("VISP_MEMORY_EMBEDDING_PROVIDER", "auto")
    for key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "EMBEDDING_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        "visp_memory.core.embeddings._sentence_transformers_available", lambda: False
    )

    def _no_daemon(*args, **kwargs):
        raise RuntimeError("no ollama daemon")

    monkeypatch.setattr("visp_memory.core.embeddings.OllamaProvider", _no_daemon)


@pytest.mark.usefixtures("cli_env", "auto_falls_through_to_noop")
class TestDoctorCarriesTheRepairNotJustTheStatus:
    def test_a_fallback_status_is_followed_by_what_to_install(self):
        result = _invoke("doctor")

        assert result.exit_code == 0, result.output
        flat = _flat(result.output)
        assert "visp-memory[local-embeddings]" in flat
        assert "OPENROUTER_API_KEY" in flat

    def test_the_status_message_says_what_the_fallback_costs(self):
        result = _invoke("doctor")

        assert "recall matches keywords, not meaning" in _flat(result.output)

    def test_the_remediation_is_machine_readable_for_a_host_to_consume(self):
        # visp-hyper-agent's own doctor reports PASS over this state. It cannot
        # do better than the field it reads, so the field exists.
        import json

        result = _invoke("doctor", "--format", "json")

        payload = json.loads(result.output)
        embedding = payload["providers"]["embedding"]
        assert embedding["effective_provider"] == "noop"
        assert embedding["status"] == "fallback"
        assert "visp-memory[local-embeddings]" in embedding["remediation"]


@pytest.mark.usefixtures("cli_env")
class TestADeliberateChoiceIsNotTreatedAsAFault:
    def test_a_deliberately_disabled_provider_is_not_offered_a_repair(self, monkeypatch):
        # "disabled" is a choice the operator made. Telling them to install
        # something is noise, and noise is what trains people to stop reading.
        import json

        monkeypatch.setenv("VISP_MEMORY_EMBEDDING_PROVIDER", "noop")
        result = _invoke("doctor", "--format", "json")

        embedding = json.loads(result.output)["providers"]["embedding"]
        assert embedding["status"] == "disabled"
        assert embedding["remediation"] is None

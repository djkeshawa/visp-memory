"""P11-MEM-02 says every write channel assigns provenance. The core storage
write channel does not.

A memory stored through ``LocalStorage.store_memory`` is persisted with no
provenance, so ``assess`` rates it ``unknown`` / trust ``0.0`` and the unsolicited
trust gate quarantines it. The memory saves successfully and is then invisible to
every prompt-adjacent read — the failure is silent in both directions.

These are marked ``xfail(strict=True)`` deliberately: they document a defect that
is real today, and they will fail loudly the moment it is fixed, which is the
signal to delete the marker rather than the test.

The fix is not obvious enough to guess at. Provenance tiers are the trust model,
so choosing what a bare storage write is worth is a governance decision, not an
implementation detail. Two defensible options:

  1. ``store_memory`` grows a required provenance argument, so a caller cannot
     write without saying where the content came from. Honest, and it breaks
     every existing caller.
  2. It defaults to the lowest tier that is still injectable, and callers that
     know better raise it. Nothing breaks, but a default that clears the gate
     weakens the gate for anything that forgets to set it.
"""

import pathlib
import tempfile

import pytest

from visp_memory.core.storage import LocalStorage
from visp_memory.core.trust import DEFAULT_MIN_TRUST, assess, filter_unsolicited


@pytest.fixture
def storage() -> LocalStorage:
    return LocalStorage(pathlib.Path(tempfile.mkdtemp()))


@pytest.mark.xfail(
    strict=True,
    reason="P11-MEM-02: store_memory assigns no provenance, so the row rates unknown/0.0",
)
def test_core_storage_write_channel_assigns_provenance(storage: LocalStorage) -> None:
    """A memory written through storage should carry provenance the trust gate can read."""
    memory_id = storage.store_memory("Alpha warning", repo_id="r1", category="warning")

    stored = storage.get_memory(memory_id)
    assessment = assess(dict(stored))

    assert assessment.tier.value != "unknown", (
        "a memory written through the core storage channel has no provenance tier"
    )
    assert assessment.trust > 0.0


@pytest.mark.xfail(
    strict=True,
    reason="P11-MEM-02: unprovenanced storage writes are quarantined from every unsolicited read",
)
def test_stored_memory_survives_the_unsolicited_trust_gate(storage: LocalStorage) -> None:
    """Saving a memory and never being able to surface it is a silent loss.

    This is the user-visible half of the defect: the write reports success, and
    the content is then filtered out of every prompt-adjacent read with no error
    at either end.
    """
    memory_id = storage.store_memory("Alpha warning", repo_id="r1", category="warning")
    stored = dict(storage.get_memory(memory_id))

    result = filter_unsolicited([stored], min_trust=DEFAULT_MIN_TRUST)

    assert [memory["content"] for memory in result.allowed] == ["Alpha warning"], (
        "a memory that saved successfully is quarantined from unsolicited reads; "
        f"rejected because: {[r.assessment.reason for r in result.rejected]}"
    )

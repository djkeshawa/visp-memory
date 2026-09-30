"""An idempotent retry is the same write when it would persist the same JSON.

Comparing decoded Python objects got both directions wrong: a tuple retried
against the stored list, an int key against the stored string key, and NaN
against NaN were rejected, while ``True`` against ``1`` and ``1`` against ``1.0``
(equal in Python, different in JSON) were accepted as the same write.
"""

import json

import pytest

from visp_memory.core.attribution import metadata_matches
from visp_memory.core.storage import EvidenceImmutableError, LocalStorage

NAN = float("nan")

# (first write, retry): each pair persists the same JSON value.
SAME_WRITE = [
    ({"a": (1, 2)}, {"a": (1, 2)}),
    ({"a": [1, 2]}, {"a": (1, 2)}),
    ({1: "x"}, {1: "x"}),
    ({"n": NAN}, {"n": NAN}),
    ({"a": 1, "b": {"c": 2}}, {"b": {"c": 2}, "a": 1}),
]

# (first write, retry): equal as Python objects, different once stored.
DIFFERENT_WRITE = [
    ({"a": True}, {"a": 1}),
    ({"a": 1}, {"a": 1.0}),
    ({"a": 0}, {"a": False}),
    ({"a": {"b": True}}, {"a": {"b": 1}}),
]


@pytest.mark.parametrize(("first", "retry"), SAME_WRITE)
def test_a_retry_that_persists_the_same_json_matches(first, retry):
    assert metadata_matches(json.dumps(first), retry)
    assert metadata_matches(first, retry)


@pytest.mark.parametrize(("first", "retry"), DIFFERENT_WRITE)
def test_values_equal_in_python_but_not_in_json_do_not_match(first, retry):
    assert not metadata_matches(json.dumps(first), retry)
    assert not metadata_matches(first, retry)


def test_written_by_is_still_ignored_with_canonical_comparison():
    stored = '{"a": [1, 2], "written_by": {"agent": "alice"}}'
    assert metadata_matches(stored, {"a": (1, 2), "written_by": {"agent": "bob"}})
    assert not metadata_matches(stored, {"a": (1, 3), "written_by": {"agent": "alice"}})


def test_metadata_that_cannot_be_stored_matches_nothing():
    assert not metadata_matches('{"a": 1}', {"a": object()})


@pytest.mark.parametrize(("first", "retry"), SAME_WRITE)
def test_an_evidence_retry_that_persists_the_same_json_is_accepted(tmp_path, first, retry):
    storage = LocalStorage(tmp_path)
    storage.store_evidence("output", "repo-a", evidence_id="ev", metadata=first)
    assert storage.store_evidence("output", "repo-a", evidence_id="ev", metadata=retry) == "ev"


@pytest.mark.parametrize(("first", "retry"), DIFFERENT_WRITE)
def test_an_evidence_retry_that_changes_the_stored_json_is_refused(tmp_path, first, retry):
    storage = LocalStorage(tmp_path)
    storage.store_evidence("output", "repo-a", evidence_id="ev", metadata=first)
    with pytest.raises(EvidenceImmutableError):
        storage.store_evidence("output", "repo-a", evidence_id="ev", metadata=retry)

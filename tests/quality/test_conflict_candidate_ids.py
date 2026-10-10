"""The detector's conflicting ids are untrusted model output.

``Memory.learn`` supersedes and links whatever ids the verdict names, so an id
the model invented (or copied from elsewhere) used to supersede a memory the
detector was never shown, or fail the edge write and quarantine a valid new
belief. Only the candidates passed in can be in conflict.
"""

import json

from visp_memory import Memory, MemoryConfig
from visp_memory.quality.conflict import ConflictDetector


class _Stub:
    def __init__(self, ids):
        self.ids = ids

    def completion(self, prompt, **kwargs):
        return json.dumps({"conflict": True, "reason": "x", "conflicting_ids": self.ids})


CANDIDATES = [{"id": "a", "content": "Sky is blue"}, {"id": "b", "content": "Grass is green"}]


def test_ids_outside_the_candidates_are_dropped():
    detector = ConflictDetector(storage=None, llm_client=_Stub(["ghost", "b", "b"]))

    verdict = detector.detect_conflicts("Grass is purple", CANDIDATES)

    assert verdict.has_conflict
    assert verdict.conflict["conflicting_ids"] == ["b"]


def test_a_conflict_naming_no_candidate_is_undetermined_not_found():
    detector = ConflictDetector(storage=None, llm_client=_Stub(["ghost"]))

    verdict = detector.detect_conflicts("Grass is purple", CANDIDATES)

    assert not verdict.determined
    assert not verdict.has_conflict
    assert verdict.reason


def test_learn_does_not_supersede_a_memory_the_detector_never_saw(tmp_path):
    config = MemoryConfig(project_name="t", repo_id="repo-a")
    config.storage.data_dir = tmp_path / "data"
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    episode = memory.record("Shipped the release 2.4 on Friday")
    memory.learn("Auth tokens expire after 15 minutes")

    memory.conflict_detector.client = _Stub([episode])
    new_id = memory.learn("Auth tokens expire after 60 minutes", detect_conflicts=True)

    assert memory._storage.get_memory(episode)["status"] == "active"
    new = memory._storage.get_memory(new_id)
    assert new["status"] == "active"
    assert "conflict_unverified" in (new.get("quality_flags") or [])

from visp_memory import Memory, MemoryConfig
from visp_memory.capture.git import CaptureManifest, GitCapture, capture_content_hash
from visp_memory.capture.tests import TestCapture
from visp_memory.core.trust import Provenance, provenance_of


def test_post_commit_hook_uses_cli_ref_option():
    capture = GitCapture.__new__(GitCapture)

    script = capture._generate_post_commit_script()

    assert "visp-memory capture git commit --ref HEAD" in script
    assert "visp-memory capture git commit HEAD" not in script


def test_capture_manifest_records_and_skips_unchanged_source(tmp_path):
    config = MemoryConfig()
    config.storage.data_dir = tmp_path / "memory"
    config.embedding.provider = "noop"
    memory = Memory(config=config)

    manifest = CaptureManifest(memory)
    content_hash = capture_content_hash({"commit": "abc", "message": "fix bug"})

    status, entry = manifest.check("git_commit", "abc", content_hash)
    assert status == "changed"
    assert entry is None

    manifest.record("git_commit", "abc", content_hash, ["mem-1"], status=status)
    status, entry = CaptureManifest(memory).check("git_commit", "abc", content_hash)

    assert status == "unchanged"
    assert entry["output_memory_ids"] == ["mem-1"]
    assert CaptureManifest(memory).summary() == {
        "changed": 0,
        "unchanged": 1,
        "stale": 0,
        "entries": 1,
    }


def test_test_capture_skips_unchanged_report(tmp_path):
    config = MemoryConfig()
    config.storage.data_dir = tmp_path / "memory"
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    report = tmp_path / "report.xml"
    report.write_text(
        """
        <testsuite tests="1" failures="1" errors="0">
          <testcase name="test_auth" file="tests/test_auth.py">
            <failure message="failed">assert False</failure>
          </testcase>
        </testsuite>
        """
    )
    capture = TestCapture(memory)

    first = capture.on_pytest_session(str(report))
    second = capture.on_pytest_session(str(report))

    assert len(first) == 1
    captured = memory._storage.get_memory(first[0])
    assert provenance_of(captured) is Provenance.UNKNOWN
    assert captured["metadata"]["write_channel"] == "test_capture"
    assert second == []
    assert capture.last_manifest_report == {"changed": 0, "unchanged": 1, "stale": 0}

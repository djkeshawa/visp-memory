import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.capture.tests import TestCapture


@pytest.fixture
def memory(tmp_path):
    config = MemoryConfig(repo_id="test-capture")
    config.storage.data_dir = tmp_path / "memory"
    config.embedding.provider = "noop"
    instance = Memory(config=config)
    yield instance
    instance.close()


@pytest.mark.parametrize("counters", ["", 'failures="0" errors="0"'])
def test_capture_reads_failure_elements_without_relying_on_summary_counts(
    memory, tmp_path, counters
):
    report = tmp_path / "report.xml"
    report.write_text(
        f"""<testsuite {counters}>
          <testcase name="failed" file="test_example.py">
            <failure message="assertion failed">assert False</failure>
          </testcase>
          <testcase name="errored"><error message="setup failed">trace</error></testcase>
          <testcase name="passed" />
          <testcase name="skipped"><skipped /></testcase>
        </testsuite>""",
        encoding="utf-8",
    )

    capture = TestCapture(memory)
    ids = capture.on_pytest_session(str(report))

    assert len(ids) == 2
    records = [memory._storage.get_memory(memory_id) for memory_id in ids]
    assert {record["metadata"]["test_name"] for record in records} == {"failed", "errored"}
    assert all(record["metadata"]["write_channel"] == "test_capture" for record in records)
    assert capture.on_pytest_session(str(report)) == []


def test_capture_includes_root_testcases_alongside_nested_suites(memory, tmp_path):
    report = tmp_path / "report.xml"
    report.write_text(
        """<testsuite failures="2">
          <testcase name="root"><failure message="root failure" /></testcase>
          <testsuite failures="1">
            <testcase name="nested"><failure message="nested failure" /></testcase>
          </testsuite>
        </testsuite>""",
        encoding="utf-8",
    )

    ids = TestCapture(memory).on_pytest_session(str(report))

    assert len(ids) == 2
    names = {memory._storage.get_memory(memory_id)["metadata"]["test_name"] for memory_id in ids}
    assert names == {"root", "nested"}

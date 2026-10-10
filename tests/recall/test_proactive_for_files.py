"""Multi-file context must carry what single-file context carries.

``for_files`` aggregated a fixed list of sections that lacked
``recent_changes``, so a change recorded against a file vanished as soon as a
second file was in the set. Its filter diagnostics were also whatever the last
``on_file_open`` left behind rather than a combination over every file.
"""

from visp_memory import Memory, MemoryConfig
from visp_memory.core.trust import WriteChannel
from visp_memory.recall.proactive import ProactiveRecall


def _memory(tmp_path) -> Memory:
    config = MemoryConfig(repo_id="repo-a")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    return Memory(config=config)


def test_recent_changes_survive_multi_file_aggregation(tmp_path):
    memory = _memory(tmp_path)
    change_id = memory.record(
        "Refactored src/auth/session.py to split refresh logic",
        category="refactor",
        _write_channel=WriteChannel.TEST_CAPTURE,
    )

    context = ProactiveRecall(memory).for_files(
        ["src/auth/session.py", "src/auth/tokens.py", "src/auth/session.py"]
    )

    assert [item["id"] for item in context["recent_changes"]] == [change_id]
    assert change_id in ProactiveRecall(memory).format_injection(context)


def test_multi_file_diagnostics_combine_every_file(tmp_path):
    memory = _memory(tmp_path)
    memory.record(
        "Refactored src/auth/session.py to split refresh logic",
        _write_channel=WriteChannel.TEST_CAPTURE,
    )
    memory.record(
        "Fixed rounding in src/billing/ledger.py",
        _write_channel=WriteChannel.TEST_CAPTURE,
    )
    files = ["src/auth/session.py", "src/billing/ledger.py"]

    per_file = [ProactiveRecall(memory).on_file_open(path) for path in files]
    recall = ProactiveRecall(memory)
    context = recall.for_files(files)

    for key in ("trust_filter", "eligibility_filter"):
        assert context[key]["considered_count"] == sum(
            single[key]["considered_count"] for single in per_file
        )
        assert context[key]["allowed_count"] == sum(
            single[key]["allowed_count"] for single in per_file
        )
    assert recall.last_trust_filter == context["trust_filter"]
    assert recall.last_eligibility_filter == context["eligibility_filter"]

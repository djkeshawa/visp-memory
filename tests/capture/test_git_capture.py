from llm_memory.capture.git import GitCapture


def test_post_commit_hook_uses_cli_ref_option():
    capture = GitCapture.__new__(GitCapture)

    script = capture._generate_post_commit_script()

    assert "llm-memory capture git commit --ref HEAD" in script
    assert "llm-memory capture git commit HEAD" not in script

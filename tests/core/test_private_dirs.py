import os
import stat

import pytest

from visp_memory.core.paths import ensure_private_dir
from visp_memory.server.owner_token import cleanup_owner_token_files, create_owner_token_files

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


@posix_only
def test_fresh_directory_is_private_even_under_a_permissive_umask(tmp_path):
    previous = os.umask(0o002)
    try:
        target = ensure_private_dir(tmp_path / "root")
    finally:
        os.umask(previous)
    assert _mode(target) == 0o700


@posix_only
def test_existing_group_readable_directory_is_tightened(tmp_path):
    target = tmp_path / "root"
    target.mkdir()
    target.chmod(0o755)

    ensure_private_dir(target)

    assert _mode(target) == 0o700


@posix_only
def test_permissions_are_never_widened(tmp_path):
    target = tmp_path / "root"
    target.mkdir()
    target.chmod(0o500)

    ensure_private_dir(target)

    assert _mode(target) == 0o500
    target.chmod(0o700)


@posix_only
def test_directory_owned_by_someone_else_is_left_alone_with_a_warning(
    tmp_path, monkeypatch, caplog
):
    target = tmp_path / "root"
    target.mkdir()
    target.chmod(0o755)
    owner = target.stat().st_uid
    monkeypatch.setattr(os, "getuid", lambda: owner + 1)

    with caplog.at_level("WARNING"):
        ensure_private_dir(target)

    assert _mode(target) == 0o755
    assert "not owned" in caplog.text


@posix_only
def test_chmod_failure_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    target = tmp_path / "root"
    target.mkdir()
    target.chmod(0o755)

    def refuse(self, mode):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(type(target), "chmod", refuse)

    with caplog.at_level("WARNING"):
        assert ensure_private_dir(target) == target

    assert "Could not make" in caplog.text


def test_existing_contents_survive_on_any_platform(tmp_path):
    target = tmp_path / "root"
    (target / "child").mkdir(parents=True)
    (target / "keep.txt").write_text("x", encoding="utf-8")

    ensure_private_dir(target)

    assert (target / "keep.txt").read_text(encoding="utf-8") == "x"
    assert (target / "child").is_dir()


@posix_only
def test_owner_token_run_dir_is_tightened_and_token_stays_0600(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    run.chmod(0o755)

    files = create_owner_token_files(
        port=8765,
        url="http://127.0.0.1:8765",
        data_dir=tmp_path / "data",
        directory=run,
    )

    assert _mode(run) == 0o700
    assert _mode(files.token_path) == 0o600
    cleanup_owner_token_files(files)

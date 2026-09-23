"""Release checks must validate only distributions produced by the current run."""

import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import release_check


def write_wheel(path, version):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"visp_memory-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: visp-memory\nVersion: {version}\n",
        )


@pytest.fixture
def isolated_release(tmp_path, monkeypatch):
    old_dist = tmp_path / "dist"
    old_dist.mkdir()
    write_wheel(old_dist / "visp_memory-0.5.0-py3-none-any.whl", "0.5.0")
    (old_dist / "standalone-binary").write_bytes(b"keep existing artifacts")
    commands = []
    outputs = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1:3] == ["-m", "build"]:
            output = (
                Path(command[command.index("--outdir") + 1])
                if "--outdir" in command
                else old_dist
            )
            output.mkdir(exist_ok=True)
            for version in outputs:
                write_wheel(output / f"visp_memory-{version}-py3-none-any.whl", version)
                (output / f"visp_memory-{version}.tar.gz").touch()

    monkeypatch.setattr(release_check, "ROOT", tmp_path)
    monkeypatch.setattr(release_check, "run", run)
    monkeypatch.setattr(release_check, "has_module", lambda _: True)
    monkeypatch.setattr(
        release_check.venv,
        "EnvBuilder",
        lambda **kwargs: SimpleNamespace(create=lambda _: None),
    )
    monkeypatch.setattr(
        sys, "argv", ["release_check.py", "--skip-tests", "--skip-frontend"]
    )
    return old_dist, commands, outputs


def test_release_checks_only_the_new_build_and_preserves_existing_outputs(isolated_release):
    old_dist, commands, outputs = isolated_release
    outputs.append("0.7.3")

    assert release_check.main() == 0

    install = next(command for command in commands if "install" in command)
    wheel = Path(install[-1].removesuffix("[api,mcp]"))
    assert wheel.name == "visp_memory-0.7.3-py3-none-any.whl"
    assert wheel.parent != old_dist
    twine = next(command for command in commands if "twine" in command)
    assert {Path(path).name for path in twine[4:]} == {
        "visp_memory-0.7.3-py3-none-any.whl",
        "visp_memory-0.7.3.tar.gz",
    }
    assert all(Path(path).parent == wheel.parent for path in twine[4:])
    assert (old_dist / "standalone-binary").read_bytes() == b"keep existing artifacts"
    assert (old_dist / "visp_memory-0.5.0-py3-none-any.whl").is_file()
    assert not wheel.parent.exists(), "Temporary release artifacts must be cleaned up"


@pytest.mark.parametrize("versions", [[], ["0.7.3", "0.7.4"]])
def test_release_refuses_missing_or_ambiguous_new_wheels(isolated_release, versions):
    _, commands, outputs = isolated_release
    outputs.extend(versions)

    with pytest.raises(SystemExit, match="exactly one wheel"):
        release_check.main()

    assert not any("install" in command for command in commands)

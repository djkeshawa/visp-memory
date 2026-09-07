"""Release tooling and distribution-content regression tests."""

from __future__ import annotations

import ast
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_built_metadata_is_accepted_by_release_checker(tmp_path, kind):
    pytest.importorskip("hatchling")
    package = pytest.importorskip("twine.package")
    from hatchling.builders.sdist import SdistBuilder
    from hatchling.builders.wheel import WheelBuilder

    builder = (WheelBuilder if kind == "wheel" else SdistBuilder)(str(ROOT))
    artifact = next(builder.build(directory=str(tmp_path)))

    checked = package.PackageFile.from_filename(str(tmp_path / artifact), None)
    assert checked.metadata["name"] == "visp-memory"


def test_legal_files_are_nonempty_and_declared_for_python_distributions():
    assert (ROOT / "LICENSE").read_text().strip()
    assert (ROOT / "NOTICE").read_text().strip()

    pyproject = (ROOT / "pyproject.toml").read_text()
    assert 'license-files = ["LICENSE", "NOTICE"]' in pyproject
    assert '"LICENSE"' in pyproject
    assert '"NOTICE"' in pyproject


def test_release_wheel_smoke_uses_isolated_storage():
    script = (ROOT / "scripts/release_check.py").read_text()

    assert 'smoke_env["VISP_MEMORY_STORAGE_DATA_DIR"]' in script
    assert "env=smoke_env" in script


def test_make_defaults_to_python3_but_remains_overridable():
    makefile = (ROOT / "Makefile").read_text()
    assert "PYTHON ?= python3" in makefile
    assert "PIP ?= $(PYTHON) -m pip" in makefile


def test_container_and_pyinstaller_include_legal_files():
    dockerfile = (ROOT / "Dockerfile").read_text()
    spec = (ROOT / "visp-memory.spec").read_text()

    assert "COPY LICENSE NOTICE ./" in dockerfile
    assert "('NOTICE', '.')" in spec
    assert "('LICENSE', '.')" in spec


def test_shared_standalone_assets_describe_supported_capabilities():
    standalone = ROOT / "standalone"
    readme = (standalone / "README-STANDALONE.md").read_text()

    assert (standalone / "start-server.sh").read_text().strip()
    assert (standalone / "start-server.ps1").read_text().strip()
    assert "CLI, API server, and embedded" in readme
    assert "dashboard. It does not contain the MCP server." in readme
    assert "Python package" in readme
    assert "container image" in readme


def test_standalone_builds_use_shared_assets_and_omit_mcp():
    builder = (ROOT / "build_standalone.sh").read_text()
    workflow = (ROOT / ".github/workflows/build-release.yml").read_text()
    spec = (ROOT / "visp-memory.spec").read_text()
    spec_tree = ast.parse(spec)
    analysis = next(
        node
        for node in ast.walk(spec_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Analysis"
    )
    analysis_options = {
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in analysis.keywords
        if keyword.arg in {"hiddenimports", "excludes"}
    }

    assert "api,mcp" not in builder
    assert 'PYTHON="${PYTHON:-python3}"' in builder
    assert '"$PYTHON" -m pip install pyinstaller' in builder
    assert '"$PYTHON" build_frontend.py' in builder
    assert '"$PYTHON" -m PyInstaller visp-memory.spec' in builder
    assert "visp_memory.interfaces.mcp" not in analysis_options["hiddenimports"]
    assert "sentence_transformers" not in analysis_options["hiddenimports"]
    assert {
        "arcadedb_embedded",
        "jpype",
        "sentence_transformers",
        "tensorflow",
        "torch",
        "transformers",
    } <= set(analysis_options["excludes"])
    assert "standalone/start-server.sh" in builder
    assert "standalone/start-server.ps1" in builder
    assert "standalone/start-server.sh standalone/start-server.ps1" in workflow
    assert "verify_distribution_artifacts.py" in workflow

    packaging_guide = (ROOT / "docs/deployment/PACKAGING.md").read_text()
    releasing_guide = (ROOT / "docs/deployment/RELEASING.md").read_text()
    assert "api,mcp,capture,analysis,chroma,neo4j" not in packaging_guide
    assert "visp_memory_mcp" not in releasing_guide
    assert "visp_memory-X.Y.Z-py3-none-any.whl" in releasing_guide


def test_artifact_verifier_accepts_complete_standalone_zip(tmp_path):
    archive = tmp_path / "visp-memory-standalone-Windows-x86_64.zip"
    files = {
        "visp-memory-standalone/visp-memory.exe": b"binary",
        "visp-memory-standalone/LICENSE": b"license",
        "visp-memory-standalone/NOTICE": b"notice",
        "visp-memory-standalone/README-STANDALONE.md": b"readme",
        "visp-memory-standalone/start-server.sh": b"#!/bin/sh",
        "visp-memory-standalone/start-server.ps1": b"Write-Host start",
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)

    subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_distribution_artifacts.py"), str(archive)],
        check=True,
        cwd=ROOT,
    )


def test_artifact_verifier_rejects_missing_notice(tmp_path):
    wheel = tmp_path / "visp_memory-0.5.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as bundle:
        bundle.writestr("visp_memory-0.5.1.dist-info/licenses/LICENSE", "license")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_distribution_artifacts.py"), str(wheel)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "missing NOTICE" in result.stderr

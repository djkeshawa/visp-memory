"""Release tooling and distribution-content regression tests."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_legal_files_are_nonempty_and_declared_for_python_distributions():
    assert (ROOT / "LICENSE").read_text().strip()
    assert (ROOT / "NOTICE").read_text().strip()

    pyproject = (ROOT / "pyproject.toml").read_text()
    assert 'license-files = ["LICENSE", "NOTICE"]' in pyproject
    assert '"LICENSE"' in pyproject
    assert '"NOTICE"' in pyproject


def test_release_script_fails_closed_before_mutating_version():
    script = (ROOT / "create-release.sh").read_text()

    clean_check = script.index("git status --porcelain --untracked-files=all")
    final_confirmation = script.index('read -p "Proceed? (y/n) "')
    version_edit = script.index('sed -i "s/version =')

    assert clean_check < final_confirmation < version_edit
    assert "git checkout pyproject.toml" not in script
    assert "Continue anyway?" not in script[: script.index("# Get current version")]


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
    assert "| Standalone archive | Yes | Yes | Yes | No |" in readme
    assert "Python package" in readme
    assert "Container image" in readme


def test_standalone_builds_use_shared_assets_and_omit_mcp():
    builder = (ROOT / "build_standalone.sh").read_text()
    workflow = (ROOT / ".github/workflows/build-release.yml").read_text()
    spec = (ROOT / "visp-memory.spec").read_text()

    assert "api,mcp" not in builder
    assert 'PYTHON="${PYTHON:-python3}"' in builder
    assert '"$PYTHON" -m pip install pyinstaller' in builder
    assert '"$PYTHON" build_frontend.py' in builder
    assert '"$PYTHON" -m PyInstaller visp-memory.spec' in builder
    assert "visp_memory.interfaces.mcp" not in spec
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

#!/usr/bin/env python3
"""Run the local release-readiness checks for Visp Memory."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
) -> None:
    """Run a command and stop on failure."""
    print(f"\n$ {' '.join(command)}")
    subprocess.run(command, cwd=cwd, check=True, env=env)


def has_module(module: str) -> bool:
    """Return whether a Python module can be imported."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def smoke_wheel(wheel: Path, *, dashboard_required: bool) -> None:
    """Install the new wheel with isolated storage and verify its version and assets."""
    with zipfile.ZipFile(wheel) as archive:
        metadata_paths = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_paths) != 1:
            raise SystemExit("The built wheel must contain exactly one package metadata file.")
        metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
    expected_version = metadata.get("Version")
    if metadata.get("Name") != "visp-memory" or not expected_version:
        raise SystemExit("The built wheel has missing or unexpected package metadata.")

    with tempfile.TemporaryDirectory(prefix="visp-memory-wheel-smoke-") as temp_dir:
        environment = Path(temp_dir) / "venv"
        smoke_env = os.environ.copy()
        smoke_env["VISP_MEMORY_STORAGE_DATA_DIR"] = str(Path(temp_dir) / "data")
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        cli = environment / (
            "Scripts/visp-memory.exe" if sys.platform == "win32" else "bin/visp-memory"
        )
        run([str(python), "-m", "pip", "install", f"{wheel}[api,mcp]"])
        run([str(cli), "--help"], env=smoke_env)
        checks = [
            "from importlib.metadata import version; "
            "from pathlib import Path; "
            "import visp_memory.server.app as api; "
            "from visp_memory.interfaces.mcp import MCP_AVAILABLE; "
            f"assert version('visp-memory') == {expected_version!r}; assert MCP_AVAILABLE"
        ]
        # The release workflow verifies shipped dashboard assets separately when
        # its quality job explicitly skips building the frontend.
        if dashboard_required:
            checks.append(
                "; static = Path(api.__file__).parent / 'static'; "
                "assert (static / 'index.html').is_file(), "
                "'dashboard assets missing from the wheel'"
            )
        run([str(python), "-c", "".join(checks)], env=smoke_env)


def check_package(*, skip_twine: bool, dashboard_required: bool) -> None:
    """Build and check fresh distributions without consuming or deleting old outputs."""
    if not has_module("build"):
        raise SystemExit("Python package 'build' is required. Install with: pip install build")
    if not skip_twine and not has_module("twine"):
        raise SystemExit("Python package 'twine' is required. Install it or pass --skip-twine.")

    with tempfile.TemporaryDirectory(prefix="visp-memory-distributions-") as temp_dir:
        output = Path(temp_dir)
        run([sys.executable, "-m", "build", "--outdir", str(output)])
        wheels = sorted(output.glob("*.whl"))
        sdists = sorted(output.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise SystemExit("The package build must produce exactly one wheel and one sdist.")
        dist_files = [str(wheels[0]), str(sdists[0])]
        run([sys.executable, "scripts/verify_distribution_artifacts.py", *dist_files])
        if not skip_twine:
            run([sys.executable, "-m", "twine", "check", *dist_files])
        smoke_wheel(wheels[0], dashboard_required=dashboard_required)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-twine",
        action="store_true",
        help="Skip Twine distribution validation explicitly.",
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Skip the dashboard build step.",
    )
    parser.add_argument(
        "--skip-package",
        action="store_true",
        help="Skip building and checking Python distributions.",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip pytest.",
    )
    parser.add_argument(
        "--with-packaged-smoke",
        action="store_true",
        help="Run packaged dashboard HTTP smoke after the frontend build.",
    )
    parser.add_argument(
        "--with-browser-smoke",
        action="store_true",
        help="Run packaged dashboard smoke in Chromium via @playwright/test.",
    )
    parser.add_argument(
        "--with-docker-build",
        action="store_true",
        help="Build the Docker image as part of release readiness.",
    )
    args = parser.parse_args()

    run([sys.executable, "-m", "ruff", "check", "."])
    run([sys.executable, "scripts/verify_github_workflows.py"])

    if not args.skip_tests:
        run([sys.executable, "-m", "pytest", "-q"])

    run(
        [
            sys.executable,
            "-c",
            (
                "from visp_memory import __version__; "
                "from visp_memory.interfaces.mcp import MCP_AVAILABLE; "
                "print(f'visp-memory {__version__}; MCP available={MCP_AVAILABLE}')"
            ),
        ]
    )

    if not args.skip_frontend:
        run([sys.executable, "build_frontend.py"])
        if args.with_packaged_smoke or args.with_browser_smoke:
            smoke_command = [sys.executable, "scripts/smoke_packaged_dashboard.py"]
            if args.with_browser_smoke:
                smoke_command.append("--browser")
            run(smoke_command)

    if not args.skip_package:
        check_package(skip_twine=args.skip_twine, dashboard_required=not args.skip_frontend)

    if args.with_docker_build:
        run(["docker", "build", "-t", "visp-memory:release-check", "."])

    print("\nRelease checks completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

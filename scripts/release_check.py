#!/usr/bin/env python3
"""Run the local release-readiness checks for Visp Memory."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    """Run a command and stop on failure."""
    print(f"\n$ {' '.join(command)}")
    subprocess.run(command, cwd=cwd, check=True)


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
        if not has_module("build"):
            raise SystemExit("Python package 'build' is required. Install with: pip install build")

        run([sys.executable, "-m", "build", "--wheel"])

        dist_files = [str(path) for path in sorted((ROOT / "dist").glob("*"))]
        if not args.skip_twine:
            if not has_module("twine"):
                raise SystemExit(
                    "Python package 'twine' is required. Install it or pass --skip-twine."
                )
            run([sys.executable, "-m", "twine", "check", *dist_files])

        wheel = next(iter(sorted((ROOT / "dist").glob("*.whl"))), None)
        if wheel is None:
            raise SystemExit("The package build did not produce a wheel.")
        with tempfile.TemporaryDirectory(prefix="visp-memory-wheel-smoke-") as temp_dir:
            environment = Path(temp_dir) / "venv"
            venv.EnvBuilder(with_pip=True).create(environment)
            python = environment / (
                "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
            )
            cli = environment / (
                "Scripts/visp-memory.exe" if sys.platform == "win32" else "bin/visp-memory"
            )
            run([str(python), "-m", "pip", "install", f"{wheel}[api,mcp]"])
            run([str(cli), "--help"])

            # The dashboard is only in the wheel when build_frontend.py ran first, so
            # asserting it under --skip-frontend is self-contradictory: the flag says
            # "do not build the frontend" and the check says "the frontend must be
            # present". That combination failed every release attempt after the
            # assertion was added. The shipped wheel is verified separately, in the
            # release workflow's build-python job, which does build the frontend.
            checks = [
                "from importlib.metadata import version; "
                "from pathlib import Path; "
                "import visp_memory.server.app as api; "
                "from visp_memory.interfaces.mcp import MCP_AVAILABLE; "
                "assert version('visp-memory'); assert MCP_AVAILABLE"
            ]
            if not args.skip_frontend:
                checks.append(
                    "; static = Path(api.__file__).parent / 'static'; "
                    "assert (static / 'index.html').is_file(), "
                    "'dashboard assets missing from the wheel'"
                )
            run([str(python), "-c", "".join(checks)])

    if args.with_docker_build:
        run(["docker", "build", "-t", "visp-memory:release-check", "."])

    print("\nRelease checks completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

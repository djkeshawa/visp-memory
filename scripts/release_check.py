#!/usr/bin/env python3
"""Run the local release-readiness checks for LLM Memory."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
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
                "from llm_memory import __version__; "
                "from llm_memory.interfaces.mcp import MCP_AVAILABLE; "
                "print(f'llm-memory {__version__}; MCP available={MCP_AVAILABLE}')"
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

        if shutil.which("twine"):
            dist_files = [str(path) for path in sorted((ROOT / "dist").glob("*"))]
            run(["twine", "check", *dist_files])
        else:
            print("\nSkipping twine check: twine is not installed.")

    if args.with_docker_build:
        run(["docker", "build", "-t", "llm-memory:release-check", "."])

    print("\nRelease checks completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

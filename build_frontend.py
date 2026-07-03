#!/usr/bin/env python3
"""
Build script for bundling the Next.js frontend with the Python package.
This script is called during package build to create a static export of the dashboard.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def build_frontend(required: bool = True) -> bool:
    """Build the Next.js dashboard and copy to package directory."""
    root_dir = Path(__file__).parent
    dashboard_dir = root_dir / "llm-memory-dashboard"
    out_dir = dashboard_dir / "out"
    target_dir = root_dir / "src" / "llm_memory" / "server" / "static"

    print("=" * 60)
    print("Building LLM Memory Dashboard")
    print("=" * 60)

    # Check if dashboard directory exists
    if not dashboard_dir.exists():
        print(f"Error: Dashboard directory not found at {dashboard_dir}")
        if required:
            return False
        print("Skipping optional frontend build. Package will work without dashboard.")
        return True

    # Resolve the npm executable via PATH. On Windows npm is `npm.cmd`, which a
    # bare subprocess(["npm", ...]) cannot execute (FileNotFoundError even when
    # Node.js is installed); shutil.which honours PATHEXT and finds it.
    npm = shutil.which("npm")
    if npm is None:
        print("Error: npm not found. Please install Node.js to build the dashboard.")
        return not required

    # Check if node_modules exists
    node_modules = dashboard_dir / "node_modules"
    if not node_modules.exists():
        print("\nInstalling frontend dependencies...")
        try:
            subprocess.run(
                [npm, "install"],
                cwd=dashboard_dir,
                check=True,
                capture_output=False,
            )
        except subprocess.CalledProcessError as e:
            print(f"Error: npm install failed: {e}")
            return not required

    # Build the Next.js app
    print("\nBuilding Next.js dashboard...")
    try:
        subprocess.run(
            [npm, "run", "export"],
            cwd=dashboard_dir,
            check=True,
            capture_output=False,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error: Next.js build failed: {e}")
        return not required

    # Copy built files to package
    if out_dir.exists():
        print(f"\nCopying built files to {target_dir}...")
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(out_dir, target_dir)
        print(f"Successfully copied {len(list(target_dir.glob('**/*')))} files")
    else:
        print(f"Error: Build output not found at {out_dir}")
        return not required

    print("\n" + "=" * 60)
    print("Frontend build complete!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--optional",
        action="store_true",
        help="Do not fail if the dashboard cannot be built.",
    )
    args = parser.parse_args()
    sys.exit(0 if build_frontend(required=not args.optional) else 1)

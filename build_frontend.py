#!/usr/bin/env python3
"""
Build script for bundling the Next.js frontend with the Python package.
This script is called during package build to create a static export of the dashboard.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path


def build_frontend():
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
        print(f"Warning: Dashboard directory not found at {dashboard_dir}")
        print("Skipping frontend build. Package will work without dashboard.")
        return

    # Check if node_modules exists
    node_modules = dashboard_dir / "node_modules"
    if not node_modules.exists():
        print("\nInstalling frontend dependencies...")
        try:
            subprocess.run(
                ["npm", "install"],
                cwd=dashboard_dir,
                check=True,
                capture_output=False,
                shell=True
            )
        except subprocess.CalledProcessError as e:
            print(f"Warning: npm install failed: {e}")
            print("Skipping frontend build.")
            return
        except FileNotFoundError:
            print("Warning: npm not found. Please install Node.js to build the dashboard.")
            print("Skipping frontend build. Package will work without dashboard.")
            return

    # Build the Next.js app
    print("\nBuilding Next.js dashboard...")
    try:
        subprocess.run(
            ["npm", "run", "export"],
            cwd=dashboard_dir,
            check=True,
            capture_output=False,
            shell=True
        )
    except subprocess.CalledProcessError as e:
        print(f"Warning: Next.js build failed: {e}")
        print("Skipping frontend build.")
        return

    # Copy built files to package
    if out_dir.exists():
        print(f"\nCopying built files to {target_dir}...")
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(out_dir, target_dir)
        print(f"Successfully copied {len(list(target_dir.glob('**/*')))} files")
    else:
        print(f"Warning: Build output not found at {out_dir}")
        return

    print("\n" + "=" * 60)
    print("Frontend build complete!")
    print("=" * 60)


if __name__ == "__main__":
    build_frontend()

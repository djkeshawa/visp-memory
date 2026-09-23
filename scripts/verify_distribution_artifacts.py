#!/usr/bin/env python3
"""Verify required legal and standalone files in built distributions."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

LEGAL_FILES = ("LICENSE", "NOTICE")
STANDALONE_FILES = (
    "README-STANDALONE.md",
    "start-server.sh",
    "start-server.ps1",
)


def _verify_names(
    artifact: Path,
    names: Iterable[str],
    read: Callable[[str], bytes],
) -> None:
    normalized = [name.replace("\\", "/") for name in names if not name.endswith("/")]
    required = list(LEGAL_FILES)
    if "standalone" in artifact.name.lower() or artifact.is_dir():
        required.extend(STANDALONE_FILES)

    for required_name in required:
        matches = [
            name for name in normalized if PurePosixPath(name).name == required_name
        ]
        if not matches:
            raise SystemExit(f"{artifact}: missing {required_name}")
        if not any(read(name).strip() for name in matches):
            raise SystemExit(f"{artifact}: {required_name} is empty")

    if "standalone" in artifact.name.lower() or artifact.is_dir():
        executable_names = {PurePosixPath(name).name for name in normalized}
        if not executable_names.intersection({"visp-memory", "visp-memory.exe"}):
            raise SystemExit(f"{artifact}: missing standalone executable")


def verify(artifact: Path) -> None:
    if artifact.is_dir():
        names = [str(path.relative_to(artifact)) for path in artifact.rglob("*") if path.is_file()]
        _verify_names(artifact, names, lambda name: (artifact / name).read_bytes())
        return

    if artifact.suffix in {".whl", ".zip"}:
        with zipfile.ZipFile(artifact) as archive:
            _verify_names(artifact, archive.namelist(), archive.read)
        return

    if artifact.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(artifact, "r:gz") as archive:
            names = [member.name for member in archive.getmembers() if member.isfile()]

            def read(name: str) -> bytes:
                extracted = archive.extractfile(name)
                return extracted.read() if extracted is not None else b""

            _verify_names(artifact, names, read)
        return

    raise SystemExit(f"Unsupported artifact type: {artifact}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()
    for artifact in args.artifacts:
        if not artifact.exists():
            raise SystemExit(f"Artifact not found: {artifact}")
        verify(artifact)
        print(f"verified {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Print one version's section of CHANGELOG.md, for the GitHub release body.

Usage: changelog_section.py VERSION [CHANGELOG]

Prints the body of ``## [VERSION]`` (a leading ``v`` is ignored). If that section
does not exist it prints ``## [Unreleased]`` instead and warns on stderr, so a tag
pushed before the heading was renamed still gets its notes. If neither exists it
prints nothing: the release then carries only GitHub's generated notes. It never
fails the release, which runs after PyPI publication.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

HEADING = re.compile(r"^## \[(?P<name>[^\]]+)\]")
LINK_DEFINITION = re.compile(r"^\[[^\]]+\]:\s")


def section(text: str, name: str) -> Optional[str]:
    """The lines under ``## [name]`` up to the next level-2 heading, or None."""
    collected: list[str] = []
    inside = False
    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            if inside:
                break
            inside = match.group("name").lower() == name.lower()
            continue
        if inside and not LINK_DEFINITION.match(line):
            collected.append(line)
    return "\n".join(collected).strip() if inside else None


def release_notes(text: str, version: str) -> str:
    version = version.removeprefix("v")
    notes = section(text, version)
    if notes is None:
        print(
            f"CHANGELOG.md has no [{version}] section; using [Unreleased].",
            file=sys.stderr,
        )
        notes = section(text, "Unreleased") or ""
    return notes


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(argv[2]) if len(argv) == 3 else Path("CHANGELOG.md")
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    print(release_notes(text, argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

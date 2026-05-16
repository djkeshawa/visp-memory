#!/usr/bin/env python3
"""Validate release workflow ownership rules that are easy to regress locally."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def text(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"Missing workflow: {path.relative_to(ROOT)}")
    return path.read_text()


def has_tag_push_trigger(content: str) -> bool:
    in_on = False
    in_push = False

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        is_top_level = not line.startswith((" ", "\t"))
        if stripped == "on:":
            in_on = True
            in_push = False
            continue

        if in_on and is_top_level:
            break

        if not in_on:
            continue

        if line.startswith("  ") and not line.startswith("    "):
            in_push = stripped == "push:"
            continue

        if in_push and line.startswith("    ") and stripped == "tags:":
            return True

    return False


def has_release_action(content: str) -> bool:
    return "softprops/action-gh-release" in content or "gh release create" in content


def main() -> int:
    release_workflow = WORKFLOWS / "build-release.yml"
    simple_workflow = WORKFLOWS / "release-simple.yml"

    release_text = text(release_workflow)
    simple_text = text(simple_workflow)

    if not has_tag_push_trigger(release_text):
        raise SystemExit("build-release.yml must own tag-triggered release publishing")

    if has_tag_push_trigger(simple_text):
        raise SystemExit("release-simple.yml must not also publish on tag push")

    tag_release_publishers = [
        path.name
        for path in WORKFLOWS.glob("*.yml")
        if has_tag_push_trigger(text(path)) and has_release_action(text(path))
    ]
    if tag_release_publishers != ["build-release.yml"]:
        raise SystemExit(
            "Exactly one tag-triggered release publisher is allowed; found "
            f"{tag_release_publishers}"
        )

    print("GitHub workflow release ownership check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The release body is the tag's CHANGELOG section, or Unreleased as a fallback."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "changelog_section.py"

CHANGELOG = """# Changelog

Intro text.

## [Unreleased]

### Upgrading

- Do the new thing.

## [1.2.0] - 2026-01-01

### Fixed

- A bug.

## [1.1.0] - 2025-12-01

- Older.

[Unreleased]: https://example.test/compare/v1.2.0...develop
[1.2.0]: https://example.test/compare/v1.1.0...v1.2.0
"""


def _run(tmp_path: Path, version: str, text: str = CHANGELOG) -> subprocess.CompletedProcess:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), version, str(changelog)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_tag_section_is_printed_without_the_heading(tmp_path):
    result = _run(tmp_path, "v1.2.0")
    assert result.stdout.strip() == "### Fixed\n\n- A bug."
    assert result.stderr == ""


def test_last_section_drops_link_definitions(tmp_path):
    assert _run(tmp_path, "1.1.0").stdout.strip() == "- Older."


def test_missing_version_falls_back_to_unreleased_with_a_warning(tmp_path):
    result = _run(tmp_path, "v9.9.9")
    assert result.stdout.strip() == "### Upgrading\n\n- Do the new thing."
    assert "no [9.9.9] section" in result.stderr


@pytest.mark.parametrize("text", ["# Changelog\n", ""])
def test_nothing_to_publish_still_succeeds(tmp_path, text):
    assert _run(tmp_path, "v1.0.0", text).stdout.strip() == ""


def test_repository_changelog_keeps_an_unreleased_section():
    changelog = ROOT / "CHANGELOG.md"
    if not changelog.is_file():
        pytest.skip("CHANGELOG.md is not part of this checkout")
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        from changelog_section import section
    finally:
        sys.path.remove(str(SCRIPT.parent))
    unreleased = section(changelog.read_text(encoding="utf-8"), "Unreleased")
    assert unreleased is not None

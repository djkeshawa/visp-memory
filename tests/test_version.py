"""Guards against version and distribution-name drift.

Both failures here are silent: `importlib.metadata.version()` on a name that is not
installed raises PackageNotFoundError, which the package catches and answers with a
hardcoded fallback. So a stale distribution name does not crash -- it just reports the
wrong version forever. That is what happened after the visp-memory rename.

pyproject is parsed with a regex rather than tomllib, which is 3.11+; this project
supports 3.10, and the two fields needed are simple top-level strings.
"""

import re
from pathlib import Path

import visp_memory

ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
_INIT = (ROOT / "src" / "visp_memory" / "__init__.py").read_text(encoding="utf-8")


def _project_field(field: str) -> str:
    match = re.search(rf'^{field} = "([^"]+)"', _PYPROJECT, re.MULTILINE)
    assert match, f"could not find {field} in pyproject.toml"
    return match.group(1)


def test_reported_version_matches_pyproject():
    assert visp_memory.__version__ == _project_field("version")


def test_fallback_version_matches_pyproject():
    """The fallback is used from a source checkout, so it must not drift either."""
    fallback = re.search(r'_FALLBACK_VERSION = "([^"]+)"', _INIT)
    assert fallback, "expected a _FALLBACK_VERSION constant"
    assert fallback.group(1) == _project_field("version")


def test_metadata_lookup_uses_the_real_distribution_name():
    looked_up = re.search(r'version\("([^"]+)"\)', _INIT)
    assert looked_up, "expected an importlib.metadata version() lookup"
    assert looked_up.group(1) == _project_field("name")

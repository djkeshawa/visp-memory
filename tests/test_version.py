"""Guards against version and distribution-name drift.

Both failures here are silent: `importlib.metadata.version()` on a name that is not
installed raises PackageNotFoundError, which the package catches and answers with a
hardcoded fallback. So a stale distribution name does not crash -- it just reports the
wrong version forever. That is what happened after the visp-memory rename.
"""

import re
from pathlib import Path

import tomllib

import visp_memory

ROOT = Path(__file__).resolve().parents[1]


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_reported_version_matches_pyproject():
    assert visp_memory.__version__ == _pyproject()["project"]["version"]


def test_fallback_version_matches_pyproject():
    """The fallback is used from a source checkout, so it must not drift either."""
    source = (ROOT / "src" / "visp_memory" / "__init__.py").read_text(encoding="utf-8")
    fallback = re.search(r'_FALLBACK_VERSION = "([^"]+)"', source)
    assert fallback, "expected a _FALLBACK_VERSION constant"
    assert fallback.group(1) == _pyproject()["project"]["version"]


def test_metadata_lookup_uses_the_real_distribution_name():
    source = (ROOT / "src" / "visp_memory" / "__init__.py").read_text(encoding="utf-8")
    looked_up = re.search(r'version\("([^"]+)"\)', source)
    assert looked_up, "expected an importlib.metadata version() lookup"
    assert looked_up.group(1) == _pyproject()["project"]["name"]

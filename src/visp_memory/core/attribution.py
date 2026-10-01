"""Informational attribution for the process or session that wrote a record."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Mapping

from visp_memory.core.json_text import to_json_text

WRITTEN_BY_KEY = "written_by"
AGENT_HEADER = "X-Visp-Agent"
SESSION_HEADER = "X-Visp-Session"
CLIENT_HEADER = "X-Visp-Client"

_LABEL_RE = re.compile(r"[A-Za-z0-9._:@/+\-]{1,64}\Z")


class _WriterBinding(Enum):
    NO_WRITER = "no_writer"


_BOUND_WRITER: ContextVar[WriterIdentity | _WriterBinding | None] = ContextVar(
    "visp_memory_writer", default=None
)
_ATTRIBUTION_SUPPRESSED: ContextVar[bool] = ContextVar(
    "visp_memory_attribution_suppressed", default=False
)


@dataclass(frozen=True)
class WriterIdentity:
    """Names supplied by a client or process, with no authority semantics."""

    agent: str | None = None
    session: str | None = None
    client: str | None = None

    def to_metadata(self) -> dict[str, str] | None:
        """Return populated fields in the persisted metadata shape."""
        fields = {
            key: value
            for key, value in (
                ("agent", self.agent),
                ("session", self.session),
                ("client", self.client),
            )
            if value not in (None, "")
        }
        return fields or None


def sanitize_label(value: object) -> str | None:
    """Keep short identifier-like labels and drop everything else."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _LABEL_RE.fullmatch(value) else None


def _sanitize_identity(identity: WriterIdentity | None) -> WriterIdentity | None:
    if identity is None:
        return None
    clean = WriterIdentity(
        agent=sanitize_label(identity.agent),
        session=sanitize_label(identity.session),
        client=sanitize_label(identity.client),
    )
    return clean if clean.to_metadata() else None


@contextmanager
def bind_writer(identity: WriterIdentity | None) -> Iterator[None]:
    """Bind a sanitized identity, explicitly excluding env fallback when absent."""
    token = _BOUND_WRITER.set(_sanitize_identity(identity) or _WriterBinding.NO_WRITER)
    try:
        yield
    finally:
        _BOUND_WRITER.reset(token)


@contextmanager
def suppress_attribution() -> Iterator[None]:
    """Let imported records keep the identity already stored in their metadata."""
    token = _ATTRIBUTION_SUPPRESSED.set(True)
    try:
        yield
    finally:
        _ATTRIBUTION_SUPPRESSED.reset(token)


def current_writer() -> WriterIdentity | None:
    """Return the bound writer or the process identity configured in the env."""
    bound = _BOUND_WRITER.get()
    if bound is _WriterBinding.NO_WRITER:
        return None
    if bound is not None:
        return bound
    identity = WriterIdentity(
        agent=sanitize_label(os.environ.get("VISP_MEMORY_AGENT")),
        session=sanitize_label(os.environ.get("VISP_MEMORY_SESSION")),
    )
    return identity if identity.to_metadata() else None


def stamp_written_by(metadata: dict | None) -> dict:
    """Copy metadata and apply the current writer unless attribution is suppressed."""
    result = dict(metadata or {})
    if _ATTRIBUTION_SUPPRESSED.get():
        return result
    identity = current_writer()
    writer_metadata = identity.to_metadata() if identity else None
    if writer_metadata:
        result[WRITTEN_BY_KEY] = writer_metadata
    return result


def without_written_by(metadata: Mapping[str, object] | None) -> dict:
    """Copy metadata minus the informational writer stamp.

    Attribution says who wrote a record first; it is not part of what the record *is*.
    Anything that decides "is this the same write?" must compare through this.
    """
    return {key: value for key, value in (metadata or {}).items() if key != WRITTEN_BY_KEY}


def _as_metadata_dict(value: object) -> object:
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value) if value else {}
        except ValueError:
            return value
    return value


def _canonical_json(metadata: Mapping[str, object] | None) -> str | None:
    """The persisted JSON value of metadata minus attribution, or None if unstorable.

    Python equality is the wrong test for "same write": ``True == 1 == 1.0`` although
    JSON stores ``true``, ``1`` and ``1.0``, while ``(1, 2) != [1, 2]`` and
    ``{1: "x"} != {"1": "x"}`` although JSON stores them identically. So encode with
    the storage serializer, read it back as the store would, and re-encode with
    sorted keys: key order is not part of what a record is.
    """
    try:
        persisted = json.loads(to_json_text(without_written_by(metadata)))
    except (TypeError, ValueError):
        return None
    return json.dumps(persisted, sort_keys=True)


def metadata_matches(stored: object, incoming: object) -> bool:
    """Compare stored and incoming metadata for an idempotent retry, ignoring attribution.

    Either side may be a dict, ``None`` or a JSON string (SQLite and Neo4j persist text).
    The stored record keeps the first writer's ``written_by``; a retry never replaces it.
    Metadata that cannot be serialized could never have been stored, so it matches nothing.
    """
    stored, incoming = _as_metadata_dict(stored), _as_metadata_dict(incoming)
    if not (isinstance(stored, Mapping) or stored is None) or not (
        isinstance(incoming, Mapping) or incoming is None
    ):
        return stored == incoming
    stored_json = _canonical_json(stored)
    return stored_json is not None and stored_json == _canonical_json(incoming)


def identity_headers(identity: WriterIdentity | None) -> dict[str, str]:
    """Convert populated identity fields to transport headers."""
    if identity is None:
        return {}
    headers = {}
    for key, value in (
        (AGENT_HEADER, identity.agent),
        (SESSION_HEADER, identity.session),
        (CLIENT_HEADER, identity.client),
    ):
        label = sanitize_label(value)
        if label:
            headers[key] = label
    return headers


def identity_from_headers(headers: Mapping[str, object]) -> WriterIdentity | None:
    """Build an identity from case-insensitive headers, dropping invalid labels."""
    lowered = {str(key).lower(): value for key, value in headers.items()}
    identity = WriterIdentity(
        agent=sanitize_label(lowered.get(AGENT_HEADER.lower())),
        session=sanitize_label(lowered.get(SESSION_HEADER.lower())),
        client=sanitize_label(lowered.get(CLIENT_HEADER.lower())),
    )
    return identity if identity.to_metadata() else None

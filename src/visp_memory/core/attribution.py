"""Informational attribution for the process or session that wrote a record."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Mapping

WRITTEN_BY_KEY = "written_by"
AGENT_HEADER = "X-Visp-Agent"
SESSION_HEADER = "X-Visp-Session"
CLIENT_HEADER = "X-Visp-Client"

_LABEL_RE = re.compile(r"[A-Za-z0-9._:@/+\-]{1,64}\Z")
_BOUND_WRITER: ContextVar[WriterIdentity | None] = ContextVar(
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
    """Bind one sanitized writer identity until this context exits."""
    token = _BOUND_WRITER.set(_sanitize_identity(identity))
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

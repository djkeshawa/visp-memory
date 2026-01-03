"""Detect and redact credentials before they are written to memory.

A memory store is an unusually good place for a secret to end up and an unusually bad
place for one to live. Content arrives from commit messages, stack traces, captured
conversations, test output, and instruction files -- all of which routinely contain
tokens someone pasted once. Unlike a log file, memory is *designed* to be retrieved and
replayed into a model's context later, so a secret stored here is a secret that will be
read aloud repeatedly.

The project already documented this risk in ``docs/development/MEMORY_GOVERNANCE.md``
("Do not record API keys, credentials, tokens..."), but documentation is advice, not
enforcement. This module is the enforcement.

**Redact, do not reject.** Refusing the whole memory loses the surrounding fact, which
is usually the part worth keeping. Rewriting

    "deploy fails unless AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

into

    "deploy fails unless AWS_SECRET_ACCESS_KEY=[REDACTED:aws-secret-key]"

keeps the engineering fact and drops the credential. The finding is recorded on the
memory so ``visp-memory audit`` can show that redaction happened.

**Bias towards precision.** A false positive silently corrupts a legitimate memory, and
the user may never notice. Patterns here are anchored to real credential formats rather
than generic high-entropy strings, and obvious placeholders are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Values that look like credentials but are documentation, examples, or indirection.
# Matching these would corrupt legitimate content -- the exact failure this module must
# not cause.
_PLACEHOLDER_RE = re.compile(
    r"^(?:"
    r"x{3,}|\*{3,}|\.{3,}|"
    r"<[^>]*>|\$\{[^}]*\}|\$[A-Z_][A-Z0-9_]*|"
    r"(?:your|my|the)[-_]?\w*|"
    r"change[-_]?me|replace[-_]?me|example|sample|dummy|fake|placeholder|"
    r"redacted|hidden|secret|password|token|apikey|api[-_]key|none|null|true|false"
    r")$",
    re.IGNORECASE,
)

# Code that *references* a secret rather than containing one. "api_key=os.getenv('X')"
# is a safe line and must survive untouched.
_INDIRECTION_RE = re.compile(
    r"^(?:os\.|process\.|config\.|settings\.|self\.|env\[|getenv|environ)", re.IGNORECASE
)


@dataclass(frozen=True)
class SecretPattern:
    """One credential shape."""

    kind: str
    regex: re.Pattern[str]
    # Which capture group holds the secret itself. 0 means the whole match.
    group: int = 0


# Anchored to published credential formats. Ordered most specific first so a token that
# matches several patterns is labelled with the most informative one.
PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern("private-key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    SecretPattern("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    SecretPattern("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    SecretPattern("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    SecretPattern("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    SecretPattern("stripe-key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{20,}\b")),
    SecretPattern(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    # Credentials embedded in a connection string: scheme://user:secret@host
    SecretPattern(
        "connection-string-password",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:([^\s@/]{3,})@"),
        group=1,
    ),
    SecretPattern(
        "bearer-token",
        re.compile(r"\bBearer\s+([A-Za-z0-9._~+/-]{20,}=*)", re.IGNORECASE),
        group=1,
    ),
    # Generic assignment. Last, and deliberately demanding: a quoted or unbroken value of
    # at least 8 characters. Anything shorter is far more often prose than a credential.
    SecretPattern(
        "credential-assignment",
        re.compile(
            r"\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
            r"auth[_-]?token|client[_-]?secret)\b\s*[=:]\s*"
            r"[\"']?([^\s\"',;)]{8,})[\"']?",
            re.IGNORECASE,
        ),
        group=1,
    ),
)


@dataclass
class RedactionResult:
    """Outcome of scanning one piece of content."""

    text: str
    findings: list[str] = field(default_factory=list)

    @property
    def redacted(self) -> bool:
        return bool(self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {"redacted": self.redacted, "kinds": sorted(set(self.findings))}


def _is_benign(value: str) -> bool:
    """Report whether a candidate is a placeholder or an indirection rather than a secret."""
    stripped = value.strip().strip("\"'")
    if not stripped or len(stripped) < 8:
        return True
    if _PLACEHOLDER_RE.match(stripped):
        return True
    if _INDIRECTION_RE.match(stripped):
        return True
    # A value made of a single repeated character carries no secret.
    return len(set(stripped)) <= 2


def scan(text: str) -> list[str]:
    """Return the kinds of credential found in ``text``, without modifying it."""
    return redact(text).findings


def redact(text: str, *, marker: str = "[REDACTED:{kind}]") -> RedactionResult:
    """Replace credentials in ``text`` with a labelled marker.

    The surrounding content is preserved: the goal is to keep the engineering fact and
    drop only the credential.
    """
    if not text or not isinstance(text, str):
        return RedactionResult(text=text, findings=[])

    findings: list[str] = []
    result = text

    for pattern in PATTERNS:
        def _replace(match: re.Match[str], _kind: str = pattern.kind, _p=pattern) -> str:
            secret = match.group(_p.group)
            if _is_benign(secret):
                return match.group(0)
            findings.append(_kind)
            replacement = marker.format(kind=_kind)
            if _p.group == 0:
                return replacement
            # Keep everything around the captured group, e.g. the "password=" prefix and
            # the "@host" suffix, so the sentence still reads correctly.
            whole = match.group(0)
            start, end = match.span(_p.group)
            offset = match.start()
            return whole[: start - offset] + replacement + whole[end - offset :]

        result = pattern.regex.sub(_replace, result)

    return RedactionResult(text=result, findings=findings)


REDACTED_FLAG = "secret_redacted"


def redact_for_storage(
    content: str,
    quality_flags: list[str] | None,
) -> tuple[str, list[str] | None]:
    """Redact ``content`` and record the fact on the memory's quality flags.

    Called from every storage backend's ``store_memory``, which is the one choke point
    all ten write paths (layers, capture, compression, reflection, import, the REST API)
    funnel through. Enforcing here rather than at each caller means a new write path
    cannot silently opt out.

    Flags are ``secret_redacted`` plus ``secret_redacted:<kind>`` so ``visp-memory audit``
    can show both that redaction happened and what was found.
    """
    outcome = redact(content)
    if not outcome.redacted:
        return content, quality_flags

    flags = list(quality_flags or [])
    if REDACTED_FLAG not in flags:
        flags.append(REDACTED_FLAG)
    for kind in sorted(set(outcome.findings)):
        tag = f"{REDACTED_FLAG}:{kind}"
        if tag not in flags:
            flags.append(tag)
    return outcome.text, flags


def redact_memory_fields(
    payload: dict[str, Any],
    fields: Iterable[str] = ("content", "event", "summary", "text"),
) -> tuple[dict[str, Any], list[str]]:
    """Redact known free-text fields of a memory payload.

    Returns the payload (a copy when anything changed) and the findings. Structured
    metadata is deliberately left alone: it is written by this project's own code paths,
    not by captured third-party text, and rewriting it risks breaking consumers.
    """
    findings: list[str] = []
    updated: dict[str, Any] | None = None

    for name in fields:
        value = payload.get(name)
        if not isinstance(value, str) or not value:
            continue
        outcome = redact(value)
        if outcome.redacted:
            if updated is None:
                updated = dict(payload)
            updated[name] = outcome.text
            findings.extend(outcome.findings)

    return (updated if updated is not None else payload), findings

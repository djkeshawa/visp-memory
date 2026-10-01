"""Which scopes a personal access token needs for each route.

One ordered table, first match wins, so a route's requirement is a decision that
can be read and tested in one place. The first rules are the specific ones: a route
that returns memory content or the whole graph must not inherit a broader prefix
that is cheaper to hold (``/repos/...`` is only ``project:read``).

A path that matches no rule is still allowed with ``project:read`` -- that default
is kept for compatibility -- but is reported as not explicit, and a test fails for
any registered route that depends on it, so a new route has to be classified here.
"""

import re
from dataclasses import dataclass
from typing import Optional

OPEN = "open"
SCOPES = "scopes"
ADMIN = "admin"
DENIED = "denied"

AUTH_DENIED = "Dashboard session authentication is required for account and token management"
IMPORT_DENIED = (
    "A token cannot import a graph export: it carries historical trust tiers, so "
    "it needs a dashboard administrator session or the local owner token"
)


@dataclass(frozen=True)
class PatDecision:
    kind: str
    scopes: tuple[str, ...] = ()
    detail: Optional[str] = None
    explicit: bool = True


@dataclass(frozen=True)
class _Rule:
    pattern: "re.Pattern[str]"
    read: PatDecision
    write: PatDecision


def _scopes(*names: str) -> PatDecision:
    return PatDecision(SCOPES, names)


_OPEN = PatDecision(OPEN)
_ADMIN = PatDecision(ADMIN)
_DEFAULT = PatDecision(SCOPES, ("project:read",), explicit=False)


def _rule(pattern: str, read: PatDecision, write: PatDecision) -> _Rule:
    return _Rule(re.compile(pattern), read, write)


def _any_method(pattern: str, decision: PatDecision) -> _Rule:
    # One decision for every method. Only for routes whose POSTs merely read (a
    # search body, a recall query, a context compile) and for decisions that are
    # the same whatever the method (open probes, denials). A GET route must use
    # _rule with a write decision: a non-GET on its path may route elsewhere
    # (PATCH /intents/usage is PATCH /intents/{intent_id}).
    return _Rule(re.compile(pattern), decision, decision)


def _prefix(*names: str) -> str:
    return r"/(?:" + "|".join(names) + r")(?:[/-].*)?"


_AUTH_DENIED = PatDecision(DENIED, detail=AUTH_DENIED)
_READ_METHODS = frozenset({"GET", "HEAD"})

_RULES = (
    _rule(r"/auth/me", _OPEN, _AUTH_DENIED),
    _any_method(_prefix("auth"), _AUTH_DENIED),
    # Owner-or-admin already, but a token must never reach it whatever it holds.
    _any_method(r"/repos/.*/import", PatDecision(DENIED, detail=IMPORT_DENIED)),
    # The export is the whole memory, evidence and intent graph.
    _rule(
        r"/repos/.*/export",
        _scopes("project:read", "memory:read", "intent:read"),
        _scopes("project:write"),
    ),
    _rule(r"/memories/[^/]+/attestation", _scopes("memory:read"), _scopes("memory:write")),
    # A POST, but it only searches: it reads memory content.
    _any_method(r"/turn-keys/search", _scopes("memory:read")),
    _rule(_prefix("recall-events"), _scopes("memory:read"), _scopes("memory:write")),
    _rule(r"/intents/usage", _scopes("intent:read"), _scopes("intent:write")),
    # Non-secret backend capabilities, which `connect` reads with any token.
    _rule(r"/diagnostics/capabilities", _scopes("project:read"), _ADMIN),
    # Owner-or-admin in the route itself; a token can only ever be the admin half.
    _any_method(_prefix("platform", "teams", "diagnostics", "dreaming", "maintenance"), _ADMIN),
    _rule(_prefix("intents"), _scopes("intent:read"), _scopes("intent:write")),
    _rule(_prefix("repos", "sessions"), _scopes("project:read"), _scopes("project:write")),
    # POST /recall and POST /context/{compile,brief} only read memory.
    _any_method(r"/recall", _scopes("memory:read")),
    _any_method(_prefix("context"), _scopes("memory:read")),
    _rule(
        _prefix("memories", "evidence", "recall", "graph", "relationships", "quality", "ai"),
        _scopes("memory:read"),
        _scopes("memory:write"),
    ),
    _rule(r"/|/status", _scopes("project:read"), _scopes("project:write")),
    # Unauthenticated probes: they never look at the caller.
    _any_method(r"/healthz|/readyz", _OPEN),
    # The exported dashboard pages are static files served before any
    # authentication; the data they show comes from the API routes above.
    _any_method(_prefix("dashboard"), _OPEN),
)


def pat_scope_decision(method: str, path: str) -> PatDecision:
    """The scope decision for one request; ``explicit`` is False for the default."""
    for rule in _RULES:
        if rule.pattern.fullmatch(path):
            return rule.read if method.upper() in _READ_METHODS else rule.write
    return _DEFAULT

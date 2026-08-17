"""
Authentication and Authorization for Visp Memory Server.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from visp_memory.config import load_config

# Security schemes
security = HTTPBearer(auto_error=False)
SESSION_COOKIE_NAME = "visp_memory_session"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


#: Peer addresses that identify a request as having come from this machine.
LOOPBACK_CLIENT_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


class UserContext(BaseModel):
    """Authenticated user context."""

    user_id: str
    username: str
    team_id: Optional[str] = None
    is_admin: bool = False
    auth_type: str = "legacy"
    scopes: list[str] = Field(default_factory=lambda: ["*"])
    repo_ids: list[str] = Field(default_factory=list)

    #: This request came from the machine holding the store, in open local mode.
    #: It reads the store as its owner -- the same view `visp-memory recall` has
    #: of the same file -- and nothing more: it is not an admin, so every
    #: administrative surface refuses it exactly as it refuses any other
    #: non-admin. Never set from a token, a session, or a network request.
    is_local_owner: bool = False

    def allows(self, scope: str) -> bool:
        return self.is_admin or "*" in self.scopes or scope in self.scopes


def is_local_owner_request(request: Request, config) -> bool:
    """Whether this request is the local owner reading its own store.

    Two independent conditions, both required. ``local_owner_mode`` is an explicit
    statement that this is a single-user store, and `visp-memory serve` sets it
    only when it starts open local mode on a loopback bind. The peer address is
    then checked on every request, so setting the flag by hand and binding a
    public interface grants a remote caller nothing.

    Deliberately not keyed on ``allow_anonymous``: that is an authentication
    setting, it is settable from the config file and the environment as well as by
    the loopback path, and it says nothing about whether the store has one owner
    or many tenants.

    Known limit, stated rather than hidden: a reverse proxy on this same machine
    presents a loopback peer, so a deployment that both sets the mode by hand and
    fronts the server with a local proxy would extend the local view to whoever
    reaches that proxy. `serve` never sets the mode on such a bind.
    """
    if not getattr(config.server, "local_owner_mode", False):
        return False
    client = getattr(request, "client", None)
    return client is not None and client.host in LOOPBACK_CLIENT_HOSTS


def _authorize_pat_request(request: Request, user: UserContext) -> UserContext:
    """Apply least-privilege scopes to personal access tokens."""
    if user.auth_type != "pat":
        return user
    path = request.url.path
    if path == "/auth/me":
        return user
    if path.startswith("/auth/") and path != "/auth/me":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dashboard session authentication is required for account and token management",
        )
    if path.startswith(("/platform", "/teams", "/diagnostics")):
        required_scope = "admin"
    elif path.startswith("/intents"):
        required_scope = "intent:read" if request.method == "GET" else "intent:write"
    elif path.startswith("/repos"):
        required_scope = "project:read" if request.method == "GET" else "project:write"
    elif path.startswith("/context"):
        required_scope = "memory:read"
    elif path.startswith(
        ("/memories", "/evidence", "/recall", "/graph", "/quality", "/ai")
    ):
        required_scope = "memory:read" if request.method == "GET" else "memory:write"
    else:
        required_scope = "project:read"
    if required_scope == "admin":
        allowed = user.is_admin and ("admin" in user.scopes or "*" in user.scopes)
    else:
        allowed = "*" in user.scopes or required_scope in user.scopes
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Token is missing required scope: {required_scope}",
        )
    return user


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create a new JWT access token."""
    config = load_config()
    if not config.server.jwt_secret:
        raise ValueError("VISP_MEMORY_JWT_SECRET must be configured to create JWT tokens")

    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(hours=config.server.jwt_expiry_hours)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, config.server.jwt_secret, algorithm=config.server.jwt_algorithm
    )
    return encoded_jwt


async def get_current_user(
    request: Request, auth: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> UserContext:
    """
    FastAPI dependency to get the current authenticated user.
    Supports both JWT and API key authentication.
    """
    config = load_config()

    if not config.server.auth_enabled:
        return UserContext(
            user_id="local",
            username="local",
            team_id=config.server.default_team,
            is_admin=True,
        )

    local_owner = is_local_owner_request(request, config)

    # 1. Check for API key in header (backward compatibility)
    api_key = request.headers.get("X-API-KEY")
    if api_key:
        if any(secrets.compare_digest(api_key, candidate) for candidate in config.server.api_keys):
            return UserContext(
                user_id="api_key_user", username="api_key", is_admin=True, auth_type="api_key"
            )
        # Also check storage config api key
        if config.storage.api_key and secrets.compare_digest(api_key, config.storage.api_key):
            return UserContext(
                user_id="default_admin", username="admin", is_admin=True, auth_type="api_key"
            )

    # 2. Check for JWT in Bearer token
    if auth:
        token = auth.credentials
        app_state = getattr(getattr(request, "app", None), "state", None)
        auth_store = getattr(app_state, "auth_store", None)
        if token.startswith("llmm_") and auth_store is not None:
            principal = auth_store.authenticate_token(token)
            if not principal:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired personal access token",
                )
            return _authorize_pat_request(
                request, UserContext(**principal, auth_type="pat")
            )

        if not config.server.jwt_secret:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT authentication is not configured",
            )

        try:
            payload = jwt.decode(
                token, config.server.jwt_secret, algorithms=[config.server.jwt_algorithm]
            )
            user_id: str = payload.get("sub")
            if user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication token",
                )

            return _authorize_pat_request(request, UserContext(
                user_id=user_id,
                username=payload.get("username", user_id),
                team_id=payload.get("team_id"),
                is_admin=payload.get("is_admin", False),
                auth_type="jwt",
                scopes=payload.get("scopes", ["*"]),
                repo_ids=payload.get("repo_ids", []),
            ))
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )

    # 3. Browser session cookie.
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    app_state = getattr(getattr(request, "app", None), "state", None)
    auth_store = getattr(app_state, "auth_store", None)
    if session_token and auth_store is not None:
        account = auth_store.authenticate_session(
            session_token, idle_hours=config.server.session_idle_hours
        )
        if account:
            if request.method.upper() in UNSAFE_METHODS:
                supplied_csrf = request.headers.get("X-CSRF-Token", "")
                if not supplied_csrf or not secrets.compare_digest(
                    supplied_csrf, account["csrf_token"]
                ):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Missing or invalid CSRF token",
                    )
            return _authorize_pat_request(request, UserContext(
                user_id=account["id"],
                username=account["username"],
                team_id=account.get("team_id"),
                is_admin=account.get("role") == "admin",
                auth_type="session",
            ))

    # 4. Allow anonymous if configured
    if config.server.allow_anonymous:
        return UserContext(
            user_id="anonymous",
            username="anonymous",
            team_id=config.server.default_team,
            is_local_owner=local_owner,
        )

    # 5. Fail if no auth
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )

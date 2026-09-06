"""Username/password sessions and personal access token endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from visp_memory.config import load_config
from visp_memory.core.clock import parse_utc, utc_now
from visp_memory.server.auth import SESSION_COOKIE_NAME, UserContext, get_current_user
from visp_memory.server.authorization import require_admin
from visp_memory.server.routers.platform import append_audit_event

router = APIRouter(prefix="/auth", tags=["authentication"])

ALLOWED_TOKEN_SCOPES = {
    "memory:read",
    "memory:write",
    "intent:read",
    "intent:write",
    "project:read",
    "project:write",
    "admin",
}
DEFAULT_TOKEN_SCOPES = [
    "memory:read",
    "memory:write",
    "intent:read",
    "intent:write",
    "project:read",
]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class InitialAccountRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=1024)
    setup_token: str = Field(min_length=20, max_length=200)


class AccountCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=1024)
    email: Optional[str] = None
    display_name: Optional[str] = None
    role: Literal["admin", "user"] = "user"
    team_id: Optional[str] = None


class AccountUpdateRequest(BaseModel):
    email: Optional[str] = None
    display_name: Optional[str] = None
    role: Optional[Literal["admin", "user"]] = None
    team_id: Optional[str] = None
    enabled: Optional[bool] = None


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=12, max_length=1024)


class TokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_TOKEN_SCOPES))
    repo_ids: list[str] = Field(default_factory=list)
    expires_at: Optional[datetime] = None


def _public_account(account: dict) -> dict:
    return {key: value for key, value in account.items() if key != "csrf_token"}


@router.get("/status")
async def authentication_status(request: Request):
    """Return enough public state to render login or first-run guidance."""
    config = load_config()
    return {
        "auth_enabled": config.server.auth_enabled,
        "setup_required": (
            config.server.auth_enabled and not request.app.state.auth_store.has_accounts()
        ),
    }


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup_account(request: Request, payload: InitialAccountRequest):
    """Create exactly one administrator using the code printed to the server console."""
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Setup must be submitted from this server's dashboard")
    store = request.app.state.auth_store
    if store.has_accounts():
        raise HTTPException(409, "Administrator setup has already been completed")
    try:
        account = store.create_account(
            username=payload.username, password=payload.password, role="admin",
            _setup_token=payload.setup_token,
        )
    except ValueError as error:
        raise HTTPException(409 if store.has_accounts() else 403, str(error)) from error
    append_audit_event(request.app.state.storage, event_type="auth.setup_completed",
                       actor_id=account["id"], target_type="user", target_id=account["id"])
    return _public_account(account)


@router.post("/login")
async def login(request: Request, response: Response, credentials: LoginRequest):
    config = load_config()
    account = request.app.state.auth_store.authenticate_password(
        credentials.username, credentials.password
    )
    if not account:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    session_token, csrf_token = request.app.state.auth_store.create_session(
        account["id"],
        idle_hours=config.server.session_idle_hours,
        max_days=config.server.session_max_days,
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        httponly=True,
        secure=config.server.session_cookie_secure,
        samesite="lax",
        max_age=config.server.session_max_days * 86400,
        path="/",
    )
    append_audit_event(
        request.app.state.storage,
        event_type="auth.login",
        actor_id=account["id"],
        target_type="user",
        target_id=account["id"],
    )
    return {"user": _public_account(account), "csrf_token": csrf_token}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    user: UserContext = Depends(get_current_user),
):
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        request.app.state.auth_store.revoke_session(session_token)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    append_audit_event(
        request.app.state.storage,
        event_type="auth.logout",
        actor_id=user.user_id,
        target_type="user",
        target_id=user.user_id,
    )
    return {"status": "logged_out"}


@router.get("/me")
async def current_account(request: Request, user: UserContext = Depends(get_current_user)):
    account = request.app.state.auth_store.get_account(user.user_id)
    csrf_token = None
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token and user.auth_type == "session":
        session = request.app.state.auth_store.authenticate_session(session_token)
        csrf_token = session.get("csrf_token") if session else None
    return {
        "user": _public_account(account)
        if account
        else {
            "id": user.user_id,
            "username": user.username,
            "role": "admin" if user.is_admin else "user",
            "team_id": user.team_id,
            "enabled": True,
        },
        "auth_type": user.auth_type,
        "scopes": user.scopes,
        "repo_ids": user.repo_ids,
        "csrf_token": csrf_token,
    }


@router.get("/users")
async def list_accounts(request: Request, user: UserContext = Depends(get_current_user)):
    require_admin(user)
    return [_public_account(account) for account in request.app.state.auth_store.list_accounts()]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_account(
    request: Request,
    payload: AccountCreateRequest,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    try:
        account = request.app.state.auth_store.create_account(**payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    append_audit_event(
        request.app.state.storage,
        event_type="auth.user_created",
        actor_id=user.user_id,
        target_type="user",
        target_id=account["id"],
        metadata={"role": account["role"]},
    )
    return _public_account(account)


@router.patch("/users/{user_id}")
async def update_account(
    request: Request,
    user_id: str,
    payload: AccountUpdateRequest,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    account = request.app.state.auth_store.update_account(
        user_id, **payload.model_dump(exclude_unset=True)
    )
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    append_audit_event(
        request.app.state.storage,
        event_type="auth.user_updated",
        actor_id=user.user_id,
        target_type="user",
        target_id=user_id,
        metadata={"fields": sorted(payload.model_fields_set)},
    )
    return _public_account(account)


@router.post("/users/{user_id}/password")
async def reset_password(
    request: Request,
    user_id: str,
    payload: PasswordResetRequest,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    try:
        changed = request.app.state.auth_store.set_password(user_id, payload.password)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    if not changed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    append_audit_event(
        request.app.state.storage,
        event_type="auth.password_reset",
        actor_id=user.user_id,
        target_type="user",
        target_id=user_id,
    )
    return {"status": "password_reset"}


@router.get("/tokens")
async def list_tokens(request: Request, user: UserContext = Depends(get_current_user)):
    return request.app.state.auth_store.list_tokens(user.user_id)


@router.post("/tokens", status_code=status.HTTP_201_CREATED)
async def create_token(
    request: Request,
    payload: TokenCreateRequest,
    user: UserContext = Depends(get_current_user),
):
    if not request.app.state.auth_store.get_account(user.user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A persistent account is required to create personal access tokens",
        )
    scopes = sorted(set(payload.scopes))
    invalid = set(scopes) - ALLOWED_TOKEN_SCOPES
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported token scopes: {', '.join(sorted(invalid))}",
        )
    if "admin" in scopes and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin scope is restricted",
        )
    if payload.expires_at and parse_utc(payload.expires_at) <= utc_now():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Expiry must be future")

    token_record, secret = request.app.state.auth_store.create_token(
        user_id=user.user_id,
        name=payload.name,
        scopes=scopes,
        repo_ids=payload.repo_ids,
        expires_at=payload.expires_at.isoformat() if payload.expires_at else None,
    )
    append_audit_event(
        request.app.state.storage,
        event_type="auth.token_created",
        actor_id=user.user_id,
        target_type="api_token",
        target_id=token_record["id"],
        metadata={"scopes": scopes, "repo_ids": payload.repo_ids},
    )
    return {**token_record, "token": secret}


@router.delete("/tokens/{token_id}")
async def revoke_token(
    request: Request,
    token_id: str,
    user: UserContext = Depends(get_current_user),
):
    if not request.app.state.auth_store.revoke_token(token_id, user_id=user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    append_audit_event(
        request.app.state.storage,
        event_type="auth.token_revoked",
        actor_id=user.user_id,
        target_type="api_token",
        target_id=token_id,
    )
    return {"status": "revoked", "id": token_id}

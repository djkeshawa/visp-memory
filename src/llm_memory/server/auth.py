"""
Authentication and Authorization for LLM Memory Server.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from llm_memory.config import load_config

# Security schemes
security = HTTPBearer(auto_error=False)


class UserContext(BaseModel):
    """Authenticated user context."""

    user_id: str
    username: str
    team_id: Optional[str] = None
    is_admin: bool = False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create a new JWT access token."""
    config = load_config()
    if not config.server.jwt_secret:
        raise ValueError("LLM_MEMORY_JWT_SECRET must be configured to create JWT tokens")

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

    # 1. Check for API key in header (backward compatibility)
    api_key = request.headers.get("X-API-KEY")
    if api_key:
        if any(secrets.compare_digest(api_key, candidate) for candidate in config.server.api_keys):
            return UserContext(user_id="api_key_user", username="api_key", is_admin=True)
        # Also check storage config api key
        if config.storage.api_key and secrets.compare_digest(api_key, config.storage.api_key):
            return UserContext(user_id="default_admin", username="admin", is_admin=True)

    # 2. Check for JWT in Bearer token
    if auth:
        if not config.server.jwt_secret:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT authentication is not configured",
            )

        token = auth.credentials
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

            return UserContext(
                user_id=user_id,
                username=payload.get("username", user_id),
                team_id=payload.get("team_id"),
                is_admin=payload.get("is_admin", False),
            )
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )

    # 3. Allow anonymous if configured
    if config.server.allow_anonymous:
        return UserContext(
            user_id="anonymous", username="anonymous", team_id=config.server.default_team
        )

    # 4. Fail if no auth
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )

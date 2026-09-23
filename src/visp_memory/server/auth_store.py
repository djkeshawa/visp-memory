"""Credential, browser-session, and personal-access-token persistence."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from visp_memory.core.clock import parse_utc, utc_now, utc_now_iso


class AuthStore:
    """Store authentication secrets separately from memory backend records.

    The server may use SQLite, Neo4j, ArcadeDB, or a remote memory backend. A
    small local credential database gives every deployment identical password,
    session, and token semantics without placing password hashes in graph data.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.passwords = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1)
        self._initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        self.path.chmod(0o600)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._db() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS auth_setup (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    token TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_accounts (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    email TEXT,
                    display_name TEXT,
                    role TEXT NOT NULL DEFAULT 'user',
                    team_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    password_changed_at TEXT NOT NULL,
                    last_login_at TEXT
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    csrf_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES auth_accounts(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS auth_tokens (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    token_prefix TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    scopes TEXT NOT NULL DEFAULT '[]',
                    repo_ids TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    last_used_at TEXT,
                    revoked_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES auth_accounts(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at);
                CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id);
                CREATE INDEX IF NOT EXISTS idx_auth_tokens_prefix ON auth_tokens(token_prefix);
                """
            )
            connection.commit()

    @staticmethod
    def _hash_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    @staticmethod
    def _account(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "username": row["username"],
            "email": row["email"],
            "display_name": row["display_name"],
            "role": row["role"],
            "team_id": row["team_id"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "password_changed_at": row["password_changed_at"],
            "last_login_at": row["last_login_at"],
        }

    def has_accounts(self) -> bool:
        with self._db() as connection:
            row = connection.execute("SELECT 1 FROM auth_accounts LIMIT 1").fetchone()
        return row is not None

    def get_setup_token(self) -> Optional[str]:
        """Return the local operator's one-use setup code; never expose it over HTTP."""
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM auth_accounts LIMIT 1").fetchone():
                connection.execute("DELETE FROM auth_setup")
                connection.commit()
                return None
            connection.execute(
                "INSERT OR IGNORE INTO auth_setup(id, token) VALUES (1, ?)",
                (secrets.token_urlsafe(32),),
            )
            token = connection.execute("SELECT token FROM auth_setup WHERE id = 1").fetchone()[0]
            connection.commit()
            return token

    def create_account(
        self,
        *,
        username: str,
        password: str,
        user_id: Optional[str] = None,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
        role: str = "user",
        team_id: Optional[str] = None,
        _setup_token: Optional[str] = None,
    ) -> dict[str, Any]:
        normalized = username.strip()
        if len(normalized) < 3 or len(normalized) > 64:
            raise ValueError("Username must be between 3 and 64 characters")
        if len(password) < 12:
            raise ValueError("Password must contain at least 12 characters")
        if role not in {"admin", "user"}:
            raise ValueError("Role must be admin or user")

        account_id = user_id or f"usr_{secrets.token_hex(8)}"
        now = utc_now_iso()
        try:
            with self._db() as connection:
                if _setup_token is not None:
                    connection.execute("BEGIN IMMEDIATE")
                    if connection.execute("SELECT 1 FROM auth_accounts LIMIT 1").fetchone():
                        raise ValueError("Administrator setup has already been completed")
                    token = connection.execute(
                        "SELECT token FROM auth_setup WHERE id = 1"
                    ).fetchone()
                    if not token or not secrets.compare_digest(token[0], _setup_token):
                        raise ValueError("Invalid setup code")
                connection.execute(
                    """
                    INSERT INTO auth_accounts (
                        id, username, password_hash, email, display_name, role,
                        team_id, enabled, created_at, password_changed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        account_id,
                        normalized,
                        self.passwords.hash(password),
                        email,
                        display_name,
                        role,
                        team_id,
                        now,
                        now,
                    ),
                )
                connection.execute("DELETE FROM auth_setup")
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise ValueError("Username or user ID already exists") from error
        return self.get_account(account_id)

    def bootstrap_admin(self, username: str, password: str) -> Optional[dict[str, Any]]:
        if self.has_accounts() or not password:
            return None
        return self.create_account(username=username or "admin", password=password, role="admin")

    def get_account(self, user_id: str) -> Optional[dict[str, Any]]:
        with self._db() as connection:
            row = connection.execute(
                "SELECT * FROM auth_accounts WHERE id = ?", (user_id,)
            ).fetchone()
        return self._account(row) if row else None

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM auth_accounts ORDER BY username COLLATE NOCASE"
            ).fetchall()
        return [self._account(row) for row in rows]

    def update_account(self, user_id: str, **changes: Any) -> Optional[dict[str, Any]]:
        allowed = {"email", "display_name", "role", "team_id", "enabled"}
        updates = {key: value for key, value in changes.items() if key in allowed}
        if "role" in updates and updates["role"] not in {"admin", "user"}:
            raise ValueError("Role must be admin or user")
        if "enabled" in updates:
            updates["enabled"] = 1 if updates["enabled"] else 0
        if not updates:
            return self.get_account(user_id)
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._db() as connection:
            result = connection.execute(
                f"UPDATE auth_accounts SET {assignments} WHERE id = ?",  # noqa: S608
                [*updates.values(), user_id],
            )
            connection.commit()
        return self.get_account(user_id) if result.rowcount else None

    def set_password(self, user_id: str, password: str) -> bool:
        if len(password) < 12:
            raise ValueError("Password must contain at least 12 characters")
        with self._db() as connection:
            result = connection.execute(
                """
                UPDATE auth_accounts SET password_hash = ?, password_changed_at = ?
                WHERE id = ?
                """,
                (self.passwords.hash(password), utc_now_iso(), user_id),
            )
            if result.rowcount:
                connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            connection.commit()
        return result.rowcount > 0

    def authenticate_password(self, username: str, password: str) -> Optional[dict[str, Any]]:
        with self._db() as connection:
            row = connection.execute(
                "SELECT * FROM auth_accounts WHERE username = ? COLLATE NOCASE", (username.strip(),)
            ).fetchone()
        if not row or not row["enabled"]:
            return None
        try:
            self.passwords.verify(row["password_hash"], password)
        except (VerifyMismatchError, InvalidHashError):
            return None

        now = utc_now_iso()
        replacement_hash = None
        if self.passwords.check_needs_rehash(row["password_hash"]):
            replacement_hash = self.passwords.hash(password)
        with self._db() as connection:
            if replacement_hash:
                connection.execute(
                    "UPDATE auth_accounts SET last_login_at = ?, password_hash = ? WHERE id = ?",
                    (now, replacement_hash, row["id"]),
                )
            else:
                connection.execute(
                    "UPDATE auth_accounts SET last_login_at = ? WHERE id = ?", (now, row["id"])
                )
            connection.commit()
        return self.get_account(row["id"])

    def create_session(
        self, user_id: str, *, idle_hours: int = 12, max_days: int = 7
    ) -> tuple[str, str]:
        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        now = utc_now()
        expires = now + timedelta(days=max(1, max_days))
        with self._db() as connection:
            connection.execute(
                """
                INSERT INTO auth_sessions (
                    token_hash, user_id, csrf_token, created_at, last_seen_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self._hash_secret(token),
                    user_id,
                    csrf_token,
                    now.isoformat(),
                    now.isoformat(),
                    expires.isoformat(),
                ),
            )
            connection.commit()
        return token, csrf_token

    def authenticate_session(self, token: str, *, idle_hours: int = 12) -> Optional[dict[str, Any]]:
        token_hash = self._hash_secret(token)
        with self._db() as connection:
            row = connection.execute(
                """
                SELECT s.*, a.* FROM auth_sessions s
                JOIN auth_accounts a ON a.id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
        if not row or not row["enabled"]:
            return None

        now = utc_now()
        expires_at = parse_utc(row["expires_at"])
        last_seen = parse_utc(row["last_seen_at"])
        if not expires_at or expires_at <= now or not last_seen:
            self.revoke_session(token)
            return None
        if now - last_seen > timedelta(hours=max(1, idle_hours)):
            self.revoke_session(token)
            return None

        with self._db() as connection:
            connection.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
                (now.isoformat(), token_hash),
            )
            connection.commit()
        account = self.get_account(row["user_id"])
        if account:
            account["csrf_token"] = row["csrf_token"]
        return account

    def revoke_session(self, token: str) -> None:
        with self._db() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?", (self._hash_secret(token),)
            )
            connection.commit()

    def create_token(
        self,
        *,
        user_id: str,
        name: str,
        scopes: list[str],
        repo_ids: list[str],
        expires_at: Optional[str] = None,
    ) -> tuple[dict[str, Any], str]:
        token_id = f"pat_{secrets.token_hex(8)}"
        public_prefix = secrets.token_hex(4)
        token = f"llmm_{public_prefix}_{secrets.token_urlsafe(36)}"
        created_at = utc_now_iso()
        with self._db() as connection:
            connection.execute(
                """
                INSERT INTO auth_tokens (
                    id, user_id, name, token_prefix, token_hash, scopes, repo_ids,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_id,
                    user_id,
                    name.strip(),
                    f"llmm_{public_prefix}",
                    self._hash_secret(token),
                    json.dumps(sorted(set(scopes))),
                    json.dumps(sorted(set(repo_ids))),
                    created_at,
                    expires_at,
                ),
            )
            connection.commit()
        return self.get_token(token_id, user_id=user_id), token

    def get_token(
        self, token_id: str, *, user_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        query = "SELECT * FROM auth_tokens WHERE id = ?"
        params: list[Any] = [token_id]
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        with self._db() as connection:
            row = connection.execute(query, params).fetchone()
        return self._token_row(row) if row else None

    @staticmethod
    def _token_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "name": row["name"],
            "token_prefix": row["token_prefix"],
            "scopes": json.loads(row["scopes"] or "[]"),
            "repo_ids": json.loads(row["repo_ids"] or "[]"),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "last_used_at": row["last_used_at"],
            "revoked_at": row["revoked_at"],
        }

    def list_tokens(self, user_id: str) -> list[dict[str, Any]]:
        with self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM auth_tokens WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
        return [self._token_row(row) for row in rows]

    def authenticate_token(self, token: str) -> Optional[dict[str, Any]]:
        if not token.startswith("llmm_"):
            return None
        with self._db() as connection:
            row = connection.execute(
                """
                SELECT t.*, a.username, a.team_id, a.role, a.enabled
                FROM auth_tokens t JOIN auth_accounts a ON a.id = t.user_id
                WHERE t.token_hash = ?
                """,
                (self._hash_secret(token),),
            ).fetchone()
        if not row or row["revoked_at"] or not row["enabled"]:
            return None
        expires_at = parse_utc(row["expires_at"])
        if expires_at and expires_at <= utc_now():
            return None
        with self._db() as connection:
            connection.execute(
                "UPDATE auth_tokens SET last_used_at = ? WHERE id = ?",
                (utc_now_iso(), row["id"]),
            )
            connection.commit()
        return {
            "user_id": row["user_id"],
            "username": row["username"],
            "team_id": row["team_id"],
            "is_admin": row["role"] == "admin",
            "scopes": json.loads(row["scopes"] or "[]"),
            "repo_ids": json.loads(row["repo_ids"] or "[]"),
        }

    def revoke_token(self, token_id: str, *, user_id: Optional[str] = None) -> bool:
        query = "UPDATE auth_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL"
        params: list[Any] = [utc_now_iso(), token_id]
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        with self._db() as connection:
            result = connection.execute(query, params)
            connection.commit()
        return result.rowcount > 0

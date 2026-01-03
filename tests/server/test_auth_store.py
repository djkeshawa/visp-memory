from visp_memory.server.auth_store import AuthStore


def test_auth_store_hashes_passwords_sessions_and_tokens(tmp_path):
    store = AuthStore(tmp_path / "auth.db")
    account = store.create_account(
        username="admin",
        password="correct-horse-battery-staple",
        role="admin",
    )
    authenticated = store.authenticate_password("ADMIN", "correct-horse-battery-staple")
    assert authenticated["id"] == account["id"]
    assert store.authenticate_password("admin", "incorrect-password") is None

    session, csrf = store.create_session(account["id"])
    authenticated = store.authenticate_session(session)
    assert authenticated["csrf_token"] == csrf

    token_record, token = store.create_token(
        user_id=account["id"],
        name="test",
        scopes=["memory:read"],
        repo_ids=["repo-a"],
    )
    principal = store.authenticate_token(token)
    assert principal["scopes"] == ["memory:read"]
    assert principal["repo_ids"] == ["repo-a"]
    assert store.revoke_token(token_record["id"], user_id=account["id"])
    assert store.authenticate_token(token) is None

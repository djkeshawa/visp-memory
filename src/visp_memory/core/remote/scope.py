def scoped_repo_id(storage, repo_id: str = None) -> str | None:
    """Prefer an explicit request scope, then the client's configured scope."""
    return repo_id if repo_id is not None else getattr(storage, "repo_id", None)

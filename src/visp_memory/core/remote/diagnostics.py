"""Scoped, read-only server inspection for client-mode diagnostics."""

from visp_memory.core.remote.errors import RemoteStorageError

_REPORT_FIELDS = {
    "inspect_intent_usage": {
        "exists": bool, "memories": int, "active_intents": int, "total_intents": int,
    },
    "inspect_repository_registration": {
        "exists": bool, "project_scopes": list, "unregistered_scopes": list,
    },
}


def _validate_report(report: dict, operation: str) -> None:
    for field, expected_type in _REPORT_FIELDS[operation].items():
        value = report.get(field)
        valid = type(value) is expected_type
        if valid and expected_type is list:
            valid = all(isinstance(item, str) for item in value)
        if valid and expected_type is int:
            valid = value >= 0
        if not valid:
            raise RemoteStorageError(f"Remote server returned an invalid {operation} report")


def inspect_remote(config, operation: str) -> dict:
    from visp_memory.core.remote_storage import RemoteStorage

    if operation not in _REPORT_FIELDS:
        raise ValueError(f"Unknown remote inspection: {operation}")
    if not isinstance(config.repo_id, str) or not config.repo_id.strip():
        raise ValueError("repo_id is required for remote diagnostics")
    remote = RemoteStorage(
        server_url=config.storage.server_url,
        api_key=config.storage.api_key,
        jwt_token=config.storage.jwt_token,
        repo_id=config.repo_id,
        timeout=5.0,
    )
    try:
        report = getattr(remote, operation)()
        _validate_report(report, operation)
        return report
    finally:
        remote.close()

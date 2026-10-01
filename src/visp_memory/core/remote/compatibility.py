"""Recognize FastAPI's default 404 without hiding resource or auth errors."""


def route_missing(response) -> bool:
    if response.status_code != 404:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("detail") == "Not Found"

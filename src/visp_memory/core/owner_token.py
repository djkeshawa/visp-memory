"""Wire names and literal endpoint paths shared by servers and clients."""

from pathlib import Path
from urllib.parse import quote

OWNER_TOKEN_HEADER = "X-Visp-Owner-Token"
OWNER_TOKEN_FILE_ENV = "VISP_MEMORY_SERVER_OWNER_TOKEN_FILE"


def owner_token_paths(directory: Path, host: str, port: int) -> tuple[Path, Path]:
    # Percent encoding keeps IPv6 colons and other host characters portable on Windows.
    endpoint = f"{quote(host, safe='')}-{port}"
    return directory / f"owner-{endpoint}.token", directory / f"server-{endpoint}.json"

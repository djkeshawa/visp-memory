"""Bind settings inherited by the Uvicorn app process."""

import os
from contextlib import contextmanager
from pathlib import Path

from visp_memory.core.owner_token import OWNER_TOKEN_FILE_ENV


@contextmanager
def server_environment(host: str, port: int, owner_token_file: Path | None = None):
    """Pass the actual CLI bind and optional token path, then restore the caller."""
    names = ("VISP_MEMORY_BIND_HOST", "VISP_MEMORY_SERVER_PORT", OWNER_TOKEN_FILE_ENV)
    previous = {name: os.environ.get(name) for name in names}
    os.environ["VISP_MEMORY_BIND_HOST"] = host
    os.environ["VISP_MEMORY_SERVER_PORT"] = str(port)
    if owner_token_file is None:
        os.environ.pop(OWNER_TOKEN_FILE_ENV, None)
    else:
        os.environ[OWNER_TOKEN_FILE_ENV] = str(owner_token_file)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

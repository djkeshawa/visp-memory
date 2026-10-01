"""Bound graph imports before FastAPI buffers and parses their JSON."""

import re
from typing import Callable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import get_route_path


class ImportBodyLimitMiddleware:
    def __init__(self, app, max_body_bytes: Callable[[], int]):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not re.fullmatch(r"/repos/.*/import", get_route_path(scope))
        ):
            await self.app(scope, receive, send)
            return

        limit = self.max_body_bytes()
        try:
            declared_size = int(Headers(scope=scope).get("content-length", "0"))
        except ValueError:
            declared_size = 0
        if declared_size > limit:
            await self._reject(scope, receive, send)
            return

        # Count actual bytes too: chunked requests and dishonest headers must
        # not bypass the bound. Only hand the body to the parser after it fits.
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > limit:
                await self._reject(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        buffered = {"type": "http.request", "body": b"".join(chunks), "more_body": False}
        chunks.clear()

        async def replay():
            nonlocal buffered
            if buffered is not None:
                message, buffered = buffered, None
                return message
            return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, scope, receive, send):
        response = JSONResponse(status_code=413, content={"detail": "Import body is too large"})
        await response(scope, receive, send)

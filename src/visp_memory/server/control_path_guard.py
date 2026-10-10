"""Refuse request paths that carry control characters before anything routes them.

Starlette compiles route paths to regexes ending in ``$``, which also matches just
before a trailing newline, so ``/memories%0A`` is served by ``/memories``. The token
scope table and the import body limit match with ``re.fullmatch``, where ``.*``
stops at the newline, so the same path missed their rules. No legitimate route
contains a control character; refusing them here keeps every path-keyed decision
in step with the router.
"""

import re

from starlette.responses import JSONResponse

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class ControlPathGuardMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and _CONTROL.search(
            scope.get("root_path", "") + scope.get("path", "")
        ):
            response = JSONResponse(status_code=400, content={"detail": "Invalid request path"})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

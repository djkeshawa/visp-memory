"""Bind informational writer labels for one ASGI request."""

from starlette.datastructures import Headers

from visp_memory.core.attribution import bind_writer, identity_from_headers


class AttributionMiddleware:
    """Keep writer identity in the context inherited by endpoint thread workers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        identity = identity_from_headers(Headers(scope=scope))
        # An unlabeled request must not inherit the shell that launched the server.
        with bind_writer(identity):
            await self.app(scope, receive, send)

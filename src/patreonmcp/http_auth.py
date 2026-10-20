"""Minimal bearer-token guard for the streamable-HTTP transport."""

from __future__ import annotations

import hmac
from typing import Any


class BearerAuthMiddleware:
    """ASGI middleware that requires a fixed bearer token for HTTP requests."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._expected = b"Bearer " + token.encode("utf-8")

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        if not hmac.compare_digest(headers.get(b"authorization", b""), self._expected):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"error":"unauthorized"}',
                }
            )
            return
        await self.app(scope, receive, send)

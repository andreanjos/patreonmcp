from __future__ import annotations

from patreonmcp.http_auth import BearerAuthMiddleware


async def call_middleware(token: str, headers: list[tuple[bytes, bytes]]):
    sent: list[dict] = []
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    middleware = BearerAuthMiddleware(app, token)
    await middleware({"type": "http", "headers": headers}, receive, send)
    return called, sent


async def test_bearer_middleware_rejects_missing_or_wrong_token() -> None:
    called, sent = await call_middleware("a-secure-token-12345", [])
    assert not called
    assert sent[0]["status"] == 401

    called, sent = await call_middleware(
        "a-secure-token-12345", [(b"authorization", b"Bearer wrong-token")]
    )
    assert not called
    assert sent[0]["status"] == 401


async def test_bearer_middleware_accepts_correct_token() -> None:
    called, sent = await call_middleware(
        "a-secure-token-12345", [(b"authorization", b"Bearer a-secure-token-12345")]
    )
    assert called
    assert sent[0]["status"] == 200

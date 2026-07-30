from __future__ import annotations

import asyncio

from talkies.auth import BearerAuthMiddleware


def _websocket_scope(headers: list[tuple[bytes, bytes]]) -> dict:
    return {
        "type": "websocket",
        "path": "/v1/audio/transcriptions/stream",
        "headers": headers,
    }


async def _receive() -> dict:
    return {"type": "websocket.connect"}


def test_websocket_missing_token_closes_with_4401() -> None:
    called = False
    messages: list[dict] = []

    async def app(scope, receive, send) -> None:
        nonlocal called
        called = True

    async def send(message: dict) -> None:
        messages.append(message)

    middleware = BearerAuthMiddleware(app, "secret")
    asyncio.run(middleware(_websocket_scope([]), _receive, send))

    assert called is False
    assert messages == [
        {
            "type": "websocket.close",
            "code": 4401,
            "reason": "missing Authorization: Bearer header",
        }
    ]


def test_websocket_wrong_token_does_not_echo_token() -> None:
    messages: list[dict] = []

    async def app(scope, receive, send) -> None:
        raise AssertionError("unauthorized websocket reached the app")

    async def send(message: dict) -> None:
        messages.append(message)

    middleware = BearerAuthMiddleware(app, "secret")
    asyncio.run(
        middleware(
            _websocket_scope([(b"authorization", b"Bearer wrong-token")]),
            _receive,
            send,
        )
    )

    assert messages[0]["code"] == 4401
    assert "wrong-token" not in messages[0]["reason"]

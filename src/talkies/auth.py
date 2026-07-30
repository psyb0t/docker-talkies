"""Optional bearer-token auth for the ASGI app.

When ``config.AUTH_TOKEN`` is non-empty, every request must include
``Authorization: Bearer <token>`` or it gets 401. Empty token = pass-through
(the historical default — the README is loud about putting this thing
behind something that does auth, but a built-in token is the lower-friction
option for self-hosters who just want one shared secret).

Exemptions: ``/healthz`` is always reachable so k8s / docker probes keep
working without leaking the token to every probe loop. ``OPTIONS``
requests (CORS preflights) are also let through — preflights aren't
allowed to send the Authorization header.

Implemented as ASGI middleware (not a FastAPI dependency) so it covers
mounted sub-apps too — specifically the MCP Streamable HTTP transport at
``/v1/mcp``, which is a Starlette app mounted into FastAPI.
"""

from __future__ import annotations

import hmac

from starlette.types import ASGIApp, Receive, Scope, Send

_EXEMPT_PATHS = frozenset({"/healthz"})
_BEARER_PREFIX = "Bearer "


class BearerAuthMiddleware:
    """ASGI middleware that enforces a static bearer token."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.token:
            await self.app(scope, receive, send)
            return
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        # ASGI gives us the path with the mount-prefix already stripped
        # for mounted sub-apps; rebuild the full path so exemption checks
        # against `/healthz` work regardless of where the middleware sits.
        path = scope.get("path", "")
        if path in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        token = _extract_bearer(scope)
        if token is None:
            await _send_unauthorized(
                scope,
                send,
                "missing Authorization: Bearer header",
            )
            return
        # Constant-time compare to dodge any timing oracle on the token.
        if not hmac.compare_digest(token, self.token):
            await _send_unauthorized(scope, send, "invalid bearer token")
            return
        await self.app(scope, receive, send)


def _extract_bearer(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            decoded = value.decode("latin-1")
            if decoded.startswith(_BEARER_PREFIX):
                return decoded[len(_BEARER_PREFIX) :].strip()
            return None
    return None


async def _send_401(send: Send, detail: str) -> None:
    body = f'{{"detail":"{detail}"}}'.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_unauthorized(scope: Scope, send: Send, detail: str) -> None:
    if scope["type"] == "websocket":
        await send(
            {
                "type": "websocket.close",
                "code": 4401,
                "reason": detail,
            }
        )
        return
    await _send_401(send, detail)

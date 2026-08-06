"""Typed runtime errors shared by HTTP, WebSocket, and MCP surfaces."""

from __future__ import annotations


class ModelAdmissionError(RuntimeError):
    """A model request could not be admitted without exceeding a limit."""

    def __init__(self, code: str, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code

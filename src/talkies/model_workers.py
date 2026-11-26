from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from functools import partial
from typing import Any
from uuid import uuid4

from anyio import CancelScope

from .errors import ModelProcessError
from .model_process import ModelProcess
from .models import build_backends

_MODEL_METHODS = frozenset({"get_model", "transcribe", "synthesize"})
_SESSION_METHODS = frozenset({"feed", "finalize", "cancel"})


class SpeechWorker:
    def __init__(self, factory: Callable[..., Any], args: tuple[Any, ...]) -> None:
        self.backend = factory(*args)
        self.sessions: dict[str, Any] = {}
        self.tts_streams: dict[str, Any] = {}

    async def execute(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        if operation in _MODEL_METHODS:
            result = await getattr(self.backend, operation)(*args, **kwargs)
            return None if operation == "get_model" else result
        handle, *values = args
        if operation == "start_stream":
            self.sessions[handle] = await self.backend.start_stream(*values, **kwargs)
            return None
        if operation == "close":
            session = self.sessions.pop(handle, None)
            if session is not None:
                await session.close()
            return None
        if operation in _SESSION_METHODS:
            return await getattr(self.sessions[handle], operation)(*values)
        if operation == "tts_start":
            self.tts_streams[handle] = self.backend.synthesize_stream(*values, **kwargs)
            return None
        if operation == "tts_next":
            return await anext(self.tts_streams[handle], None)
        if operation == "tts_close":
            stream = self.tts_streams.pop(handle, None)
            if stream is not None:
                await stream.aclose()
            return None
        raise ValueError("unknown speech worker operation")

    def resident(self) -> bool:
        return bool(self.backend.loaded())

    async def close(self) -> None:
        for session in self.sessions.values():
            await session.close()
        for stream in self.tts_streams.values():
            await stream.aclose()


class StreamSession:
    def __init__(self, handle: str, call: Callable[..., Any]) -> None:
        self._handle = handle
        self._call = call

    async def feed(self, frame: bytes) -> Any:
        return await self._call("feed", self._handle, frame)

    async def finalize(self) -> Any:
        return await self._call("finalize", self._handle)

    async def cancel(self) -> None:
        await self._call("cancel", self._handle)

    async def close(self) -> None:
        await self._call("close", self._handle)


def _build_backend(slug: str, entry: dict, device: str) -> Any:
    return build_backends({slug: entry}, device)[slug]


class WorkerBackend:
    def __init__(
        self, metadata: Any, factory: Callable[..., Any], *factory_args: Any
    ) -> None:
        self._metadata = metadata
        self._process = ModelProcess(SpeechWorker, factory, factory_args)
        self._last_used: float | None = None

    def __getattr__(self, name: str) -> Any:
        metadata = getattr(self._metadata, name)
        if name == "start_stream":
            return self._start_stream
        if name == "synthesize_stream":
            return self._synthesize_stream
        if name in _MODEL_METHODS:
            return partial(self._call, name)
        return metadata

    def loaded(self) -> bool:
        return bool(self._process.resident)

    def last_used_secs_ago(self) -> float | None:
        return None if self._last_used is None else time.monotonic() - self._last_used

    async def unload(self) -> None:
        await self._process.stop()
        self._last_used = None

    async def _call(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return await self._process.call(operation, *args, **kwargs)
        finally:
            self._last_used = time.monotonic()

    async def _start_stream(self, *args: Any, **kwargs: Any) -> StreamSession:
        handle = uuid4().hex
        session = StreamSession(handle, self._call)
        try:
            await self._call("start_stream", handle, *args, **kwargs)
        except BaseException:
            with CancelScope(shield=True), suppress(ModelProcessError):
                await session.close()
            raise
        return session

    async def _synthesize_stream(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        handle = uuid4().hex
        try:
            await self._call("tts_start", handle, *args, **kwargs)
            while (chunk := await self._call("tts_next", handle)) is not None:
                yield chunk
        finally:
            with CancelScope(shield=True), suppress(ModelProcessError):
                await self._call("tts_close", handle)


def isolate_models(registry: dict[str, dict], device: str) -> dict[str, Any]:
    return {
        slug: WorkerBackend(metadata, _build_backend, slug, registry[slug], device)
        for slug, metadata in build_backends(registry, device).items()
    }

from __future__ import annotations

import asyncio
import ctypes
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

# Import the module under test without executing the eager backend factory in
# talkies.models.__init__.py. The dev image intentionally excludes every heavy
# ML dependency imported by that factory.
_MODELS_PACKAGE = types.ModuleType("talkies.models")
_MODELS_PACKAGE.__path__ = [
    str(Path(__file__).resolve().parents[1] / "src" / "talkies" / "models")
]
sys.modules["talkies.models"] = _MODELS_PACKAGE

from talkies.models.parakeet_cpp import (
    _CAPI,  # noqa: E402
    ParakeetCppBackend,
    _consume_stream_json,
    _pcm16le_to_float32,
)


class _NativeFunction:
    def __init__(self, callback: Any) -> None:
        self._callback = callback
        self.restype: Any = None
        self.argtypes: list[Any] = []

    def __call__(self, *args: Any) -> Any:
        return self._callback(*args)


class _FakeLibrary:
    def __init__(self) -> None:
        self.buffers: list[Any] = []
        self.begin_calls: list[str | None] = []
        self.feed_samples: list[list[float]] = []
        self.freed_strings = 0
        self.freed_streams: list[int] = []
        self.freed_contexts: list[int] = []
        self.feed_document: Any = {
            "text": "hello <en-us>",
            "eou": 1,
            "frame_sec": 0.08,
            "words": [
                {"w": "hello", "start": 0.1, "end": 0.4, "conf": 0.95},
                {"w": "world", "start": 0.5, "end": 0.8, "conf": 0.8},
                {"w": "<en-us>", "start": 0.8, "end": 0.9, "conf": 1.0},
            ],
        }
        self.final_document: Any = {
            "text": "done",
            "eou": 0,
            "frame_sec": 0.08,
            "words": [],
        }
        self.last_error = b"native failure"

        self.parakeet_capi_stream_begin = _NativeFunction(self._begin)
        self.parakeet_capi_stream_begin_lang = _NativeFunction(self._begin_lang)
        self.parakeet_capi_stream_feed_json = _NativeFunction(self._feed)
        self.parakeet_capi_stream_finalize_json = _NativeFunction(self._finalize)
        self.parakeet_capi_stream_free = _NativeFunction(self._free_stream)
        self.parakeet_capi_free = _NativeFunction(self._free_context)
        self.parakeet_capi_free_string = _NativeFunction(self._free_string)
        self.parakeet_capi_last_error = _NativeFunction(lambda _ctx: self.last_error)

    def _json_pointer(self, document: Any) -> int:
        if isinstance(document, bytes):
            payload = document
        else:
            payload = json.dumps(document).encode("utf-8")
        buffer = ctypes.create_string_buffer(payload)
        self.buffers.append(buffer)
        return ctypes.addressof(buffer)

    def _begin(self, _ctx: Any) -> int:
        self.begin_calls.append(None)
        return 201

    def _begin_lang(self, _ctx: Any, language: bytes) -> int:
        self.begin_calls.append(language.decode("utf-8"))
        return 202

    def _feed(self, _stream: Any, pcm: Any, sample_count: int) -> int:
        self.feed_samples.append([float(pcm[i]) for i in range(sample_count)])
        return self._json_pointer(self.feed_document)

    def _finalize(self, _stream: Any) -> int:
        return self._json_pointer(self.final_document)

    def _free_stream(self, stream: Any) -> None:
        self.freed_streams.append(int(stream.value))

    def _free_context(self, context: Any) -> None:
        self.freed_contexts.append(int(context))

    def _free_string(self, _pointer: Any) -> None:
        self.freed_strings += 1


@pytest.fixture
def fake_capi(monkeypatch: pytest.MonkeyPatch) -> _FakeLibrary:
    library = _FakeLibrary()
    capi = SimpleNamespace(lib=library, abi_version=4)
    monkeypatch.setattr(_CAPI, "_instance", capi)
    return library


def _backend(tmp_path: Path) -> ParakeetCppBackend:
    backend = ParakeetCppBackend(
        model_id="stream-test",
        repo="example/model",
        model_path=tmp_path,
        device="cpu",
    )
    backend._ctx = 101
    return backend


def test_pcm16_conversion_preserves_signed_range() -> None:
    samples = _pcm16le_to_float32(
        b"\x00\x80\x00\x00\xff\x7f",
        max_frame_bytes=6,
    )
    assert list(samples) == pytest.approx([-1.0, 0.0, 32767 / 32768])


@pytest.mark.parametrize(
    ("frame", "limit", "message"),
    [
        (b"", 8, "empty"),
        (b"\x00", 8, "complete 16-bit"),
        (b"\x00\x00\x00\x00", 2, "limit"),
    ],
)
def test_pcm16_conversion_rejects_invalid_frames(
    frame: bytes,
    limit: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _pcm16le_to_float32(frame, max_frame_bytes=limit)


def test_stream_feed_parses_words_confidence_and_eou(
    fake_capi: _FakeLibrary,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        session = await backend.start_stream(max_frame_bytes=8)
        event = await session.feed(b"\x00\x80\x00\x00\xff\x7f")

        assert fake_capi.begin_calls == [None]
        assert fake_capi.feed_samples[0] == pytest.approx([-1.0, 0.0, 32767 / 32768])
        assert event == {
            "text": "hello",
            "words": [
                {
                    "word": "hello",
                    "start": 0.1,
                    "end": 0.4,
                    "confidence": 0.95,
                },
                {
                    "word": "world",
                    "start": 0.5,
                    "end": 0.8,
                    "confidence": 0.8,
                },
            ],
            "confidence": 0.8,
            "eou": True,
            "frame_sec": 0.08,
        }
        await session.cancel()

    asyncio.run(scenario())
    assert fake_capi.freed_strings == 1
    assert fake_capi.freed_streams == [201]


def test_explicit_language_uses_begin_lang(
    fake_capi: _FakeLibrary,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session = await _backend(tmp_path).start_stream(language="de")
        await session.cancel()

    asyncio.run(scenario())
    assert fake_capi.begin_calls == ["de"]
    assert fake_capi.freed_streams == [202]


def test_finalize_and_cancel_are_idempotent(
    fake_capi: _FakeLibrary,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        session = await backend.start_stream()
        first = await session.finalize()
        second = await session.finalize()
        await session.cancel()
        await session.close()

        assert first is second
        assert first["text"] == "done"
        assert backend._active_streams == 0

    asyncio.run(scenario())
    assert fake_capi.freed_strings == 1
    assert fake_capi.freed_streams == [201]


def test_active_stream_prevents_backend_unload(
    fake_capi: _FakeLibrary,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        session = await backend.start_stream()
        with pytest.raises(RuntimeError, match="streaming session"):
            await backend.unload()
        await session.cancel()

    asyncio.run(scenario())
    assert fake_capi.freed_streams == [201]


def test_backend_unload_releases_native_model_context(
    fake_capi: _FakeLibrary,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)

        await backend.unload()

        assert backend.loaded() is False

    asyncio.run(scenario())
    assert fake_capi.freed_contexts == [101]


def test_malformed_native_json_is_freed_and_rejected(
    fake_capi: _FakeLibrary,
    tmp_path: Path,
) -> None:
    fake_capi.feed_document = b"{not-json"

    async def scenario() -> None:
        session = await _backend(tmp_path).start_stream()
        with pytest.raises(RuntimeError, match="malformed JSON"):
            await session.feed(b"\x00\x00")
        await session.cancel()

    asyncio.run(scenario())
    assert fake_capi.freed_strings == 1
    assert fake_capi.freed_streams == [201]


def test_native_null_reports_bounded_contextual_error(
    fake_capi: _FakeLibrary,
) -> None:
    capi = cast(_CAPI, SimpleNamespace(lib=fake_capi, abi_version=4))
    with pytest.raises(RuntimeError, match="feed_json failed: native failure"):
        _consume_stream_json(capi, 101, 0, "feed_json")


@pytest.mark.parametrize(
    "invalid_word",
    [
        {"w": "bad", "start": 1.0, "end": 0.5, "conf": 0.5},
        {"w": "bad", "start": 0.0, "end": 0.5, "conf": float("nan")},
        {"w": "bad", "start": 0.0, "end": 0.5, "conf": 1.5},
    ],
)
def test_invalid_native_word_is_rejected(
    fake_capi: _FakeLibrary,
    invalid_word: dict[str, Any],
) -> None:
    pointer = fake_capi._json_pointer(
        {
            "text": "bad",
            "eou": 0,
            "frame_sec": 0.08,
            "words": [invalid_word],
        }
    )
    capi = cast(_CAPI, SimpleNamespace(lib=fake_capi, abi_version=4))
    with pytest.raises(RuntimeError, match="invalid|non-finite"):
        _consume_stream_json(capi, 101, pointer, "feed_json")
    assert fake_capi.freed_strings == 1

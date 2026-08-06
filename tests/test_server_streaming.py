from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from talkies.asr_streaming import (
    EVENT_FINAL,
    EVENT_PARTIAL,
    StreamConfig,
    TranscriptEvent,
)

_MODELS_PACKAGE = types.ModuleType("talkies.models")
_MODELS_PACKAGE.__path__ = [
    str(Path(__file__).resolve().parents[1] / "src" / "talkies" / "models")
]
sys.modules.setdefault("talkies.models", _MODELS_PACKAGE)

from talkies.models.base import TranscribeResult


class _FakeStreamSession:
    def __init__(self) -> None:
        self.canceled = False
        self.closed = False
        self.feed_count = 0

    async def feed(self, frame: bytes) -> list[TranscriptEvent]:
        self.feed_count += 1
        return [
            TranscriptEvent(
                event_type=EVENT_PARTIAL,
                revision=self.feed_count,
                text="hello",
                audio_seconds=len(frame) / 32000,
            )
        ]

    async def finalize(self) -> TranscriptEvent:
        return TranscriptEvent(
            event_type=EVENT_FINAL,
            revision=self.feed_count + 1,
            text="hello world",
            audio_seconds=0.01,
            is_final=True,
        )

    async def cancel(self) -> None:
        self.canceled = True

    async def close(self) -> None:
        self.closed = True


class _FakeBackend:
    repo = "example/stream-model"

    def __init__(self) -> None:
        self.sessions: list[_FakeStreamSession] = []
        self.configs: list[StreamConfig] = []
        self.transcribe_calls: list[dict[str, object]] = []
        self.unload_count = 0

    async def start_stream(self, config: StreamConfig) -> _FakeStreamSession:
        session = _FakeStreamSession()
        self.configs.append(config)
        self.sessions.append(session)
        return session

    async def transcribe(
        self,
        audio_path: str,
        *,
        source_lang: str | None,
        target_lang: str | None,
        task: str,
        with_timestamps: bool = False,
    ) -> TranscribeResult:
        self.transcribe_calls.append(
            {
                "audio_path": audio_path,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "task": task,
                "with_timestamps": with_timestamps,
            }
        )
        return TranscribeResult(text="file transcript", language=source_lang)

    async def unload(self) -> None:
        self.unload_count += 1

    def loaded(self) -> bool:
        return True

    def last_used_secs_ago(self) -> float:
        return 0.0


class _FakeTTSBackend:
    repo = "example/tts-model"
    sample_rate = 24000

    def default_voice(self) -> str:
        return "voice"

    def voices(self) -> list[str]:
        return ["voice"]

    async def synthesize(self, *args, **kwargs):
        return types.SimpleNamespace(pcm_int16=b"pcm", sample_rate=self.sample_rate)

    async def synthesize_stream(self, *args, **kwargs):
        yield b"chunk-one"
        yield b"chunk-two"

    async def unload(self) -> None:
        return None

    def loaded(self) -> bool:
        return False

    def last_used_secs_ago(self) -> float:
        return 0.0


class _DummySessionManager:
    @asynccontextmanager
    async def run(self):
        yield


class _DummyMCPServer:
    def __init__(self) -> None:
        self.session_manager = _DummySessionManager()

    def streamable_http_app(self):
        async def app(scope, receive, send) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [],
                }
            )
            await send({"type": "http.response.body", "body": b""})

        return app


@pytest.fixture
def streaming_server(monkeypatch, tmp_path: Path):
    registry_path = tmp_path / "models.json"
    registry_path.write_text(
        json.dumps(
            {
                "models": {
                    "stream-model": {
                        "repo": "example/stream-model",
                        "executor": "vosk",
                    }
                }
            }
        )
    )
    monkeypatch.setenv("TALKIES_MODELS_FILE", str(registry_path))
    monkeypatch.setenv("TALKIES_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TALKIES_STREAM_MAX_CONNECTIONS", "1")

    backend = _FakeBackend()
    models_module = types.ModuleType("talkies.models")
    models_module.__path__ = [
        str(Path(__file__).resolve().parents[1] / "src" / "talkies" / "models")
    ]
    models_module.build_backends = lambda registry, device: {"stream-model": backend}
    models_module.is_asr_backend = lambda candidate: hasattr(candidate, "transcribe")
    models_module.is_streaming_asr_backend = lambda candidate: hasattr(
        candidate,
        "start_stream",
    )
    models_module.is_tts_backend = lambda candidate: False

    mcp_module = types.ModuleType("talkies.mcp_server")
    mcp_module.build_mcp_server = lambda **kwargs: _DummyMCPServer()

    replaced = {
        name: sys.modules.get(name)
        for name in (
            "talkies.config",
            "talkies.downloads",
            "talkies.files",
            "talkies.mcp_server",
            "talkies.models",
            "talkies.server",
            "talkies.tts",
        )
    }
    for name in replaced:
        sys.modules.pop(name, None)
    talkies_package = importlib.import_module("talkies")
    package_attributes = {
        name: getattr(talkies_package, name, None)
        for name in (
            "config",
            "downloads",
            "files",
            "mcp_server",
            "models",
            "server",
            "tts",
        )
    }
    for name in package_attributes:
        if hasattr(talkies_package, name):
            delattr(talkies_package, name)
    sys.modules["talkies.models"] = models_module
    sys.modules["talkies.mcp_server"] = mcp_module
    talkies_package.models = models_module
    talkies_package.mcp_server = mcp_module
    server = importlib.import_module("talkies.server")
    yield server, backend
    for name in replaced:
        sys.modules.pop(name, None)
    for name, module in replaced.items():
        if module is not None:
            sys.modules[name] = module
    for name in package_attributes:
        if hasattr(talkies_package, name):
            delattr(talkies_package, name)
    for name, module in package_attributes.items():
        if module is not None:
            setattr(talkies_package, name, module)


def _start_message(model: str = "stream-model") -> dict[str, Any]:
    return {
        "type": "start",
        "model": model,
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
        "channels": 1,
        "word_timestamps": True,
    }


def test_stream_happy_path_emits_ready_partial_final_and_stats(
    streaming_server,
) -> None:
    server, backend = streaming_server
    with TestClient(server.app) as client:
        with client.websocket_connect("/v1/audio/transcriptions/stream") as websocket:
            websocket.send_json(_start_message())
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_bytes(b"\x00\x00" * 160)
            assert websocket.receive_json()["type"] == "partial"
            websocket.send_json({"type": "end"})
            final = websocket.receive_json()
            stats = websocket.receive_json()

    assert final["type"] == "final"
    assert final["text"] == "hello world"
    assert stats == {
        "type": "stats",
        "audio_seconds": 0.01,
        "frames": 1,
        "canceled": False,
    }
    assert backend.configs[0].word_timestamps is True
    assert backend.sessions[0].closed is True


def test_file_transcription_accepts_a_streaming_asr_backend(
    streaming_server,
    monkeypatch,
    tmp_path: Path,
) -> None:
    server, backend = streaming_server
    normalized_wav = tmp_path / "normalized.wav"
    with wave.open(str(normalized_wav), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00" * 160)
    monkeypatch.setattr(
        server,
        "to_wav_16k_mono",
        lambda raw, original_name: str(normalized_wav),
    )

    with TestClient(server.app) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            data={"model": "stream-model", "response_format": "json"},
            files={"file": ("audio.wav", b"not-read-by-fake-converter", "audio/wav")},
        )

    assert response.status_code == 200
    assert response.json() == {"text": "file transcript"}
    assert backend.transcribe_calls == [
        {
            "audio_path": str(normalized_wav),
            "source_lang": None,
            "target_lang": None,
            "task": "asr",
            "with_timestamps": False,
        }
    ]
    assert server._active_request_count("stream-model") == 0


def test_model_admission_allows_two_and_rejects_third(streaming_server) -> None:
    server, _ = streaming_server
    server.REGISTRY["stream-model"]["max_concurrency"] = 2

    async def scenario() -> None:
        await server._reserve_model("stream-model")
        await server._reserve_model("stream-model", streaming=True)
        assert server._active_request_count("stream-model") == 2
        assert server._active_stream_count("stream-model") == 1
        with pytest.raises(server.ModelAdmissionError) as raised:
            await server._reserve_model("stream-model")
        assert raised.value.code == "model_capacity"
        assert raised.value.status_code == 429
        await server._release_model("stream-model", streaming=True)
        await server._release_model("stream-model")
        assert server._active_request_count("stream-model") == 0
        assert server._active_stream_count("stream-model") == 0

    asyncio.run(scenario())


def test_model_admission_blocks_sibling_without_eviction(streaming_server) -> None:
    server, _ = streaming_server
    sibling = _FakeBackend()
    server.BACKENDS["sibling"] = sibling
    server.REGISTRY["sibling"] = {
        "repo": sibling.repo,
        "executor": "vosk",
        "max_concurrency": 1,
    }

    async def scenario() -> None:
        await server._reserve_model("stream-model")
        sibling.unload_count = 0
        with pytest.raises(server.ModelAdmissionError) as raised:
            await server._reserve_model("sibling")
        assert raised.value.code == "model_busy"
        assert sibling.unload_count == 0
        await server._release_model("stream-model")

    try:
        asyncio.run(scenario())
    finally:
        server.BACKENDS.pop("sibling", None)
        server.REGISTRY.pop("sibling", None)


def test_http_transcription_returns_429_at_model_capacity(
    streaming_server,
) -> None:
    server, backend = streaming_server
    server._active_requests["stream-model"] = 1

    try:
        with TestClient(server.app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                data={"model": "stream-model", "response_format": "json"},
                files={"file": ("audio.wav", b"audio", "audio/wav")},
            )
    finally:
        server._active_requests.clear()

    assert response.status_code == 429
    assert response.json() == {"detail": "model concurrency limit reached (1)"}
    assert backend.transcribe_calls == []


def test_pipeline_failure_releases_model_capacity(
    streaming_server,
    monkeypatch,
    tmp_path: Path,
) -> None:
    server, backend = streaming_server
    normalized_wav = tmp_path / "normalized.wav"
    normalized_wav.write_bytes(b"not-a-real-wav")
    monkeypatch.setattr(
        server,
        "to_wav_16k_mono",
        lambda raw, original_name: str(normalized_wav),
    )
    monkeypatch.setattr(server, "_wav_duration_seconds", lambda path: None)

    async def fail_transcribe(*args, **kwargs):
        raise RuntimeError("backend failed")

    backend.transcribe = fail_transcribe

    with pytest.raises(RuntimeError, match="backend failed"):
        asyncio.run(
            server.run_transcription_pipeline(
                raw=b"audio",
                original_name="audio.wav",
                model="stream-model",
                language=None,
                response_format="json",
                do_diarize=False,
            )
        )

    assert server._active_request_count("stream-model") == 0


def test_buffered_tts_releases_model_capacity(
    streaming_server,
    monkeypatch,
) -> None:
    server, _ = streaming_server
    backend = _FakeTTSBackend()
    server.BACKENDS["tts-model"] = backend
    server.REGISTRY["tts-model"] = {
        "repo": backend.repo,
        "executor": "kokoro",
        "max_concurrency": 1,
    }
    monkeypatch.setattr(
        server, "is_tts_backend", lambda candidate: candidate is backend
    )

    async def encode_audio(pcm, sample_rate, fmt):
        assert pcm == b"pcm"
        assert sample_rate == 24000
        assert fmt == "wav"
        return b"encoded", "audio/wav"

    monkeypatch.setattr(server.tts_mod, "encode_audio", encode_audio)
    body = server.SpeechRequest(
        model="tts-model",
        input="hello",
        voice="voice",
        response_format="wav",
    )

    try:
        response = asyncio.run(server.speech(body))
    finally:
        server.BACKENDS.pop("tts-model", None)
        server.REGISTRY.pop("tts-model", None)

    assert response.body == b"encoded"
    assert server._active_request_count("tts-model") == 0


def test_streaming_tts_holds_capacity_until_iterator_finishes(
    streaming_server,
    monkeypatch,
) -> None:
    server, _ = streaming_server
    backend = _FakeTTSBackend()
    server.BACKENDS["tts-model"] = backend
    server.REGISTRY["tts-model"] = {
        "repo": backend.repo,
        "executor": "qwen3_tts",
        "max_concurrency": 1,
    }
    monkeypatch.setattr(
        server, "is_tts_backend", lambda candidate: candidate is backend
    )
    body = server.SpeechRequest(
        model="tts-model",
        input="hello",
        voice="voice",
        response_format="pcm",
    )

    async def scenario() -> bytes:
        response = await server.speech(body)
        assert server._active_request_count("tts-model") == 1
        chunks = [chunk async for chunk in response.body_iterator]
        assert server._active_request_count("tts-model") == 0
        return b"".join(chunks)

    try:
        audio = asyncio.run(scenario())
    finally:
        server.BACKENDS.pop("tts-model", None)
        server.REGISTRY.pop("tts-model", None)

    assert audio == b"chunk-onechunk-two"


def test_stream_suppresses_empty_native_partial(streaming_server) -> None:
    server, backend = streaming_server

    async def empty_feed(_frame: bytes) -> dict[str, object]:
        return {"text": "", "words": [], "eou": False}

    with TestClient(server.app) as client:
        with client.websocket_connect("/v1/audio/transcriptions/stream") as websocket:
            websocket.send_json(_start_message())
            assert websocket.receive_json()["type"] == "ready"
            backend.sessions[0].feed = empty_feed
            websocket.send_bytes(b"\x00\x00" * 160)
            websocket.send_json({"type": "end"})
            final = websocket.receive_json()
            stats = websocket.receive_json()

    assert final["type"] == "final"
    assert stats["type"] == "stats"


def test_stream_cancel_releases_session(streaming_server) -> None:
    server, backend = streaming_server
    with TestClient(server.app) as client:
        with client.websocket_connect("/v1/audio/transcriptions/stream") as websocket:
            websocket.send_json(_start_message())
            websocket.receive_json()
            websocket.send_json({"type": "cancel"})
            stats = websocket.receive_json()

    assert stats["canceled"] is True
    assert backend.sessions[0].canceled is True
    assert backend.sessions[0].closed is True


def test_stream_rejects_unknown_model_without_leaking_registry(
    streaming_server,
) -> None:
    server, _ = streaming_server
    with TestClient(server.app) as client:
        with client.websocket_connect("/v1/audio/transcriptions/stream") as websocket:
            websocket.send_json(_start_message("missing-model"))
            error = websocket.receive_json()

    assert error == {
        "type": "error",
        "code": "unknown_model",
        "detail": "requested model is not configured",
    }


def test_stream_rejects_oversized_frame(streaming_server) -> None:
    server, backend = streaming_server
    with TestClient(server.app) as client:
        with client.websocket_connect("/v1/audio/transcriptions/stream") as websocket:
            websocket.send_json(_start_message())
            websocket.receive_json()
            websocket.send_bytes(b"\x00\x00" * 32769)
            error = websocket.receive_json()

    assert error["code"] == "frame_too_large"
    assert backend.sessions[0].canceled is True


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("not-json", "invalid_json"),
        ('{"type":"start","type":"end"}', "duplicate_field"),
        (
            '{"type":"start","model":"stream-model","encoding":"pcm_s16le",'
            '"sample_rate":16000,"channels":1,"is_admin":true}',
            "unknown_field",
        ),
        (
            '{"type":"start","model":"../../etc/passwd","encoding":"pcm_s16le",'
            '"sample_rate":16000,"channels":1}',
            "invalid_model",
        ),
        ('{"type":"' + "x" * 4096 + '"}', "message_too_large"),
    ],
)
def test_stream_rejects_hostile_start_messages(
    streaming_server,
    message,
    code,
) -> None:
    server, backend = streaming_server
    with TestClient(server.app) as client:
        with client.websocket_connect("/v1/audio/transcriptions/stream") as websocket:
            websocket.send_text(message)
            error = websocket.receive_json()

    assert error["code"] == code
    assert backend.sessions == []


@pytest.mark.parametrize(
    ("frame", "code"),
    [
        (b"", "empty_audio"),
        (b"\x00", "unaligned_audio"),
    ],
)
def test_stream_rejects_invalid_pcm_frames(
    streaming_server,
    frame,
    code,
) -> None:
    server, backend = streaming_server
    with TestClient(server.app) as client:
        with client.websocket_connect("/v1/audio/transcriptions/stream") as websocket:
            websocket.send_json(_start_message())
            websocket.receive_json()
            websocket.send_bytes(frame)
            error = websocket.receive_json()

    assert error["code"] == code
    assert backend.sessions[0].canceled is True


def test_connection_limit_and_unload_conflict(streaming_server) -> None:
    server, _ = streaming_server
    with TestClient(server.app) as client:
        with client.websocket_connect("/v1/audio/transcriptions/stream") as first:
            first.send_json(_start_message())
            first.receive_json()
            response = client.delete("/api/ps/stream-model")
            assert response.status_code == 409

            with client.websocket_connect("/v1/audio/transcriptions/stream") as second:
                second.send_json(_start_message())
                error = second.receive_json()
            assert error["code"] == "connection_limit"

            first.send_json({"type": "cancel"})
            first.receive_json()


def test_manual_unload_drains_cuda_memory(streaming_server, monkeypatch) -> None:
    server, backend = streaming_server
    drain_calls = 0

    async def fake_drain() -> None:
        nonlocal drain_calls
        drain_calls += 1

    monkeypatch.setattr(server, "_wait_for_gpu_drain", fake_drain)

    with TestClient(server.app) as client:
        response = client.delete("/api/ps/stream-model")

    assert response.status_code == 200
    assert backend.unload_count == 1
    assert drain_calls == 1


def test_unload_all_drains_cuda_memory_once(streaming_server, monkeypatch) -> None:
    server, backend = streaming_server
    drain_calls = 0

    async def fake_drain() -> None:
        nonlocal drain_calls
        drain_calls += 1

    monkeypatch.setattr(server, "_wait_for_gpu_drain", fake_drain)

    with TestClient(server.app) as client:
        response = client.post("/unload")

    assert response.status_code == 200
    assert response.json() == {"unloaded": ["stream-model"]}
    assert backend.unload_count == 1
    assert drain_calls == 1


@pytest.mark.parametrize(
    ("initialized", "expected_calls"),
    [
        (False, []),
        (True, ["synchronize", "empty_cache", "ipc_collect"]),
    ],
)
def test_gpu_drain_does_not_initialize_an_unused_torch_context(
    streaming_server,
    monkeypatch,
    initialized: bool,
    expected_calls: list[str],
) -> None:
    server, _ = streaming_server
    calls: list[str] = []
    fake_cuda = types.SimpleNamespace(
        is_available=lambda: True,
        is_initialized=lambda: initialized,
        synchronize=lambda: calls.append("synchronize"),
        empty_cache=lambda: calls.append("empty_cache"),
        ipc_collect=lambda: calls.append("ipc_collect"),
    )
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=fake_cuda))

    asyncio.run(server._wait_for_gpu_drain())

    assert calls == expected_calls


def test_two_websockets_are_admitted_and_third_is_rejected(
    streaming_server,
) -> None:
    server, _ = streaming_server
    server.REGISTRY["stream-model"]["max_concurrency"] = 2
    server.config.STREAM_MAX_CONNECTIONS = 3

    with TestClient(server.app) as client:
        with client.websocket_connect("/v1/audio/transcriptions/stream") as first:
            first.send_json(_start_message())
            assert first.receive_json()["type"] == "ready"
            with client.websocket_connect("/v1/audio/transcriptions/stream") as second:
                second.send_json(_start_message())
                assert second.receive_json()["type"] == "ready"
                with client.websocket_connect(
                    "/v1/audio/transcriptions/stream"
                ) as third:
                    third.send_json(_start_message())
                    error = third.receive_json()
                assert error == {
                    "type": "error",
                    "code": "connection_limit",
                    "detail": "model concurrency limit reached (2)",
                }
                second.send_json({"type": "cancel"})
                second.receive_json()
            first.send_json({"type": "cancel"})
            first.receive_json()

    assert server._active_request_count("stream-model") == 0


def test_main_bounds_uvicorn_websocket_ingress(
    streaming_server,
    monkeypatch,
) -> None:
    server, _ = streaming_server
    run_calls: list[dict[str, Any]] = []
    uvicorn_module = types.ModuleType("uvicorn")
    uvicorn_module.run = lambda app, **kwargs: run_calls.append(kwargs)
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn_module)
    monkeypatch.setattr(server, "configure_logging", lambda: None)

    assert server.main() == 0
    assert run_calls == [
        {
            "host": "0.0.0.0",
            "port": 8000,
            "log_config": None,
            "ws_max_size": server.config.STREAM_MAX_FRAME_BYTES,
            "ws_max_queue": 1,
        }
    ]

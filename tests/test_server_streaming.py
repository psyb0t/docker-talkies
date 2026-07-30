from __future__ import annotations

import importlib
import json
import sys
import types
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
        self.unload_count = 0

    async def start_stream(self, config: StreamConfig) -> _FakeStreamSession:
        session = _FakeStreamSession()
        self.configs.append(config)
        self.sessions.append(session)
        return session

    async def unload(self) -> None:
        self.unload_count += 1

    def loaded(self) -> bool:
        return True

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

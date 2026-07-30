from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_MODELS_PACKAGE = types.ModuleType("talkies.models")
_MODELS_PACKAGE.__path__ = [
    str(Path(__file__).resolve().parents[1] / "src" / "talkies" / "models")
]
sys.modules["talkies.models"] = _MODELS_PACKAGE

from talkies import asr_streaming  # noqa: E402
from talkies.models import vosk as vosk_module  # noqa: E402

EVENT_ENDPOINT = asr_streaming.EVENT_ENDPOINT
EVENT_FINAL = asr_streaming.EVENT_FINAL
EVENT_PARTIAL = asr_streaming.EVENT_PARTIAL
StreamConfig = asr_streaming.StreamConfig
StreamingProtocolError = asr_streaming.StreamingProtocolError
TranscriptEvent = asr_streaming.TranscriptEvent
VoskBackend = vosk_module.VoskBackend
_parse_result_json = vosk_module._parse_result_json


class _FakeRecognizer:
    def __init__(self, model: Any, sample_rate: int) -> None:
        self.model = model
        self.sample_rate = sample_rate
        self.words_enabled = False
        self.partial_words_enabled = False
        self.endpoint = False
        self.accepted_frames: list[bytes] = []
        self.partial_document: Any = {"partial": ""}
        self.result_document: Any = {"text": "", "result": []}
        self.final_document: Any = {"text": "", "result": []}
        self.final_calls = 0

    def SetWords(self, enabled: bool) -> None:
        self.words_enabled = enabled

    def SetPartialWords(self, enabled: bool) -> None:
        self.partial_words_enabled = enabled

    def AcceptWaveform(self, frame: bytes) -> bool:
        self.accepted_frames.append(frame)
        return self.endpoint

    def PartialResult(self) -> str:
        return _document(self.partial_document)

    def Result(self) -> str:
        return _document(self.result_document)

    def FinalResult(self) -> str:
        self.final_calls += 1
        return _document(self.final_document)


class _FakeVosk(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("vosk")
        self.model_paths: list[str] = []
        self.recognizers: list[_FakeRecognizer] = []

    def Model(self, path: str) -> object:
        self.model_paths.append(path)
        return object()

    def KaldiRecognizer(self, model: Any, sample_rate: int) -> _FakeRecognizer:
        recognizer = _FakeRecognizer(model, sample_rate)
        self.recognizers.append(recognizer)
        return recognizer


def _document(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value)


@pytest.fixture
def fake_vosk(monkeypatch: pytest.MonkeyPatch) -> _FakeVosk:
    module = _FakeVosk()
    monkeypatch.setitem(sys.modules, "vosk", module)
    return module


def _backend(tmp_path: Path) -> VoskBackend:
    return VoskBackend(
        model_id="vosk-test",
        repo="example/vosk-model",
        model_path=tmp_path,
        device="cpu",
    )


def _config(**overrides: Any) -> StreamConfig:
    values: dict[str, Any] = {
        "model": "vosk-test",
        "word_timestamps": True,
    }
    values.update(overrides)
    return StreamConfig(**values)


def test_backend_loads_model_lazily_once(
    fake_vosk: _FakeVosk,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        assert not backend.loaded()
        first = await backend.get_model()
        second = await backend.get_model()
        assert first is second
        assert backend.loaded()

    asyncio.run(scenario())
    assert fake_vosk.model_paths == [str(tmp_path)]


def test_stream_configures_word_results(
    fake_vosk: _FakeVosk,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        session = await backend.start_stream(_config(language="en"))
        recognizer = fake_vosk.recognizers[0]
        assert recognizer.sample_rate == 16_000
        assert recognizer.words_enabled
        assert recognizer.partial_words_enabled
        assert backend.active_streams == 1
        await session.close()
        assert backend.active_streams == 0

    asyncio.run(scenario())


def test_partial_result_preserves_words_and_confidence(
    fake_vosk: _FakeVosk,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session = await _backend(tmp_path).start_stream(_config())
        recognizer = fake_vosk.recognizers[0]
        recognizer.partial_document = {
            "partial": " hello ",
            "partial_result": [
                {"word": "hello", "start": 0.1, "end": 0.4, "conf": 0.9}
            ],
        }
        events = await session.feed(b"\x00\x00\xff\x7f")
        assert events == [
            TranscriptEvent(
                event_type=EVENT_PARTIAL,
                revision=1,
                text="hello",
                words=[
                    {
                        "word": "hello",
                        "start": 0.1,
                        "end": 0.4,
                        "confidence": 0.9,
                    }
                ],
                audio_seconds=2 / 16_000,
                is_final=False,
            )
        ]
        assert await session.feed(b"\x00\x00") == []
        await session.cancel()

    asyncio.run(scenario())


def test_endpoint_result_is_final_event(
    fake_vosk: _FakeVosk,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session = await _backend(tmp_path).start_stream(_config())
        recognizer = fake_vosk.recognizers[0]
        recognizer.endpoint = True
        recognizer.result_document = {
            "text": "done",
            "result": [{"word": "done", "start": 0.2, "end": 0.5, "conf": 0.8}],
        }
        events = await session.feed(b"\x00\x00")
        assert events[0].event_type == EVENT_ENDPOINT
        assert events[0].is_final is False
        assert events[0].words[0]["confidence"] == 0.8
        assert events[0].words[0]["end"] == 0.5
        await session.cancel()

    asyncio.run(scenario())


def test_stream_options_suppress_interim_and_word_timestamps(
    fake_vosk: _FakeVosk,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session = await _backend(tmp_path).start_stream(
            _config(interim_results=False, word_timestamps=False)
        )
        recognizer = fake_vosk.recognizers[0]
        recognizer.partial_document = {"partial": "ignored"}
        assert await session.feed(b"\x00\x00") == []

        recognizer.endpoint = True
        recognizer.result_document = {
            "text": "endpoint",
            "result": [{"word": "endpoint", "start": 0.0, "end": 0.2, "conf": 0.7}],
        }
        events = await session.feed(b"\x00\x00")
        assert events[0].text == "endpoint"
        assert events[0].words == []
        await session.cancel()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (b"", "empty"),
        (b"\x00", "complete int16"),
        (b"\x00\x00\x00\x00", "limit"),
    ],
)
def test_feed_rejects_invalid_pcm_frames(
    fake_vosk: _FakeVosk,
    tmp_path: Path,
    frame: bytes,
    message: str,
) -> None:
    async def scenario() -> None:
        backend = VoskBackend(
            model_id="vosk-test",
            repo="example/vosk-model",
            model_path=tmp_path,
            device="cpu",
            max_frame_bytes=2,
        )
        session = await backend.start_stream(_config())
        with pytest.raises(StreamingProtocolError, match=message):
            await session.feed(frame)
        assert fake_vosk.recognizers[0].accepted_frames == []
        await session.cancel()

    asyncio.run(scenario())


def test_finalize_and_cleanup_are_idempotent(
    fake_vosk: _FakeVosk,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        session = await backend.start_stream(_config())
        recognizer = fake_vosk.recognizers[0]
        recognizer.final_document = {"text": "finished", "result": []}
        first = await session.finalize()
        second = await session.finalize()
        await session.cancel()
        await session.close()
        assert first is second
        assert first.event_type == EVENT_FINAL
        assert first.text == "finished"
        assert recognizer.final_calls == 1
        assert backend.active_streams == 0

    asyncio.run(scenario())


def test_active_stream_prevents_unload(
    fake_vosk: _FakeVosk,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path)
        session = await backend.start_stream(_config())
        with pytest.raises(RuntimeError, match="active stream"):
            await backend.unload()
        await session.cancel()
        await backend.unload()
        assert not backend.loaded()

    asyncio.run(scenario())


def test_closed_session_rejects_more_audio(
    fake_vosk: _FakeVosk,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        session = await _backend(tmp_path).start_stream(_config())
        await session.cancel()
        with pytest.raises(RuntimeError, match="closed"):
            await session.feed(b"\x00\x00")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "document",
    [
        "{bad-json",
        "[]",
        json.dumps({"text": 4}),
        json.dumps({"text": "bad", "result": "not-a-list"}),
    ],
)
def test_malformed_result_is_rejected(document: str) -> None:
    with pytest.raises(RuntimeError, match="malformed|non-object|invalid"):
        _parse_result_json(document, "result")


@pytest.mark.parametrize(
    "word",
    [
        {"word": "bad", "start": 1.0, "end": 0.5, "conf": 0.5},
        {"word": "bad", "start": 0.0, "end": 0.5, "conf": float("nan")},
        {"word": "bad", "start": 0.0, "end": 0.5, "conf": 1.5},
        {"word": 4, "start": 0.0, "end": 0.5, "conf": 0.5},
    ],
)
def test_invalid_word_is_rejected(word: dict[str, Any]) -> None:
    document = json.dumps({"text": "bad", "result": [word]})
    with pytest.raises(RuntimeError, match="invalid|non-finite"):
        _parse_result_json(document, "result")

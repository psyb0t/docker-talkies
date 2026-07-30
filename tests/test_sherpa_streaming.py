"""Unit tests for the optional Sherpa-ONNX streaming adapter."""

from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from talkies.asr_streaming import StreamConfig

# The dev image excludes ML dependencies imported by the eager backend factory.
_MODELS_PACKAGE = types.ModuleType("talkies.models")
_MODELS_PACKAGE.__path__ = [
    str(Path(__file__).resolve().parents[1] / "src" / "talkies" / "models")
]
sys.modules["talkies.models"] = _MODELS_PACKAGE

from talkies.models.sherpa import SherpaBackend, _result_text  # noqa: E402


@dataclass
class FakeResult:
    text: object = ""
    tokens: object = field(default_factory=list)
    timestamps: object = field(default_factory=list)


class FakeStream:
    def __init__(self) -> None:
        self.accepted: list[tuple[int, list[float]]] = []
        self.finished_count = 0

    def accept_waveform(self, sample_rate: int, samples: object) -> None:
        self.accepted.append((sample_rate, list(samples)))

    def input_finished(self) -> None:
        self.finished_count += 1


class FakeRecognizer:
    def __init__(self) -> None:
        self.streams: list[FakeStream] = []
        self.result: object = FakeResult()
        self.ready_steps = 0
        self.decode_count = 0
        self.endpoint = False
        self.reset_count = 0

    def create_stream(self) -> FakeStream:
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def is_ready(self, stream: FakeStream) -> bool:
        del stream
        return self.ready_steps > 0

    def decode_stream(self, stream: FakeStream) -> None:
        del stream
        self.ready_steps -= 1
        self.decode_count += 1

    def get_result(self, stream: FakeStream) -> object:
        del stream
        return self.result

    def is_endpoint(self, stream: FakeStream) -> bool:
        del stream
        return self.endpoint

    def reset(self, stream: FakeStream) -> None:
        del stream
        self.endpoint = False
        self.reset_count += 1


class FakeOnlineRecognizer:
    recognizer = FakeRecognizer()
    calls: list[dict[str, object]] = []

    @classmethod
    def from_transducer(cls, **config: object) -> FakeRecognizer:
        cls.calls.append(config)
        return cls.recognizer


def _backend(
    recognizer: FakeRecognizer | None = None,
    device: str = "cpu",
) -> tuple[SherpaBackend, SimpleNamespace]:
    FakeOnlineRecognizer.recognizer = recognizer or FakeRecognizer()
    FakeOnlineRecognizer.calls = []
    module = SimpleNamespace(OnlineRecognizer=FakeOnlineRecognizer)
    backend = SherpaBackend(
        model_id="sherpa-test",
        repo="example/sherpa-test",
        recognizer_config={
            "tokens": "/models/tokens.txt",
            "encoder": "/models/encoder.onnx",
            "decoder": "/models/decoder.onnx",
            "joiner": "/models/joiner.onnx",
        },
        device=device,
    )
    return backend, module


def _config(word_timestamps: bool = False) -> StreamConfig:
    return StreamConfig(
        model="sherpa-test",
        word_timestamps=word_timestamps,
    )


def test_dependency_is_lazy_and_load_config_is_safe() -> None:
    backend, module = _backend(device="cuda:0")

    with patch(
        "talkies.models.sherpa.importlib.import_module",
        return_value=module,
    ) as import_module:
        assert backend.loaded() is False
        assert import_module.call_count == 0

        session = asyncio.run(backend.start_stream(_config()))

    assert backend.loaded() is True
    assert backend.active_streams == 1
    assert import_module.call_args.args == ("sherpa_onnx",)
    assert FakeOnlineRecognizer.calls[0]["provider"] == "cuda"
    assert FakeOnlineRecognizer.calls[0]["enable_endpoint_detection"] is True
    asyncio.run(session.cancel())
    assert backend.active_streams == 0


def test_feed_decodes_ready_frames_and_emits_revisioned_partial() -> None:
    recognizer = FakeRecognizer()
    recognizer.result = FakeResult(
        text=" hello ",
        tokens=["hello", "world"],
        timestamps=[0.0, 0.01],
    )
    recognizer.ready_steps = 3
    backend, module = _backend(recognizer)

    with patch(
        "talkies.models.sherpa.importlib.import_module",
        return_value=module,
    ):
        session = asyncio.run(backend.start_stream(_config(word_timestamps=True)))

    pcm = b"\x00\x80\x00\x00\xff\x7f"
    events = asyncio.run(session.feed(pcm))

    assert recognizer.decode_count == 3
    assert recognizer.streams[0].accepted[0][0] == 16_000
    assert recognizer.streams[0].accepted[0][1] == pytest.approx(
        [-1.0, 0.0, 32767 / 32768]
    )
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "partial"
    assert event.revision == 1
    assert event.text == "hello"
    assert event.audio_seconds == pytest.approx(3 / 16_000)
    assert event.words == [
        {
            "word": "hello",
            "start": 0.0,
            "end": pytest.approx(3 / 16_000),
        },
    ]

    assert asyncio.run(session.feed(pcm)) == []
    asyncio.run(session.close())


def test_feed_accepts_the_runtime_string_result_shape() -> None:
    recognizer = FakeRecognizer()
    recognizer.result = " runtime text "
    recognizer.ready_steps = 1
    backend, module = _backend(recognizer)

    with patch(
        "talkies.models.sherpa.importlib.import_module",
        return_value=module,
    ):
        session = asyncio.run(backend.start_stream(_config()))

    events = asyncio.run(session.feed(b"\x00\x00"))

    assert [event.text for event in events] == ["runtime text"]
    asyncio.run(session.close())


def test_result_text_rejects_unknown_runtime_result_shape() -> None:
    assert _result_text(object()) == ""


def test_endpoint_resets_stream_and_finalization_releases_once() -> None:
    recognizer = FakeRecognizer()
    recognizer.result = FakeResult(text="first", tokens=["first"], timestamps=[0.0])
    recognizer.endpoint = True
    backend, module = _backend(recognizer)

    with patch(
        "talkies.models.sherpa.importlib.import_module",
        return_value=module,
    ):
        session = asyncio.run(backend.start_stream(_config(word_timestamps=True)))

    endpoint_events = asyncio.run(session.feed(b"\x00\x00" * 160))
    assert endpoint_events[0].event_type == "endpoint"
    assert endpoint_events[0].is_final is False
    assert endpoint_events[0].revision == 1
    assert recognizer.reset_count == 1

    recognizer.result = FakeResult(
        text="second",
        tokens=["second"],
        timestamps=[0.0],
    )
    recognizer.ready_steps = 1
    final_event = asyncio.run(session.finalize())
    repeated_final = asyncio.run(session.finalize())

    assert final_event is repeated_final
    assert final_event.event_type == "final"
    assert final_event.revision == 2
    assert final_event.words[0]["start"] == pytest.approx(0.01)
    assert recognizer.streams[0].finished_count == 1
    assert backend.active_streams == 0
    asyncio.run(session.close())
    assert backend.active_streams == 0


@pytest.mark.parametrize("pcm", [b"", b"\x00"])
def test_feed_rejects_invalid_pcm(pcm: bytes) -> None:
    backend, module = _backend()
    with patch(
        "talkies.models.sherpa.importlib.import_module",
        return_value=module,
    ):
        session = asyncio.run(backend.start_stream(_config()))

    with pytest.raises(ValueError):
        asyncio.run(session.feed(pcm))
    asyncio.run(session.cancel())


def test_cancel_is_idempotent_and_active_stream_blocks_unload() -> None:
    backend, module = _backend()
    with patch(
        "talkies.models.sherpa.importlib.import_module",
        return_value=module,
    ):
        session = asyncio.run(backend.start_stream(_config()))

    with pytest.raises(RuntimeError, match="active stream"):
        asyncio.run(backend.unload())
    asyncio.run(session.cancel())
    asyncio.run(session.cancel())
    asyncio.run(backend.unload())
    assert backend.loaded() is False

    with pytest.raises(RuntimeError, match="closed"):
        asyncio.run(session.feed(b"\x00\x00"))


def test_missing_optional_dependency_preserves_cause() -> None:
    backend, _ = _backend()
    missing = ModuleNotFoundError("No module named 'sherpa_onnx'")

    with patch(
        "talkies.models.sherpa.importlib.import_module",
        side_effect=missing,
    ):
        with pytest.raises(RuntimeError, match="optional sherpa-onnx") as raised:
            asyncio.run(backend.get_model())

    assert raised.value.__cause__ is missing

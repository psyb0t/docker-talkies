"""Unit tests for file transcription through streaming ASR sessions."""

from __future__ import annotations

import asyncio
import sys
import types
import wave
from pathlib import Path

import pytest

from talkies.asr_streaming import EVENT_ENDPOINT, EVENT_FINAL, TranscriptEvent

_MODELS_PACKAGE = types.ModuleType("talkies.models")
_MODELS_PACKAGE.__path__ = [
    str(Path(__file__).resolve().parents[1] / "src" / "talkies" / "models")
]
sys.modules["talkies.models"] = _MODELS_PACKAGE

from talkies.models.stream_batch import transcribe_wav_via_stream  # noqa: E402

_PCM = b"\x00\x00" * 10


class _FakeSession:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.closed = False

    async def feed(self, pcm_s16le: bytes) -> list[TranscriptEvent]:
        self.frames.append(pcm_s16le)
        if len(self.frames) != 1:
            return []
        return [
            TranscriptEvent(
                event_type=EVENT_ENDPOINT,
                revision=1,
                text="first phrase",
                words=[{"word": "first", "start": 0.0, "end": 0.1}],
                audio_seconds=0.1,
            )
        ]

    async def finalize(self) -> TranscriptEvent:
        return TranscriptEvent(
            event_type=EVENT_FINAL,
            revision=2,
            text="second phrase",
            words=[{"word": "second", "start": 0.1, "end": 0.2}],
            audio_seconds=0.2,
            is_final=True,
        )

    async def close(self) -> None:
        self.closed = True


class _FakeBackend:
    model_id = "fake-stream-model"

    def __init__(self) -> None:
        self.session = _FakeSession()
        self.config = None

    async def start_stream(self, config):
        self.config = config
        return self.session


def _write_wav(path: Path, pcm: bytes = _PCM) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(pcm)


def test_batch_transcription_splits_pcm_and_aggregates_endpoints(
    tmp_path: Path,
) -> None:
    wav_path = tmp_path / "input.wav"
    _write_wav(wav_path)
    backend = _FakeBackend()

    result = asyncio.run(
        transcribe_wav_via_stream(
            backend,
            str(wav_path),
            source_lang="en",
            with_timestamps=True,
            max_frame_bytes=8,
        )
    )

    assert backend.config.model == backend.model_id
    assert backend.config.language == "en"
    assert backend.config.interim_results is False
    assert backend.config.word_timestamps is True
    assert backend.session.frames == [_PCM[:8], _PCM[8:16], _PCM[16:]]
    assert backend.session.closed
    assert result.text == "first phrase second phrase"
    assert result.language == "en"
    assert result.supports_timestamps
    assert result.segments == [
        {"id": 0, "start": 0.0, "end": 0.1, "text": "first phrase"},
        {"id": 1, "start": 0.1, "end": 0.2, "text": "second phrase"},
    ]
    assert [word["word"] for word in result.words] == ["first", "second"]


def test_batch_transcription_omits_timestamp_data_when_not_requested(
    tmp_path: Path,
) -> None:
    wav_path = tmp_path / "input.wav"
    _write_wav(wav_path)

    result = asyncio.run(
        transcribe_wav_via_stream(
            _FakeBackend(),
            str(wav_path),
            source_lang=None,
            with_timestamps=False,
            max_frame_bytes=8,
        )
    )

    assert result.text == "first phrase second phrase"
    assert result.segments == []
    assert result.words == []
    assert result.supports_timestamps is False


def test_batch_transcription_rejects_non_normalized_wav(tmp_path: Path) -> None:
    wav_path = tmp_path / "stereo.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(_PCM)

    with pytest.raises(RuntimeError, match="normalized 16 kHz mono PCM WAV"):
        asyncio.run(
            transcribe_wav_via_stream(
                _FakeBackend(),
                str(wav_path),
                source_lang=None,
                with_timestamps=False,
                max_frame_bytes=8,
            )
        )


def test_batch_transcription_rejects_odd_frame_limit(tmp_path: Path) -> None:
    wav_path = tmp_path / "input.wav"
    _write_wav(wav_path)

    with pytest.raises(ValueError, match="multiple of int16 bytes"):
        asyncio.run(
            transcribe_wav_via_stream(
                _FakeBackend(),
                str(wav_path),
                source_lang=None,
                with_timestamps=False,
                max_frame_bytes=3,
            )
        )

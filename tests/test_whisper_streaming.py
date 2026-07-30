"""Unit tests for rolling-window Whisper pseudo-streaming."""

from __future__ import annotations

import asyncio
import sys
import types
import wave
from pathlib import Path

import pytest

from talkies.asr_streaming import StreamClosedError, StreamConfig

_MODELS_PACKAGE = types.ModuleType("talkies.models")
_MODELS_PACKAGE.__path__ = [
    str(Path(__file__).resolve().parents[1] / "src" / "talkies" / "models")
]
sys.modules["talkies.models"] = _MODELS_PACKAGE

from talkies.models.base import TranscribeResult  # noqa: E402
from talkies.models.whisper_stream import WhisperStreamingAdapter  # noqa: E402

_FRAME_SECONDS = 0.25
_SAMPLE_RATE = 16_000
_FRAME = b"\x00\x00" * int(_SAMPLE_RATE * _FRAME_SECONDS)


class FakeWhisperBackend:
    def __init__(self, results: list[TranscribeResult | Exception]) -> None:
        self._results = iter(results)
        self.calls: list[dict[str, object]] = []
        self.model_id = "whisper-test"
        self.repo = "example/whisper-test"
        self.unload_calls = 0

    async def get_model(self) -> object:
        return self

    async def unload(self) -> None:
        self.unload_calls += 1

    def loaded(self) -> bool:
        return True

    def last_used_secs_ago(self) -> float:
        return 1.0

    async def transcribe(
        self,
        audio_path: str,
        *,
        source_lang: str | None,
        target_lang: str | None,
        task: str,
        with_timestamps: bool = False,
    ) -> TranscribeResult:
        path = Path(audio_path)
        with wave.open(str(path), "rb") as wav_file:
            self.calls.append(
                {
                    "path": path,
                    "channels": wav_file.getnchannels(),
                    "sample_width": wav_file.getsampwidth(),
                    "sample_rate": wav_file.getframerate(),
                    "frames": wav_file.getnframes(),
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                    "task": task,
                    "with_timestamps": with_timestamps,
                }
            )
        result = next(self._results)
        if isinstance(result, Exception):
            raise result
        return result


def _config(
    *,
    interim_results: bool = True,
    word_timestamps: bool = False,
    language: str | None = None,
) -> StreamConfig:
    return StreamConfig(
        model="whisper-test",
        interim_results=interim_results,
        word_timestamps=word_timestamps,
        language=language,
    )


def test_decode_cadence_emits_changed_revisions_and_suppresses_duplicates(
    tmp_path,
):
    backend = FakeWhisperBackend(
        [
            TranscribeResult(text="hello wor"),
            TranscribeResult(text="hello world"),
            TranscribeResult(text="hello world"),
        ]
    )
    adapter = WhisperStreamingAdapter(
        backend,
        max_buffer_seconds=2.0,
        decode_interval_seconds=_FRAME_SECONDS,
        temp_directory=tmp_path,
    )
    session = asyncio.run(adapter.start_stream(_config()))

    first = asyncio.run(session.feed(_FRAME))
    second = asyncio.run(session.feed(_FRAME))
    duplicate = asyncio.run(session.feed(_FRAME))

    assert [(event.revision, event.text) for event in first + second] == [
        (1, "hello wor"),
        (2, "hello world"),
    ]
    assert duplicate == []
    assert [call["frames"] for call in backend.calls] == [4_000, 8_000, 12_000]
    assert all(not call["path"].exists() for call in backend.calls)


def test_rolling_window_commits_evicted_words_and_offsets_timestamps(tmp_path):
    backend = FakeWhisperBackend(
        [
            TranscribeResult(
                text="hello world",
                words=[
                    {"word": "hello", "start": 0.0, "end": 0.2},
                    {"word": "world", "start": 0.3, "end": 0.45},
                ],
            ),
            TranscribeResult(
                text="world again",
                words=[
                    {"word": "world", "start": 0.05, "end": 0.2},
                    {"word": "again", "start": 0.25, "end": 0.45},
                ],
            ),
        ]
    )
    adapter = WhisperStreamingAdapter(
        backend,
        max_buffer_seconds=0.5,
        decode_interval_seconds=0.5,
        temp_directory=tmp_path,
    )
    session = asyncio.run(adapter.start_stream(_config(word_timestamps=True)))

    assert asyncio.run(session.feed(_FRAME)) == []
    first = asyncio.run(session.feed(_FRAME))
    assert first[0].text == "hello world"

    assert asyncio.run(session.feed(_FRAME)) == []
    second = asyncio.run(session.feed(_FRAME))

    assert second[0].text == "hello world again"
    assert second[0].audio_seconds == 1.0
    assert second[0].words == [
        {"word": "hello", "start": 0.0, "end": 0.2},
        {"word": "world", "start": 0.3, "end": 0.45},
        {"word": "again", "start": 0.75, "end": 0.95},
    ]
    assert backend.calls[-1]["frames"] == 8_000


def test_rolling_window_retains_stable_prefix_without_word_timestamps(tmp_path):
    backend = FakeWhisperBackend(
        [
            TranscribeResult(text="hello wor"),
            TranscribeResult(text="hello world"),
            TranscribeResult(text="world again"),
        ]
    )
    adapter = WhisperStreamingAdapter(
        backend,
        max_buffer_seconds=0.5,
        decode_interval_seconds=_FRAME_SECONDS,
        temp_directory=tmp_path,
    )
    session = asyncio.run(adapter.start_stream(_config()))

    asyncio.run(session.feed(_FRAME))
    asyncio.run(session.feed(_FRAME))
    after_eviction = asyncio.run(session.feed(_FRAME))

    assert after_eviction[0].text == "hello world again"
    assert after_eviction[0].revision == 3


def test_finalize_is_idempotent_and_cleans_session(tmp_path):
    backend = FakeWhisperBackend([TranscribeResult(text="done")])
    adapter = WhisperStreamingAdapter(
        backend,
        max_buffer_seconds=1.0,
        decode_interval_seconds=1.0,
        temp_directory=tmp_path,
    )
    session = asyncio.run(adapter.start_stream(_config(language="auto")))
    asyncio.run(session.feed(_FRAME))

    first = asyncio.run(session.finalize())
    second = asyncio.run(session.finalize())

    assert first is second
    assert first.to_dict() == {
        "type": "final",
        "revision": 1,
        "text": "done",
        "words": [],
        "audio_seconds": _FRAME_SECONDS,
        "is_final": True,
    }
    assert backend.calls[0]["source_lang"] is None
    with pytest.raises(StreamClosedError):
        asyncio.run(session.feed(_FRAME))


def test_interim_disabled_decodes_without_emitting_partial(tmp_path):
    backend = FakeWhisperBackend(
        [TranscribeResult(text="draft"), TranscribeResult(text="final text")]
    )
    adapter = WhisperStreamingAdapter(
        backend,
        max_buffer_seconds=1.0,
        decode_interval_seconds=_FRAME_SECONDS,
        temp_directory=tmp_path,
    )
    session = asyncio.run(adapter.start_stream(_config(interim_results=False)))

    assert asyncio.run(session.feed(_FRAME)) == []
    final = asyncio.run(session.finalize())

    assert final.text == "final text"
    assert final.revision == 1


def test_cancel_and_close_are_idempotent_without_decoding(tmp_path):
    backend = FakeWhisperBackend([])
    adapter = WhisperStreamingAdapter(
        backend,
        max_buffer_seconds=1.0,
        temp_directory=tmp_path,
    )
    session = asyncio.run(adapter.start_stream(_config()))

    asyncio.run(session.cancel())
    asyncio.run(session.cancel())
    asyncio.run(session.close())

    assert backend.calls == []
    with pytest.raises(StreamClosedError):
        asyncio.run(session.feed(_FRAME))


def test_temp_wav_is_removed_when_backend_fails(tmp_path):
    backend = FakeWhisperBackend([RuntimeError("decode failed")])
    adapter = WhisperStreamingAdapter(
        backend,
        max_buffer_seconds=1.0,
        decode_interval_seconds=_FRAME_SECONDS,
        temp_directory=tmp_path,
    )
    session = asyncio.run(adapter.start_stream(_config()))

    with pytest.raises(RuntimeError, match="decode failed"):
        asyncio.run(session.feed(_FRAME))

    assert len(backend.calls) == 1
    assert not backend.calls[0]["path"].exists()
    asyncio.run(session.close())


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"max_buffer_seconds": 0}, "positive"),
        (
            {"max_buffer_seconds": 1, "decode_interval_seconds": 0},
            "positive",
        ),
        (
            {"max_buffer_seconds": 1, "decode_interval_seconds": 2},
            "must not exceed",
        ),
        ({"max_buffer_seconds": 1, "max_frame_bytes": 1}, "one sample"),
    ],
)
def test_adapter_rejects_invalid_limits(kwargs, match):
    with pytest.raises(ValueError, match=match):
        WhisperStreamingAdapter(FakeWhisperBackend([]), **kwargs)


def test_session_rejects_invalid_pcm_frame(tmp_path):
    adapter = WhisperStreamingAdapter(
        FakeWhisperBackend([]),
        max_buffer_seconds=1.0,
        max_frame_bytes=2,
        temp_directory=tmp_path,
    )
    session = asyncio.run(adapter.start_stream(_config()))

    with pytest.raises(ValueError, match="configured limit"):
        asyncio.run(session.feed(b"\x00\x00\x00\x00"))


def test_adapter_proxies_offline_and_lifecycle_surface(tmp_path):
    backend = FakeWhisperBackend([TranscribeResult(text="offline")])
    adapter = WhisperStreamingAdapter(
        backend,
        max_buffer_seconds=1.0,
        temp_directory=tmp_path,
    )
    audio_path = tmp_path / "input.wav"
    with wave.open(str(audio_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(_SAMPLE_RATE)
        wav_file.writeframes(_FRAME)

    result = asyncio.run(
        adapter.transcribe(
            str(audio_path),
            source_lang="en",
            target_lang=None,
            task="transcribe",
            with_timestamps=False,
        )
    )

    assert result.text == "offline"
    assert adapter.model_id == backend.model_id
    assert adapter.repo == backend.repo
    assert adapter.loaded() is True
    assert adapter.last_used_secs_ago() == 1.0
    assert asyncio.run(adapter.get_model()) is backend
    asyncio.run(adapter.unload())
    assert backend.unload_calls == 1

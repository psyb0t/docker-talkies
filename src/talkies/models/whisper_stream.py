"""Bounded rolling-window streaming adapter for the Whisper backend."""

from __future__ import annotations

import asyncio
import tempfile
import wave
from pathlib import Path
from typing import Protocol

from ..asr_streaming import (
    EVENT_FINAL,
    EVENT_PARTIAL,
    PCM_CHANNELS,
    PCM_SAMPLE_BYTES,
    PCM_SAMPLE_RATE,
    StreamClosedError,
    StreamConfig,
    TranscriptEvent,
    longest_stable_prefix,
    validate_pcm_frame,
)
from .base import TranscribeResult

_DEFAULT_DECODE_INTERVAL_SECONDS = 0.5
_DEFAULT_MAX_FRAME_BYTES = 65_536


class WhisperTranscriber(Protocol):
    """File transcription surface consumed by the rolling adapter."""

    model_id: str
    repo: str

    async def get_model(self) -> object: ...

    async def unload(self) -> None: ...

    def loaded(self) -> bool: ...

    def last_used_secs_ago(self) -> float | None: ...

    async def transcribe(
        self,
        audio_path: str,
        *,
        source_lang: str | None,
        target_lang: str | None,
        task: str,
        with_timestamps: bool = False,
    ) -> TranscribeResult: ...


class WhisperStreamingAdapter:
    """Create isolated pseudo-streaming sessions over a Whisper backend."""

    def __init__(
        self,
        backend: WhisperTranscriber,
        *,
        max_buffer_seconds: float,
        decode_interval_seconds: float = _DEFAULT_DECODE_INTERVAL_SECONDS,
        max_frame_bytes: int = _DEFAULT_MAX_FRAME_BYTES,
        temp_directory: Path | None = None,
    ) -> None:
        if max_buffer_seconds <= 0:
            raise ValueError("max_buffer_seconds must be positive")
        if decode_interval_seconds <= 0:
            raise ValueError("decode_interval_seconds must be positive")
        if decode_interval_seconds > max_buffer_seconds:
            raise ValueError("decode interval must not exceed the rolling window")
        if max_frame_bytes < PCM_SAMPLE_BYTES:
            raise ValueError("max_frame_bytes must fit at least one sample")

        self._backend = backend
        self._max_buffer_bytes = _seconds_to_bytes(max_buffer_seconds)
        self._decode_interval_bytes = _seconds_to_bytes(decode_interval_seconds)
        self._max_frame_bytes = max_frame_bytes
        self._temp_directory = temp_directory

    @property
    def model_id(self) -> str:
        return self._backend.model_id

    @property
    def repo(self) -> str:
        return self._backend.repo

    async def get_model(self) -> object:
        return await self._backend.get_model()

    async def unload(self) -> None:
        await self._backend.unload()

    def loaded(self) -> bool:
        return self._backend.loaded()

    def last_used_secs_ago(self) -> float | None:
        return self._backend.last_used_secs_ago()

    async def transcribe(
        self,
        audio_path: str,
        *,
        source_lang: str | None,
        target_lang: str | None,
        task: str,
        with_timestamps: bool = False,
    ) -> TranscribeResult:
        return await self._backend.transcribe(
            audio_path,
            source_lang=source_lang,
            target_lang=target_lang,
            task=task,
            with_timestamps=with_timestamps,
        )

    async def start_stream(self, config: StreamConfig) -> _WhisperStreamingSession:
        """Start a session without loading the model until audio is decoded."""

        return _WhisperStreamingSession(
            self._backend,
            config=config,
            max_buffer_bytes=self._max_buffer_bytes,
            decode_interval_bytes=self._decode_interval_bytes,
            max_frame_bytes=self._max_frame_bytes,
            temp_directory=self._temp_directory,
        )


class _WhisperStreamingSession:
    def __init__(
        self,
        backend: WhisperTranscriber,
        *,
        config: StreamConfig,
        max_buffer_bytes: int,
        decode_interval_bytes: int,
        max_frame_bytes: int,
        temp_directory: Path | None,
    ) -> None:
        self._backend = backend
        self._config = config
        self._max_buffer_bytes = max_buffer_bytes
        self._decode_interval_bytes = decode_interval_bytes
        self._max_frame_bytes = max_frame_bytes
        self._temp_directory = temp_directory
        self._pcm = bytearray()
        self._accepted_bytes = 0
        self._bytes_since_decode = 0
        self._window_start_seconds = 0.0
        self._revision = 0
        self._committed_text = ""
        self._committed_words: list[dict[str, object]] = []
        self._previous_window_text = ""
        self._previous_words: list[dict[str, object]] = []
        self._stable_prefix = ""
        self._last_emitted_text: str | None = None
        self._final_event: TranscriptEvent | None = None
        self._closed = False
        self._lock = asyncio.Lock()

    async def feed(self, pcm_s16le: bytes) -> list[TranscriptEvent]:
        """Append one frame and decode when the configured cadence elapses."""

        async with self._lock:
            self._require_open()
            validate_pcm_frame(
                pcm_s16le,
                max_frame_bytes=self._max_frame_bytes,
            )
            self._pcm.extend(pcm_s16le)
            self._accepted_bytes += len(pcm_s16le)
            self._bytes_since_decode += len(pcm_s16le)
            self._trim_window()
            if self._bytes_since_decode < self._decode_interval_bytes:
                return []

            result = await self._decode_window()
            self._bytes_since_decode = 0
            if not self._config.interim_results:
                self._reconcile_result(result)
                return []
            event = self._partial_event(result)
            return [] if event is None else [event]

    async def finalize(self) -> TranscriptEvent:
        """Decode the last window and return one idempotent final revision."""

        async with self._lock:
            if self._final_event is not None:
                return self._final_event
            self._require_open()
            result = await self._decode_window()
            text, words = self._reconcile_result(result)
            self._revision += 1
            self._final_event = TranscriptEvent(
                event_type=EVENT_FINAL,
                revision=self._revision,
                text=text,
                words=words if self._config.word_timestamps else [],
                audio_seconds=self.audio_seconds,
                is_final=True,
            )
            self._closed = True
            self._clear_audio()
            return self._final_event

    async def cancel(self) -> None:
        """Discard buffered audio without decoding it."""

        await self.close()

    async def close(self) -> None:
        """Release session memory once; repeated cleanup is safe."""

        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._clear_audio()

    @property
    def audio_seconds(self) -> float:
        return self._accepted_bytes / (PCM_SAMPLE_RATE * PCM_SAMPLE_BYTES)

    def _require_open(self) -> None:
        if self._closed:
            raise StreamClosedError("stream is closed")

    def _trim_window(self) -> None:
        overflow_bytes = len(self._pcm) - self._max_buffer_bytes
        if overflow_bytes <= 0:
            return
        overflow_bytes -= overflow_bytes % PCM_SAMPLE_BYTES
        if overflow_bytes <= 0:
            return

        shift_seconds = overflow_bytes / (PCM_SAMPLE_RATE * PCM_SAMPLE_BYTES)
        self._commit_evicted_audio(shift_seconds)
        del self._pcm[:overflow_bytes]
        self._window_start_seconds += shift_seconds

    def _commit_evicted_audio(self, shift_seconds: float) -> None:
        evicted_words: list[dict[str, object]] = []
        remaining_words: list[dict[str, object]] = []
        for word in self._previous_words:
            end = _finite_float(word.get("end"))
            if end is not None and end <= shift_seconds:
                evicted_words.append(word)
                continue
            shifted = dict(word)
            start = _finite_float(shifted.get("start"))
            if start is not None:
                shifted["start"] = max(0.0, start - shift_seconds)
            if end is not None:
                shifted["end"] = max(0.0, end - shift_seconds)
            remaining_words.append(shifted)

        if evicted_words:
            evicted_text = " ".join(_word_text(word) for word in evicted_words).strip()
            self._committed_text = _merge_text(self._committed_text, evicted_text)
            self._committed_words.extend(
                _offset_words(evicted_words, self._window_start_seconds)
            )
            self._previous_window_text = " ".join(
                _word_text(word) for word in remaining_words
            ).strip()
            self._previous_words = remaining_words
            self._stable_prefix = ""
            return

        if not self._stable_prefix:
            return
        self._committed_text = _merge_text(
            self._committed_text,
            self._stable_prefix.strip(),
        )
        self._previous_window_text = _remove_prefix(
            self._previous_window_text,
            self._stable_prefix,
        )
        self._stable_prefix = ""

    async def _decode_window(self) -> TranscribeResult:
        if not self._pcm:
            return TranscribeResult(text="", supports_timestamps=True)

        audio_path = self._write_temp_wav()
        try:
            language = (
                None if self._config.language == "auto" else self._config.language
            )
            return await self._backend.transcribe(
                str(audio_path),
                source_lang=language,
                target_lang=None,
                task="transcribe",
                with_timestamps=True,
            )
        finally:
            audio_path.unlink(missing_ok=True)

    def _write_temp_wav(self) -> Path:
        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            dir=self._temp_directory,
            delete=False,
        ) as audio_file:
            audio_path = Path(audio_file.name)
        try:
            with wave.open(str(audio_path), "wb") as wav_file:
                wav_file.setnchannels(PCM_CHANNELS)
                wav_file.setsampwidth(PCM_SAMPLE_BYTES)
                wav_file.setframerate(PCM_SAMPLE_RATE)
                wav_file.writeframes(self._pcm)
        except (OSError, wave.Error):
            audio_path.unlink(missing_ok=True)
            raise
        return audio_path

    def _partial_event(self, result: TranscribeResult) -> TranscriptEvent | None:
        text, words = self._reconcile_result(result)
        if text == self._last_emitted_text:
            return None
        self._last_emitted_text = text
        self._revision += 1
        return TranscriptEvent(
            event_type=EVENT_PARTIAL,
            revision=self._revision,
            text=text,
            words=words if self._config.word_timestamps else [],
            audio_seconds=self.audio_seconds,
        )

    def _reconcile_result(
        self,
        result: TranscribeResult,
    ) -> tuple[str, list[dict[str, object]]]:
        window_text = result.text.strip()
        self._stable_prefix = longest_stable_prefix(
            self._previous_window_text,
            window_text,
        )
        self._previous_window_text = window_text
        self._previous_words = [dict(word) for word in result.words]
        text = _merge_text(self._committed_text, window_text)
        current_words = _offset_words(
            self._previous_words,
            self._window_start_seconds,
        )
        return text, _merge_words(self._committed_words, current_words)

    def _clear_audio(self) -> None:
        self._pcm.clear()
        self._previous_words.clear()


def _seconds_to_bytes(seconds: float) -> int:
    byte_count = int(seconds * PCM_SAMPLE_RATE * PCM_SAMPLE_BYTES)
    byte_count -= byte_count % PCM_SAMPLE_BYTES
    if byte_count < PCM_SAMPLE_BYTES:
        raise ValueError("duration must fit at least one PCM sample")
    return byte_count


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    if converted != converted or converted in (float("inf"), float("-inf")):
        return None
    return converted


def _word_text(word: dict[str, object]) -> str:
    value = word.get("word", "")
    return value.strip() if isinstance(value, str) else ""


def _offset_words(
    words: list[dict[str, object]],
    offset_seconds: float,
) -> list[dict[str, object]]:
    adjusted_words: list[dict[str, object]] = []
    for word in words:
        adjusted = dict(word)
        for field_name in ("start", "end"):
            timestamp = _finite_float(adjusted.get(field_name))
            if timestamp is not None:
                adjusted[field_name] = timestamp + offset_seconds
        adjusted_words.append(adjusted)
    return adjusted_words


def _merge_text(committed: str, current: str) -> str:
    committed_words = committed.split()
    current_words = current.split()
    overlap = _overlap_size(committed_words, current_words)
    return " ".join(committed_words + current_words[overlap:])


def _merge_words(
    committed: list[dict[str, object]],
    current: list[dict[str, object]],
) -> list[dict[str, object]]:
    committed_text = [_word_text(word) for word in committed]
    current_text = [_word_text(word) for word in current]
    overlap = _overlap_size(committed_text, current_text)
    return [dict(word) for word in committed + current[overlap:]]


def _overlap_size(previous: list[str], current: list[str]) -> int:
    overlap = min(len(previous), len(current))
    while overlap > 0:
        if previous[-overlap:] == current[:overlap]:
            return overlap
        overlap -= 1
    return 0


def _remove_prefix(text: str, prefix: str) -> str:
    if text.startswith(prefix):
        return text[len(prefix) :].strip()
    return text

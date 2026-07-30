"""Batch-file transcription through a native streaming ASR backend."""

from __future__ import annotations

import asyncio
import math
import wave
from pathlib import Path
from typing import Protocol

from ..asr_streaming import (
    EVENT_FINAL,
    PCM_SAMPLE_BYTES,
    PCM_SAMPLE_RATE,
    StreamConfig,
    TranscriptEvent,
)
from .base import TranscribeResult


class StreamingBackend(Protocol):
    model_id: str

    async def start_stream(self, config: StreamConfig) -> "StreamingSession": ...


class StreamingSession(Protocol):
    async def feed(self, pcm_s16le: bytes) -> list[TranscriptEvent]: ...

    async def finalize(self) -> TranscriptEvent: ...

    async def close(self) -> None: ...


async def transcribe_wav_via_stream(
    backend: StreamingBackend,
    audio_path: str,
    *,
    source_lang: str | None,
    with_timestamps: bool,
    max_frame_bytes: int,
) -> TranscribeResult:
    """Decode Talkies-normalized WAV audio using an isolated stream session."""
    if max_frame_bytes < PCM_SAMPLE_BYTES or max_frame_bytes % PCM_SAMPLE_BYTES:
        raise ValueError("batch frame limit must be a positive multiple of int16 bytes")

    pcm_s16le = await asyncio.to_thread(_read_normalized_wav, audio_path)
    session = await backend.start_stream(
        StreamConfig(
            model=backend.model_id,
            language=source_lang,
            interim_results=False,
            word_timestamps=with_timestamps,
        )
    )
    events: list[TranscriptEvent] = []
    try:
        for offset in range(0, len(pcm_s16le), max_frame_bytes):
            frame = pcm_s16le[offset : offset + max_frame_bytes]
            events.extend(await session.feed(frame))
        events.append(await session.finalize())
    finally:
        await session.close()
    return _events_to_result(
        events,
        source_lang=source_lang,
        with_timestamps=with_timestamps,
    )


def _read_normalized_wav(audio_path: str) -> bytes:
    try:
        with wave.open(str(Path(audio_path)), "rb") as wav_file:
            if (
                wav_file.getnchannels() != 1
                or wav_file.getsampwidth() != PCM_SAMPLE_BYTES
                or wav_file.getframerate() != PCM_SAMPLE_RATE
            ):
                raise RuntimeError(
                    "batch ASR requires normalized 16 kHz mono PCM WAV audio"
                )
            pcm_s16le = wav_file.readframes(wav_file.getnframes())
    except wave.Error as err:
        raise RuntimeError(
            "normalized batch ASR audio is not a readable WAV file"
        ) from err
    if len(pcm_s16le) % PCM_SAMPLE_BYTES:
        raise RuntimeError(
            "normalized batch ASR audio contains an incomplete int16 sample"
        )
    return pcm_s16le


def _events_to_result(
    events: list[TranscriptEvent],
    *,
    source_lang: str | None,
    with_timestamps: bool,
) -> TranscribeResult:
    text_parts: list[str] = []
    segments: list[dict[str, float | int | str]] = []
    words: list[dict[str, object]] = []
    previous_end = 0.0
    previous_text = ""
    previous_words: list[dict[str, object]] = []

    for event in events:
        event_words = [dict(word) for word in event.words]
        if not event.text and not event_words:
            continue
        if (
            event.event_type == EVENT_FINAL
            and event.text == previous_text
            and event_words == previous_words
        ):
            continue

        if event.text:
            start = _event_start(event_words, previous_end)
            end = max(event.audio_seconds, _event_end(event_words, start))
            segments.append(
                {
                    "id": len(segments),
                    "start": start,
                    "end": end,
                    "text": event.text,
                }
            )
            text_parts.append(event.text)
            previous_end = end
        words.extend(event_words)
        previous_text = event.text
        previous_words = event_words

    return TranscribeResult(
        text=" ".join(text_parts).strip(),
        segments=segments if with_timestamps else [],
        words=words if with_timestamps else [],
        language=source_lang,
        supports_timestamps=with_timestamps and bool(words),
    )


def _event_start(words: list[dict[str, object]], fallback: float) -> float:
    valid_starts = [
        timestamp
        for word in words
        if (timestamp := _finite_timestamp(word.get("start"))) is not None
    ]
    return min(valid_starts) if valid_starts else fallback


def _event_end(words: list[dict[str, object]], fallback: float) -> float:
    valid_ends = [
        timestamp
        for word in words
        if (timestamp := _finite_timestamp(word.get("end"))) is not None
    ]
    return max(valid_ends) if valid_ends else fallback


def _finite_timestamp(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None

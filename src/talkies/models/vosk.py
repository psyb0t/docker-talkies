"""Vosk native streaming ASR adapter."""

from __future__ import annotations

import asyncio
import gc
import importlib
import json
import math
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# isort: off
from ..asr_streaming import (
    EVENT_ENDPOINT,
    EVENT_FINAL,
    EVENT_PARTIAL,
    PCM_SAMPLE_BYTES,
    StreamConfig,
    StreamingASRSession,
    TranscriptEvent,
    validate_pcm_frame,
)

# isort: on

_DEFAULT_MAX_FRAME_BYTES = 65_536


class VoskBackend:
    """Share lazily loaded Vosk model weights across decoder sessions."""

    def __init__(
        self,
        model_id: str,
        repo: str,
        model_path: Path,
        device: str,
        max_frame_bytes: int = _DEFAULT_MAX_FRAME_BYTES,
    ) -> None:
        if max_frame_bytes < PCM_SAMPLE_BYTES:
            raise ValueError("Vosk frame limit must allow at least one PCM16 sample")
        self.model_id = model_id
        self.repo = repo
        self.model_path = model_path
        self._device = device
        self._max_frame_bytes = max_frame_bytes
        self._model: Any | None = None
        self._lock = asyncio.Lock()
        self._active_streams = 0
        self._last_used: float | None = None

    def loaded(self) -> bool:
        return self._model is not None

    def last_used_secs_ago(self) -> float | None:
        if self._last_used is None:
            return None
        return time.monotonic() - self._last_used

    @property
    def active_streams(self) -> int:
        return self._active_streams

    async def get_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_sync)
            return self._model

    def _load_sync(self) -> Any:
        if not self.model_path.is_dir():
            raise FileNotFoundError(
                f"Vosk model directory is missing at {self.model_path}"
            )
        try:
            vosk = importlib.import_module("vosk")
        except ModuleNotFoundError as err:
            raise RuntimeError(
                "Vosk streaming requires the optional vosk package"
            ) from err
        model_factory = getattr(vosk, "Model", None)
        if not callable(model_factory):
            raise RuntimeError("installed vosk package does not provide Model")
        return model_factory(str(self.model_path))

    async def start_stream(self, config: StreamConfig) -> StreamingASRSession:
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_sync)
            recognizer = await asyncio.to_thread(
                self._create_recognizer,
                self._model,
                config.sample_rate,
            )
            self._active_streams += 1
            self._last_used = time.monotonic()
        return VoskStreamingSession(
            recognizer=recognizer,
            config=config,
            max_frame_bytes=self._max_frame_bytes,
            release=self._release_stream,
        )

    def _create_recognizer(self, model: Any, sample_rate: int) -> Any:
        vosk = importlib.import_module("vosk")
        recognizer_factory = getattr(vosk, "KaldiRecognizer", None)
        if not callable(recognizer_factory):
            raise RuntimeError(
                "installed vosk package does not provide KaldiRecognizer"
            )
        recognizer = recognizer_factory(model, sample_rate)
        set_words = getattr(recognizer, "SetWords", None)
        if not callable(set_words):
            raise RuntimeError("installed Vosk recognizer lacks word output")
        set_words(True)
        set_partial_words = getattr(recognizer, "SetPartialWords", None)
        if callable(set_partial_words):
            set_partial_words(True)
        return recognizer

    async def _release_stream(self) -> None:
        async with self._lock:
            if self._active_streams > 0:
                self._active_streams -= 1
            self._last_used = time.monotonic()

    async def unload(self) -> None:
        async with self._lock:
            if self._active_streams:
                raise RuntimeError(
                    f"cannot unload Vosk backend with {self._active_streams} "
                    "active stream(s)"
                )
            self._model = None
            self._last_used = None
        await asyncio.to_thread(gc.collect)


class VoskStreamingSession:
    """Decode one bounded PCM16LE stream with exactly-once cleanup."""

    def __init__(
        self,
        recognizer: Any,
        config: StreamConfig,
        max_frame_bytes: int,
        release: Callable[[], Awaitable[None]],
    ) -> None:
        self._recognizer: Any | None = recognizer
        self._config = config
        self._max_frame_bytes = max_frame_bytes
        self._release = release
        self._accepted_samples = 0
        self._last_partial = ""
        self._revision = 0
        self._final_event: TranscriptEvent | None = None
        self._released = False
        self._operation_lock = asyncio.Lock()

    async def feed(self, pcm_s16le: bytes) -> list[TranscriptEvent]:
        sample_count = validate_pcm_frame(
            pcm_s16le,
            max_frame_bytes=self._max_frame_bytes,
        )
        async with self._operation_lock:
            recognizer = self._require_open()
            self._accepted_samples += sample_count
            return await asyncio.to_thread(
                self._feed_sync,
                recognizer,
                pcm_s16le,
            )

    def _feed_sync(
        self,
        recognizer: Any,
        pcm_s16le: bytes,
    ) -> list[TranscriptEvent]:
        if recognizer.AcceptWaveform(pcm_s16le):
            payload = _parse_result_json(recognizer.Result(), "result")
            self._last_partial = ""
            return [self._event(EVENT_ENDPOINT, payload)]

        if not self._config.interim_results:
            return []
        payload = _parse_result_json(recognizer.PartialResult(), "partial result")
        text = payload["text"]
        if not text or text == self._last_partial:
            return []
        self._last_partial = text
        return [self._event(EVENT_PARTIAL, payload)]

    async def finalize(self) -> TranscriptEvent:
        async with self._operation_lock:
            if self._final_event is not None:
                return self._final_event
            recognizer = self._require_open()
            try:
                payload = await asyncio.to_thread(
                    _parse_result_json,
                    recognizer.FinalResult(),
                    "final result",
                )
                self._final_event = self._event(EVENT_FINAL, payload)
                return self._final_event
            finally:
                await self._close_once()

    async def cancel(self) -> None:
        await self.close()

    async def close(self) -> None:
        async with self._operation_lock:
            await self._close_once()

    async def _close_once(self) -> None:
        if self._recognizer is None:
            return
        self._recognizer = None
        if self._released:
            return
        self._released = True
        await self._release()

    def _require_open(self) -> Any:
        if self._recognizer is None:
            raise RuntimeError("Vosk streaming session is closed")
        return self._recognizer

    @property
    def audio_seconds(self) -> float:
        return self._accepted_samples / self._config.sample_rate

    def _event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> TranscriptEvent:
        self._revision += 1
        words = payload["words"] if self._config.word_timestamps else []
        return TranscriptEvent(
            event_type=event_type,
            revision=self._revision,
            text=payload["text"],
            words=words,
            audio_seconds=self.audio_seconds,
            is_final=event_type == EVENT_FINAL,
        )


def _parse_result_json(document: str, operation: str) -> dict[str, Any]:
    try:
        parsed = json.loads(document)
    except (json.JSONDecodeError, TypeError) as err:
        raise RuntimeError(f"Vosk {operation} returned malformed JSON") from err
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Vosk {operation} returned a non-object JSON document")

    text_key = "partial" if "partial" in parsed else "text"
    words_key = "partial_result" if text_key == "partial" else "result"
    text = parsed.get(text_key, "")
    if not isinstance(text, str):
        raise RuntimeError(f"Vosk {operation} returned invalid text")
    raw_words = parsed.get(words_key, [])
    if not isinstance(raw_words, list):
        raise RuntimeError(f"Vosk {operation} returned invalid words")
    words = [_parse_word(word, operation) for word in raw_words]
    return {"text": text.strip(), "words": words}


def _parse_word(raw_word: Any, operation: str) -> dict[str, Any]:
    if not isinstance(raw_word, dict):
        raise RuntimeError(f"Vosk {operation} returned an invalid word")
    word = raw_word.get("word", "")
    if not isinstance(word, str):
        raise RuntimeError(f"Vosk {operation} returned invalid word text")
    start = _finite_number(raw_word.get("start"), "word start", operation)
    end = _finite_number(raw_word.get("end"), "word end", operation)
    confidence = _finite_number(
        raw_word.get("conf"),
        "word confidence",
        operation,
    )
    if start < 0 or end < start:
        raise RuntimeError(f"Vosk {operation} returned invalid word timestamps")
    if confidence < 0 or confidence > 1:
        raise RuntimeError(f"Vosk {operation} returned invalid word confidence")
    return {
        "word": word,
        "start": start,
        "end": end,
        "confidence": confidence,
    }


def _finite_number(value: Any, field: str, operation: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"Vosk {operation} returned invalid {field}")
    try:
        result = float(value)
    except (TypeError, ValueError) as err:
        raise RuntimeError(f"Vosk {operation} returned invalid {field}") from err
    if not math.isfinite(result):
        raise RuntimeError(f"Vosk {operation} returned non-finite {field}")
    return result

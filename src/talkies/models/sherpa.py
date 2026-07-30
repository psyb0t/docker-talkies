"""Sherpa-ONNX native streaming ASR adapter."""

from __future__ import annotations

import asyncio
import gc
import importlib
import logging
import math
import sys
import time
from array import array
from collections.abc import Mapping
from typing import Any, Callable

from ..asr_streaming import StreamConfig, StreamingASRSession, TranscriptEvent
from .base import TranscribeResult
from .stream_batch import transcribe_wav_via_stream

_SUPPORTED_FACTORIES = frozenset(
    {
        "from_paraformer",
        "from_transducer",
        "from_wenet_ctc",
        "from_zipformer2_ctc",
    }
)
_MAX_DECODE_STEPS_PER_CALL = 10_000
_PCM16_SCALE = 32_768.0
_MAX_BATCH_FRAME_BYTES = 65_536

logger = logging.getLogger(__name__)


class SherpaBackend:
    """Share lazy-loaded recognizer weights across isolated decoder streams."""

    def __init__(
        self,
        model_id: str,
        repo: str,
        recognizer_config: Mapping[str, Any],
        device: str,
        recognizer_factory: str = "from_transducer",
    ) -> None:
        if recognizer_factory not in _SUPPORTED_FACTORIES:
            raise ValueError(
                f"unsupported Sherpa recognizer factory: {recognizer_factory!r}"
            )
        self.model_id = model_id
        self.repo = repo
        self._recognizer_config = dict(recognizer_config)
        self._device = device
        self._recognizer_factory = recognizer_factory
        self._recognizer: Any | None = None
        self._load_lock = asyncio.Lock()
        self._active_streams = 0
        self._last_used: float | None = None

    def loaded(self) -> bool:
        return self._recognizer is not None

    def last_used_secs_ago(self) -> float | None:
        if self._last_used is None:
            return None
        return time.monotonic() - self._last_used

    @property
    def active_streams(self) -> int:
        return self._active_streams

    async def get_model(self) -> Any:
        if self._recognizer is not None:
            return self._recognizer
        async with self._load_lock:
            if self._recognizer is not None:
                return self._recognizer
            logger.info(
                "loading Sherpa online recognizer",
                extra={"model_id": self.model_id, "repo": self.repo},
            )
            self._recognizer = await asyncio.to_thread(self._load_recognizer)
            logger.info(
                "loaded Sherpa online recognizer",
                extra={"model_id": self.model_id, "repo": self.repo},
            )
            return self._recognizer

    def _load_recognizer(self) -> Any:
        try:
            sherpa_onnx = importlib.import_module("sherpa_onnx")
        except ModuleNotFoundError as err:
            raise RuntimeError(
                "Sherpa streaming requires the optional sherpa-onnx package"
            ) from err
        factory = getattr(
            sherpa_onnx.OnlineRecognizer,
            self._recognizer_factory,
            None,
        )
        if not callable(factory):
            raise RuntimeError(
                "installed sherpa-onnx does not provide "
                f"OnlineRecognizer.{self._recognizer_factory}"
            )
        config = dict(self._recognizer_config)
        config.setdefault("provider", self._provider())
        config.setdefault("enable_endpoint_detection", True)
        return factory(**config)

    def _provider(self) -> str:
        if self._device.lower().startswith("cuda"):
            return "cuda"
        if self._device.lower() == "coreml":
            return "coreml"
        return "cpu"

    async def start_stream(self, config: StreamConfig) -> StreamingASRSession:
        recognizer = await self.get_model()
        stream = await asyncio.to_thread(recognizer.create_stream)
        self._active_streams += 1
        self._last_used = time.monotonic()
        return SherpaStreamingSession(
            recognizer=recognizer,
            stream=stream,
            config=config,
            release=self._release_stream,
        )

    async def transcribe(
        self,
        audio_path: str,
        *,
        source_lang: str | None,
        target_lang: str | None,
        task: str,
        with_timestamps: bool = False,
    ) -> TranscribeResult:
        """Transcribe a complete normalized audio file with the online decoder."""
        del target_lang, task
        return await transcribe_wav_via_stream(
            self,
            audio_path,
            source_lang=source_lang,
            with_timestamps=with_timestamps,
            max_frame_bytes=_MAX_BATCH_FRAME_BYTES,
        )

    def _release_stream(self) -> None:
        if self._active_streams > 0:
            self._active_streams -= 1
        self._last_used = time.monotonic()

    async def unload(self) -> None:
        async with self._load_lock:
            if self._active_streams:
                raise RuntimeError(
                    f"cannot unload Sherpa backend with {self._active_streams} "
                    "active stream(s)"
                )
            self._recognizer = None
            self._last_used = None
        await asyncio.to_thread(gc.collect)


class SherpaStreamingSession:
    """Decode one client's incremental PCM stream."""

    def __init__(
        self,
        recognizer: Any,
        stream: Any,
        config: StreamConfig,
        release: Callable[[], None],
    ) -> None:
        self._recognizer = recognizer
        self._stream = stream
        self._config = config
        self._release = release
        self._revision = 0
        self._accepted_samples = 0
        self._utterance_start_seconds = 0.0
        self._last_partial = ""
        self._closed = False
        self._released = False
        self._final_event: TranscriptEvent | None = None
        self._operation_lock = asyncio.Lock()

    async def feed(self, pcm_s16le: bytes) -> list[TranscriptEvent]:
        if not pcm_s16le:
            raise ValueError("Sherpa PCM frame must not be empty")
        if len(pcm_s16le) % 2:
            raise ValueError("Sherpa PCM frame must contain complete int16 samples")
        async with self._operation_lock:
            self._require_open()
            return await asyncio.to_thread(self._feed_sync, pcm_s16le)

    def _feed_sync(self, pcm_s16le: bytes) -> list[TranscriptEvent]:
        samples = _pcm16_to_float(pcm_s16le)
        self._stream.accept_waveform(self._config.sample_rate, samples)
        self._accepted_samples += len(samples)
        self._decode_ready()
        result = self._recognizer.get_result(self._stream)
        text = _result_text(result)
        if self._recognizer.is_endpoint(self._stream):
            event = self._event("endpoint", result, is_final=False)
            self._recognizer.reset(self._stream)
            self._last_partial = ""
            self._utterance_start_seconds = self.audio_seconds
            return [event]
        if not text or text == self._last_partial:
            return []
        self._last_partial = text
        return [self._event("partial", result, is_final=False)]

    async def finalize(self) -> TranscriptEvent:
        async with self._operation_lock:
            if self._final_event is not None:
                return self._final_event
            self._require_open()
            try:
                self._final_event = await asyncio.to_thread(self._finalize_sync)
                return self._final_event
            finally:
                self._close_once()

    def _finalize_sync(self) -> TranscriptEvent:
        self._stream.input_finished()
        self._decode_ready()
        return self._event(
            "final",
            self._recognizer.get_result(self._stream),
            is_final=True,
        )

    async def cancel(self) -> None:
        await self.close()

    async def close(self) -> None:
        async with self._operation_lock:
            self._close_once()

    def _close_once(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stream = None
        if not self._released:
            self._released = True
            self._release()

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Sherpa streaming session is closed")

    def _decode_ready(self) -> None:
        for _ in range(_MAX_DECODE_STEPS_PER_CALL):
            if not self._recognizer.is_ready(self._stream):
                return
            self._recognizer.decode_stream(self._stream)
        raise RuntimeError("Sherpa decoder exceeded the per-call decode step limit")

    @property
    def audio_seconds(self) -> float:
        return self._accepted_samples / self._config.sample_rate

    def _event(
        self,
        event_type: str,
        result: Any,
        is_final: bool,
    ) -> TranscriptEvent:
        self._revision += 1
        words: list[dict[str, Any]] = []
        if self._config.word_timestamps:
            words = _result_words(
                result,
                time_offset=self._utterance_start_seconds,
                audio_seconds=self.audio_seconds,
            )
        return TranscriptEvent(
            event_type=event_type,
            revision=self._revision,
            text=_result_text(result),
            words=words,
            audio_seconds=self.audio_seconds,
            is_final=is_final,
        )


def _pcm16_to_float(pcm_s16le: bytes) -> array[float]:
    samples = array("h")
    samples.frombytes(pcm_s16le)
    if samples.itemsize != 2:
        raise RuntimeError("this platform does not provide 16-bit signed arrays")
    if sys.byteorder != "little":
        samples.byteswap()
    return array("f", (sample / _PCM16_SCALE for sample in samples))


def _result_text(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    text = getattr(result, "text", "")
    if not isinstance(text, str):
        return ""
    return text.strip()


def _result_words(
    result: Any,
    time_offset: float,
    audio_seconds: float,
) -> list[dict[str, Any]]:
    tokens = getattr(result, "tokens", ())
    timestamps = getattr(result, "timestamps", ())
    if isinstance(tokens, (str, bytes)) or isinstance(timestamps, (str, bytes)):
        return []
    try:
        usable = min(len(tokens), len(timestamps))
    except TypeError:
        return []
    words: list[dict[str, Any]] = []
    for index in range(usable):
        token = tokens[index]
        timestamp = timestamps[index]
        if not isinstance(token, str) or not isinstance(timestamp, (int, float)):
            continue
        start = float(timestamp) + time_offset
        if not math.isfinite(start) or start < time_offset or start > audio_seconds:
            continue
        end = audio_seconds
        if index + 1 < usable:
            next_timestamp = timestamps[index + 1]
            if isinstance(next_timestamp, (int, float)):
                candidate = float(next_timestamp) + time_offset
                if math.isfinite(candidate) and candidate >= start:
                    end = min(candidate, audio_seconds)
        words.append({"word": token, "start": start, "end": max(start, end)})
    return words

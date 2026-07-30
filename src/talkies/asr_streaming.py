"""Framework-independent protocol primitives for streaming ASR."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

PCM_ENCODING = "pcm_s16le"
PCM_SAMPLE_RATE = 16_000
PCM_CHANNELS = 1
PCM_SAMPLE_BYTES = 2
MAX_CONTROL_MESSAGE_BYTES = 4096

EVENT_START = "start"
EVENT_END = "end"
EVENT_CANCEL = "cancel"
EVENT_PARTIAL = "partial"
EVENT_ENDPOINT = "endpoint"
EVENT_FINAL = "final"

_TRANSCRIPT_EVENT_TYPES = frozenset({EVENT_PARTIAL, EVENT_ENDPOINT, EVENT_FINAL})
_START_FIELDS = frozenset(
    {
        "type",
        "model",
        "encoding",
        "sample_rate",
        "channels",
        "language",
        "interim_results",
        "word_timestamps",
    }
)
_REQUIRED_START_FIELDS = frozenset(
    {"type", "model", "encoding", "sample_rate", "channels"}
)
_CONTROL_FIELDS = frozenset({"type"})
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})*$")
_LANGUAGE_AUTO = "auto"


class StreamingProtocolError(ValueError):
    """A client message or PCM frame violates the streaming wire contract."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class StreamBufferFullError(RuntimeError):
    """A stream's bounded receive buffer cannot accept another frame."""


class StreamClosedError(RuntimeError):
    """An operation was attempted after stream cleanup began."""


@dataclass(frozen=True)
class StreamConfig:
    """Validated settings from the first client message."""

    model: str
    encoding: str = PCM_ENCODING
    sample_rate: int = PCM_SAMPLE_RATE
    channels: int = PCM_CHANNELS
    language: str | None = None
    interim_results: bool = True
    word_timestamps: bool = False


@dataclass(frozen=True)
class TranscriptEvent:
    """A revisioned transcript update shared by every streaming backend."""

    event_type: str
    revision: int
    text: str
    words: list[dict[str, object]] = field(default_factory=list)
    audio_seconds: float = 0.0
    is_final: bool = False

    def __post_init__(self) -> None:
        if self.event_type not in _TRANSCRIPT_EVENT_TYPES:
            raise ValueError(f"unsupported transcript event: {self.event_type}")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if not math.isfinite(self.audio_seconds) or self.audio_seconds < 0:
            raise ValueError("audio_seconds must be finite and non-negative")
        if self.event_type == EVENT_FINAL and not self.is_final:
            raise ValueError("final events must set is_final")
        if self.event_type != EVENT_FINAL and self.is_final:
            raise ValueError("only final events may set is_final")
        if any(not isinstance(word, dict) for word in self.words):
            raise TypeError("words must contain objects")

    def to_dict(self) -> dict[str, object]:
        """Return the public WebSocket representation."""

        return {
            "type": self.event_type,
            "revision": self.revision,
            "text": self.text,
            "words": [dict(word) for word in self.words],
            "audio_seconds": self.audio_seconds,
            "is_final": self.is_final,
        }


StreamingTranscriptEvent = TranscriptEvent


class StreamingASRSession(Protocol):
    """Per-connection decoder state implemented by a streaming backend."""

    async def feed(self, pcm_s16le: bytes) -> list[TranscriptEvent]: ...

    async def finalize(self) -> TranscriptEvent: ...

    async def cancel(self) -> None: ...

    async def close(self) -> None: ...


class StreamingASRBackend(Protocol):
    """Shared model handle that creates isolated decoder sessions."""

    async def start_stream(self, config: StreamConfig) -> StreamingASRSession: ...


CleanupCallback = Callable[[], Awaitable[None] | None]


def parse_start_message(message: object) -> StreamConfig:
    """Validate and normalize the first JSON object from a client."""

    data = _require_object(message)
    _reject_unknown_fields(data, _START_FIELDS)
    missing = _REQUIRED_START_FIELDS.difference(data)
    if missing:
        raise StreamingProtocolError(
            "missing_field", f"missing required field: {sorted(missing)[0]}"
        )
    if data["type"] != EVENT_START:
        raise StreamingProtocolError("invalid_event", "first event must be start")

    model = _require_string(data, "model")
    if not _MODEL_PATTERN.fullmatch(model):
        raise StreamingProtocolError("invalid_model", "model has an invalid format")

    encoding = _require_string(data, "encoding")
    if encoding != PCM_ENCODING:
        raise StreamingProtocolError(
            "unsupported_encoding", f"encoding must be {PCM_ENCODING}"
        )
    sample_rate = _require_integer(data, "sample_rate")
    if sample_rate != PCM_SAMPLE_RATE:
        raise StreamingProtocolError(
            "unsupported_sample_rate", f"sample_rate must be {PCM_SAMPLE_RATE}"
        )
    channels = _require_integer(data, "channels")
    if channels != PCM_CHANNELS:
        raise StreamingProtocolError(
            "unsupported_channels", f"channels must be {PCM_CHANNELS}"
        )

    return StreamConfig(
        model=model,
        encoding=encoding,
        sample_rate=sample_rate,
        channels=channels,
        language=_optional_language(data.get("language")),
        interim_results=_optional_boolean(data, "interim_results", True),
        word_timestamps=_optional_boolean(data, "word_timestamps", False),
    )


def parse_control_message(message: object) -> str:
    """Validate an end or cancel JSON control object."""

    data = _require_object(message)
    _reject_unknown_fields(data, _CONTROL_FIELDS)
    event_type = _require_string(data, "type")
    if event_type not in (EVENT_END, EVENT_CANCEL):
        raise StreamingProtocolError(
            "invalid_event", "control event must be end or cancel"
        )
    return event_type


def decode_json_message(
    text: str,
    *,
    max_message_bytes: int = MAX_CONTROL_MESSAGE_BYTES,
) -> object:
    """Decode one bounded JSON message and reject ambiguous duplicate keys."""

    if max_message_bytes < 1:
        raise ValueError("max_message_bytes must be positive")
    try:
        encoded_size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise StreamingProtocolError(
            "invalid_json",
            "message must contain valid Unicode",
        ) from exc
    if encoded_size > max_message_bytes:
        raise StreamingProtocolError(
            "message_too_large",
            "JSON message exceeds the configured limit",
        )

    try:
        return json.loads(text, object_pairs_hook=_unique_json_object)
    except StreamingProtocolError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise StreamingProtocolError(
            "invalid_json",
            "message must contain valid JSON",
        ) from exc


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StreamingProtocolError(
                "duplicate_field",
                f"duplicate field: {key}",
            )
        result[key] = value
    return result


def validate_pcm_frame(frame: bytes, *, max_frame_bytes: int) -> int:
    """Validate one PCM S16LE frame and return its mono sample count."""

    if max_frame_bytes < PCM_SAMPLE_BYTES:
        raise ValueError("max_frame_bytes must fit at least one sample")
    if not isinstance(frame, bytes):
        raise StreamingProtocolError("invalid_audio", "audio frame must be bytes")
    if not frame:
        raise StreamingProtocolError("empty_audio", "audio frame must not be empty")
    if len(frame) > max_frame_bytes:
        raise StreamingProtocolError(
            "frame_too_large", "audio frame exceeds the configured limit"
        )
    if len(frame) % PCM_SAMPLE_BYTES:
        raise StreamingProtocolError(
            "unaligned_audio", "audio frame must contain complete int16 samples"
        )
    return len(frame) // PCM_SAMPLE_BYTES


def pack_event(event_type: str, **payload: object) -> dict[str, object]:
    """Pack a non-transcript server event without mutating caller data."""

    if event_type in _TRANSCRIPT_EVENT_TYPES:
        raise ValueError("use TranscriptEvent for transcript events")
    return {"type": event_type, **payload}


def longest_stable_prefix(previous: str, current: str) -> str:
    """Return their common prefix ending at a complete whitespace boundary."""

    shared_length = 0
    for previous_char, current_char in zip(previous, current):
        if previous_char != current_char:
            break
        shared_length += 1
    if shared_length == 0:
        return ""
    shared = current[:shared_length]
    if shared_length == len(current) and shared_length == len(previous):
        return shared
    boundary = shared.rfind(" ")
    if boundary < 0:
        return ""
    return shared[: boundary + 1]


class StreamSessionState:
    """Bounded queue, sample clock, revisions, and convergent cleanup."""

    def __init__(
        self,
        *,
        max_buffer_bytes: int,
        max_buffer_frames: int,
        cleanup: CleanupCallback | None = None,
    ) -> None:
        if max_buffer_bytes < PCM_SAMPLE_BYTES:
            raise ValueError("max_buffer_bytes must fit at least one sample")
        if max_buffer_frames < 1:
            raise ValueError("max_buffer_frames must be positive")
        self.max_buffer_bytes = max_buffer_bytes
        self.max_buffer_frames = max_buffer_frames
        self.pending_bytes = 0
        self.pending_frames = 0
        self.accepted_samples = 0
        self.revision = 0
        self._cleanup = cleanup
        self._closed = False
        self._close_lock = asyncio.Lock()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def audio_seconds(self) -> float:
        return self.accepted_samples / PCM_SAMPLE_RATE

    def reserve_frame(self, frame: bytes, *, max_frame_bytes: int) -> int:
        """Reserve bounded queue capacity for a validated frame."""

        if self._closed:
            raise StreamClosedError("stream is closed")
        samples = validate_pcm_frame(frame, max_frame_bytes=max_frame_bytes)
        if self.pending_frames >= self.max_buffer_frames:
            raise StreamBufferFullError("stream frame buffer is full")
        if self.pending_bytes + len(frame) > self.max_buffer_bytes:
            raise StreamBufferFullError("stream byte buffer is full")
        self.pending_frames += 1
        self.pending_bytes += len(frame)
        self.accepted_samples += samples
        return samples

    def release_frame(self, frame_bytes: int) -> None:
        """Release capacity after one queued frame leaves the worker."""

        if frame_bytes < PCM_SAMPLE_BYTES or frame_bytes % PCM_SAMPLE_BYTES:
            raise ValueError("frame_bytes must contain complete int16 samples")
        if self.pending_frames < 1 or frame_bytes > self.pending_bytes:
            raise ValueError("frame release exceeds pending queue accounting")
        self.pending_frames -= 1
        self.pending_bytes -= frame_bytes

    def transcript_event(
        self,
        event_type: str,
        text: str,
        *,
        words: list[dict[str, object]] | None = None,
    ) -> TranscriptEvent:
        """Create the next monotonic transcript revision."""

        if self._closed:
            raise StreamClosedError("stream is closed")
        self.revision += 1
        return TranscriptEvent(
            event_type=event_type,
            revision=self.revision,
            text=text,
            words=[] if words is None else words,
            audio_seconds=self.audio_seconds,
            is_final=event_type == EVENT_FINAL,
        )

    async def close(self) -> None:
        """Run cleanup at most once, including under concurrent callers."""

        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            cleanup = self._cleanup
            self._cleanup = None
            if cleanup is None:
                return
            result = cleanup()
            if inspect.isawaitable(result):
                await result


def _require_object(message: object) -> dict[str, object]:
    if not isinstance(message, dict):
        raise StreamingProtocolError("invalid_message", "message must be an object")
    if any(not isinstance(key, str) for key in message):
        raise StreamingProtocolError(
            "invalid_message", "message field names must be strings"
        )
    return message


def _reject_unknown_fields(
    data: dict[str, object], allowed_fields: frozenset[str]
) -> None:
    unknown = set(data).difference(allowed_fields)
    if unknown:
        raise StreamingProtocolError(
            "unknown_field", f"unknown field: {sorted(unknown)[0]}"
        )


def _require_string(data: dict[str, object], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value:
        raise StreamingProtocolError(
            "invalid_field", f"{field_name} must be a non-empty string"
        )
    return value


def _require_integer(data: dict[str, object], field_name: str) -> int:
    value = data.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StreamingProtocolError(
            "invalid_field", f"{field_name} must be an integer"
        )
    return value


def _optional_boolean(data: dict[str, object], field_name: str, default: bool) -> bool:
    if field_name not in data:
        return default
    value = data[field_name]
    if not isinstance(value, bool):
        raise StreamingProtocolError("invalid_field", f"{field_name} must be a boolean")
    return value


def _optional_language(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise StreamingProtocolError(
            "invalid_language", "language must be a non-empty string"
        )
    if value == _LANGUAGE_AUTO:
        return value
    if not _LANGUAGE_PATTERN.fullmatch(value):
        raise StreamingProtocolError(
            "invalid_language", "language has an invalid format"
        )
    return value

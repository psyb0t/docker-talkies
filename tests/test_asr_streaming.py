"""Unit tests for framework-independent streaming ASR primitives."""

from __future__ import annotations

import asyncio

import pytest

from talkies.asr_streaming import (
    EVENT_CANCEL,
    EVENT_END,
    EVENT_FINAL,
    EVENT_PARTIAL,
    PCM_SAMPLE_RATE,
    StreamBufferFullError,
    StreamClosedError,
    StreamingProtocolError,
    StreamSessionState,
    TranscriptEvent,
    decode_json_message,
    longest_stable_prefix,
    pack_event,
    parse_control_message,
    parse_start_message,
    validate_pcm_frame,
)

_MAX_FRAME_BYTES = 8
_VALID_START = {
    "type": "start",
    "model": "nemotron-3.5-asr-0.6b",
    "encoding": "pcm_s16le",
    "sample_rate": PCM_SAMPLE_RATE,
    "channels": 1,
}


def test_parse_start_message_applies_defaults():
    config = parse_start_message(_VALID_START)

    assert config.model == "nemotron-3.5-asr-0.6b"
    assert config.language is None
    assert config.interim_results is True
    assert config.word_timestamps is False


def test_parse_start_message_accepts_optional_fields():
    config = parse_start_message(
        {
            **_VALID_START,
            "language": "en-US",
            "interim_results": False,
            "word_timestamps": True,
        }
    )

    assert config.language == "en-US"
    assert config.interim_results is False
    assert config.word_timestamps is True


@pytest.mark.parametrize(
    "change,code",
    [
        ({"type": "partial"}, "invalid_event"),
        ({"model": "../model"}, "invalid_model"),
        ({"encoding": "opus"}, "unsupported_encoding"),
        ({"sample_rate": 48_000}, "unsupported_sample_rate"),
        ({"channels": 2}, "unsupported_channels"),
        ({"language": "../../en"}, "invalid_language"),
        ({"interim_results": 1}, "invalid_field"),
        ({"word_timestamps": "yes"}, "invalid_field"),
        ({"unexpected": True}, "unknown_field"),
    ],
)
def test_parse_start_message_rejects_invalid_fields(change, code):
    with pytest.raises(StreamingProtocolError) as error:
        parse_start_message({**_VALID_START, **change})

    assert error.value.code == code


@pytest.mark.parametrize("missing", sorted(_VALID_START))
def test_parse_start_message_rejects_missing_required_fields(missing):
    message = dict(_VALID_START)
    del message[missing]

    with pytest.raises(StreamingProtocolError):
        parse_start_message(message)


@pytest.mark.parametrize("message", [None, [], "start", {1: "start"}])
def test_parse_start_message_requires_string_keyed_object(message):
    with pytest.raises(StreamingProtocolError, match="message"):
        parse_start_message(message)


@pytest.mark.parametrize("event_type", [EVENT_END, EVENT_CANCEL])
def test_parse_control_message_accepts_supported_events(event_type):
    assert parse_control_message({"type": event_type}) == event_type


@pytest.mark.parametrize(
    "message",
    [{"type": "start"}, {"type": "end", "text": "ignored"}, {}, b"end"],
)
def test_parse_control_message_rejects_invalid_shape(message):
    with pytest.raises(StreamingProtocolError):
        parse_control_message(message)


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ('{"type":"start","type":"end"}', "duplicate_field"),
        ("{" * 1000, "invalid_json"),
        ('{"type":"' + "x" * 4096 + '"}', "message_too_large"),
    ],
)
def test_decode_json_message_rejects_ambiguous_or_oversized_input(message, code):
    with pytest.raises(StreamingProtocolError) as error:
        decode_json_message(message)

    assert error.value.code == code


@pytest.mark.parametrize(
    "frame,expected_samples",
    [(b"\x00\x00", 1), (b"\x00\x00" * 4, 4)],
)
def test_validate_pcm_frame_accepts_aligned_boundaries(frame, expected_samples):
    assert (
        validate_pcm_frame(frame, max_frame_bytes=_MAX_FRAME_BYTES) == expected_samples
    )


@pytest.mark.parametrize(
    "frame,code",
    [
        (b"", "empty_audio"),
        (b"\x00", "unaligned_audio"),
        (b"\x00\x00" * 5, "frame_too_large"),
    ],
)
def test_validate_pcm_frame_rejects_bad_audio(frame, code):
    with pytest.raises(StreamingProtocolError) as error:
        validate_pcm_frame(frame, max_frame_bytes=_MAX_FRAME_BYTES)

    assert error.value.code == code


def test_transcript_event_packs_copied_wire_shape():
    words = [{"word": "hello", "start": 0.0, "end": 0.25}]
    event = TranscriptEvent(
        event_type=EVENT_PARTIAL,
        revision=1,
        text="hello",
        words=words,
        audio_seconds=0.25,
    )

    packed = event.to_dict()
    words[0]["word"] = "changed"

    assert packed == {
        "type": "partial",
        "revision": 1,
        "text": "hello",
        "words": [{"word": "hello", "start": 0.0, "end": 0.25}],
        "audio_seconds": 0.25,
        "is_final": False,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"event_type": "ready"},
        {"revision": 0},
        {"audio_seconds": -1.0},
        {"audio_seconds": float("nan")},
        {"event_type": EVENT_FINAL, "is_final": False},
        {"event_type": EVENT_PARTIAL, "is_final": True},
    ],
)
def test_transcript_event_rejects_invalid_values(kwargs):
    values = {
        "event_type": EVENT_PARTIAL,
        "revision": 1,
        "text": "hello",
        "audio_seconds": 0.0,
        "is_final": False,
    }
    values.update(kwargs)

    with pytest.raises((TypeError, ValueError)):
        TranscriptEvent(**values)


def test_pack_event_rejects_transcript_event_types():
    assert pack_event("ready", model="test") == {"type": "ready", "model": "test"}
    with pytest.raises(ValueError, match="TranscriptEvent"):
        pack_event(EVENT_PARTIAL)


@pytest.mark.parametrize(
    "previous,current,expected",
    [
        ("hello world", "hello there", "hello "),
        ("same", "same", "same"),
        ("prefix", "prelude", ""),
        ("hi brave world", "hi brave new world", "hi brave "),
        ("old", "new", ""),
    ],
)
def test_longest_stable_prefix(previous, current, expected):
    assert longest_stable_prefix(previous, current) == expected


def test_session_state_accounts_queue_clock_and_revisions():
    state = StreamSessionState(max_buffer_bytes=8, max_buffer_frames=2)

    assert state.reserve_frame(b"\x00\x00" * 2, max_frame_bytes=8) == 2
    assert state.reserve_frame(b"\x00\x00", max_frame_bytes=8) == 1
    assert state.pending_bytes == 6
    assert state.pending_frames == 2
    assert state.audio_seconds == 3 / PCM_SAMPLE_RATE

    partial = state.transcript_event(EVENT_PARTIAL, "hello")
    final = state.transcript_event(EVENT_FINAL, "hello")
    assert (partial.revision, final.revision) == (1, 2)
    assert final.is_final is True

    state.release_frame(4)
    state.release_frame(2)
    assert state.pending_bytes == 0
    assert state.pending_frames == 0


def test_session_state_rejects_full_or_invalid_release():
    state = StreamSessionState(max_buffer_bytes=4, max_buffer_frames=1)
    state.reserve_frame(b"\x00\x00", max_frame_bytes=4)

    with pytest.raises(StreamBufferFullError):
        state.reserve_frame(b"\x00\x00", max_frame_bytes=4)
    with pytest.raises(ValueError, match="exceeds"):
        state.release_frame(4)


def test_session_state_cleanup_is_idempotent_under_concurrency():
    cleanup_calls = 0

    async def cleanup():
        nonlocal cleanup_calls
        await asyncio.sleep(0)
        cleanup_calls += 1

    async def scenario():
        state = StreamSessionState(
            max_buffer_bytes=8,
            max_buffer_frames=2,
            cleanup=cleanup,
        )
        await asyncio.gather(state.close(), state.close(), state.close())
        assert state.closed is True
        with pytest.raises(StreamClosedError):
            state.reserve_frame(b"\x00\x00", max_frame_bytes=8)
        with pytest.raises(StreamClosedError):
            state.transcript_event(EVENT_PARTIAL, "late")

    asyncio.run(scenario())
    assert cleanup_calls == 1


def test_session_state_does_not_retry_failed_cleanup():
    cleanup_calls = 0

    async def cleanup():
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise RuntimeError("cleanup failed")

    async def scenario():
        state = StreamSessionState(
            max_buffer_bytes=8,
            max_buffer_frames=2,
            cleanup=cleanup,
        )
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await state.close()
        await state.close()
        assert state.closed is True

    asyncio.run(scenario())
    assert cleanup_calls == 1

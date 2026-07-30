#!/bin/bash
# Real WebSocket streaming coverage for the native parakeet.cpp/Nemotron ASR
# path. The helper container shares the service container's network namespace,
# converts the committed fixture to PCM, and exercises the public socket.

set -euo pipefail

_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# This test deliberately covers the bundled CPU-native parakeet.cpp stream.
# Other integration files retain the harness's CUDA defaults.
export HARNESS_IMAGE="${HARNESS_IMAGE:-psyb0t/talkies:local}"
export HARNESS_DEVICE="${HARNESS_DEVICE:-cpu}"
export HARNESS_USE_GPU="${HARNESS_USE_GPU:-0}"
# shellcheck source=harness.sh
source "${_DIR}/harness.sh"
# shellcheck source=common.sh
source "${_DIR}/common.sh"

readonly STREAM_MODELS="${STREAM_MODELS:-nemotron-3.5-asr-0.6b}"
readonly ASR_SLUG="${ASR_SLUG:-nemotron-3.5-asr-0.6b}"
readonly FIXTURE_MP3="${FIXTURE_MP3:-${_HARNESS_REPO_ROOT}/tests/integration/.fixtures/audio.mp3}"
readonly FIXTURE_TXT="${FIXTURE_TXT:-${_HARNESS_REPO_ROOT}/tests/integration/.fixtures/audio.mp3.txt}"
readonly PCM_FRAME_BYTES=640

harness_start "$STREAM_MODELS"

if [ ! -f "$FIXTURE_MP3" ]; then
    echo "FATAL: fixture missing — needs $FIXTURE_MP3" >&2
    exit 2
fi

if [ -n "${EXPECTED_TEXT:-}" ]; then
    expected_text="$EXPECTED_TEXT"
elif [ -f "$FIXTURE_TXT" ]; then
    expected_text="$(tr -d '\r\n' <"$FIXTURE_TXT")"
else
    echo "FATAL: transcript fixture missing — needs $FIXTURE_TXT or EXPECTED_TEXT" >&2
    exit 2
fi

if [ -n "${EXPECTED_WORDS_CSV:-}" ]; then
    read -r -a EXPECTED_WORDS <<<"$(printf '%s' "$EXPECTED_WORDS_CSV" | tr ',' ' ' | talkies_normalize_text)"
else
    read -r -a EXPECTED_WORDS <<<"$(printf '%s' "$expected_text" | talkies_normalize_text)"
fi

test_stream_fixture_over_real_websocket() {
    local summary helper_stderr transcript normalized ready_model sent_frames reported_frames audio_seconds
    helper_stderr="$(mktemp)"
    if ! summary="$(
        docker run --rm -i \
            --network "container:${HARNESS_CONTAINER}" \
            --entrypoint python3 \
            -v "${FIXTURE_MP3}:/fixture/audio.mp3:ro" \
            "$HARNESS_IMAGE" - "$ASR_SLUG" "$PCM_FRAME_BYTES" 2>"$helper_stderr" <<'PY'
import asyncio
import json
import subprocess
import sys

from websockets.asyncio.client import connect


MODEL = sys.argv[1]
FRAME_BYTES = int(sys.argv[2])
STREAM_URL = "ws://127.0.0.1:8000/v1/audio/transcriptions/stream"
PCM_FORMAT = "pcm_s16le"
PCM_SAMPLE_RATE = 16000
PCM_CHANNELS = 1
RECEIVE_TIMEOUT_SECONDS = 300


def pcm_fixture() -> bytes:
    conversion = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/fixture/audio.mp3",
            "-f",
            "s16le",
            "-acodec",
            PCM_FORMAT,
            "-ar",
            str(PCM_SAMPLE_RATE),
            "-ac",
            str(PCM_CHANNELS),
            "pipe:1",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if conversion.returncode != 0:
        raise RuntimeError(conversion.stderr.decode("utf-8", errors="replace"))
    if not conversion.stdout:
        raise RuntimeError("ffmpeg produced no PCM")
    return conversion.stdout


async def stream_fixture() -> dict[str, object]:
    pcm = pcm_fixture()
    start = {
        "type": "start",
        "model": MODEL,
        "encoding": PCM_FORMAT,
        "sample_rate": PCM_SAMPLE_RATE,
        "channels": PCM_CHANNELS,
        "interim_results": True,
        "word_timestamps": True,
    }
    transcript_chunks: list[str] = []
    event_types: list[str] = []
    sent_frames = 0

    async with connect(STREAM_URL, open_timeout=30, close_timeout=30) as socket:
        await socket.send(json.dumps(start))
        ready = json.loads(
            await asyncio.wait_for(socket.recv(), timeout=RECEIVE_TIMEOUT_SECONDS)
        )
        if ready.get("type") != "ready":
            raise RuntimeError(f"expected ready event, got {ready}")
        if ready.get("model") != MODEL:
            raise RuntimeError(f"ready model mismatch: {ready}")

        for offset in range(0, len(pcm), FRAME_BYTES):
            await socket.send(pcm[offset : offset + FRAME_BYTES])
            sent_frames += 1
        await socket.send(json.dumps({"type": "end"}))

        final_seen = False
        stats: dict[str, object] | None = None
        while stats is None:
            event = json.loads(
                await asyncio.wait_for(socket.recv(), timeout=RECEIVE_TIMEOUT_SECONDS)
            )
            event_type = event.get("type")
            if not isinstance(event_type, str):
                raise RuntimeError(f"event type missing: {event}")
            event_types.append(event_type)
            if event_type == "error":
                raise RuntimeError(f"server returned error: {event}")
            if event_type in {"partial", "endpoint", "final"}:
                text = event.get("text")
                if isinstance(text, str) and text:
                    transcript_chunks.append(text)
            if event_type == "final":
                final_seen = True
            if event_type == "stats":
                stats = event

    if not final_seen:
        raise RuntimeError(f"server never emitted final: {event_types}")
    if not transcript_chunks:
        raise RuntimeError(f"server emitted no transcript text: {event_types}")
    if stats is None:
        raise RuntimeError("server never emitted stats")
    if stats.get("canceled") is not False:
        raise RuntimeError(f"stream unexpectedly canceled: {stats}")
    if stats.get("frames") != sent_frames:
        raise RuntimeError(f"frame count mismatch: sent={sent_frames}, stats={stats}")

    return {
        "ready": ready,
        "event_types": event_types,
        "transcript": " ".join(transcript_chunks),
        "sent_frames": sent_frames,
        "stats": stats,
    }


print(json.dumps(asyncio.run(stream_fixture())))
PY
    )"; then
        echo "  FAIL: real WebSocket fixture stream failed"
        sed 's/^/  helper: /' "$helper_stderr" >&2
        rm -f "$helper_stderr"
        return 1
    fi
    rm -f "$helper_stderr"

    if ! printf '%s\n' "$summary" | jq -e . >/dev/null; then
        echo "  FAIL: helper returned invalid JSON: $summary"
        return 1
    fi
    ready_model="$(printf '%s\n' "$summary" | jq -r '.ready.model')"
    transcript="$(printf '%s\n' "$summary" | jq -r '.transcript')"
    sent_frames="$(printf '%s\n' "$summary" | jq -r '.sent_frames')"
    reported_frames="$(printf '%s\n' "$summary" | jq -r '.stats.frames')"
    audio_seconds="$(printf '%s\n' "$summary" | jq -r '.stats.audio_seconds')"
    normalized="$(printf '%s' "$transcript" | talkies_normalize_text)"

    assert_eq "$ready_model" "$ASR_SLUG" "ready model"
    assert_eq "$reported_frames" "$sent_frames" "stats frame count"
    if ! awk -v seconds="$audio_seconds" 'BEGIN { exit !(seconds > 0) }'; then
        echo "  FAIL: non-positive streamed audio duration: $audio_seconds"
        return 1
    fi

    local word
    for word in "${EXPECTED_WORDS[@]}"; do
        if [[ " $normalized " != *" $word "* ]]; then
            echo "  FAIL: streamed transcript missing expected word '$word'"
            echo "  transcript: $transcript"
            return 1
        fi
    done

    echo "  streamed: $transcript"
    echo "  ok: ready, transcript, final, stats; frames=$sent_frames audio=${audio_seconds}s"
    echo "OK: ${FUNCNAME[0]}"
}

harness_run_tests test_stream_fixture_over_real_websocket

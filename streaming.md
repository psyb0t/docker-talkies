# Streaming audio in talkies

talkies has two independent streaming surfaces:

- Live ASR uses a bidirectional WebSocket carrying raw PCM audio in and JSON
  transcript events out.
- Qwen3-TTS can return raw PCM incrementally in an HTTP response body.

## Contents

- [Live ASR over WebSocket](#live-asr-over-websocket)
  - [Audio and start message](#audio-and-start-message)
  - [Session lifecycle](#session-lifecycle)
  - [Python client](#python-client)
  - [Server events](#server-events)
  - [Errors and close codes](#errors-and-close-codes)
  - [Backend behavior](#backend-behavior)
  - [Limits and model pinning](#limits-and-model-pinning)
  - [Sherpa-ONNX and Vosk models](#sherpa-onnx-and-vosk-models)
- [Qwen3-TTS PCM over HTTP](#qwen3-tts-pcm-over-http)
  - [TTS wire format](#tts-wire-format)
  - [TTS quick start](#tts-quick-start)
  - [TTS configuration](#tts-configuration)
  - [TTS cancellation](#tts-cancellation)
  - [TTS architecture notes](#tts-architecture-notes)

## Live ASR over WebSocket

Connect to:

```text
ws://localhost:8000/v1/audio/transcriptions/stream
```

Use `wss://` when the service is behind TLS. The route is talkies-specific;
the OpenAI-compatible `POST /v1/audio/transcriptions` route remains a complete
file request/response API.

When `TALKIES_AUTH_TOKEN` is configured, include this header in the WebSocket
upgrade request:

```text
Authorization: Bearer <token>
```

Do not put the token in the URL. Missing or invalid credentials close the
connection with code 4401 before the application accepts the WebSocket.

### Audio and start message

The client must send one JSON text message before any audio:

```json
{
  "type": "start",
  "model": "nemotron-3.5-asr-0.6b",
  "encoding": "pcm_s16le",
  "sample_rate": 16000,
  "channels": 1,
  "language": "auto",
  "interim_results": true,
  "word_timestamps": true
}
```

| Field | Required | Default | Contract |
|---|---|---|---|
| `type` | yes | — | Must be `start`. |
| `model` | yes | — | Configured ASR slug whose backend supports streaming. |
| `encoding` | yes | — | Must be `pcm_s16le`. |
| `sample_rate` | yes | — | Must be `16000`. |
| `channels` | yes | — | Must be `1`. |
| `language` | no | backend default | `auto` or a language/locale tag such as `en` or `en-US`. |
| `interim_results` | no | `true` | When false, suppresses `partial` events. |
| `word_timestamps` | no | `false` | Requests `words`; unsupported/unavailable alignments remain an empty list. |

Start and control JSON messages are capped at 4096 UTF-8 bytes. Unknown and
duplicate fields are rejected. After `ready`, every audio message must be a
binary WebSocket frame containing raw signed 16-bit little-endian mono samples
with no WAV header. Frames must be non-empty and contain an even byte count.
The frame boundary is transport framing only; it does not need to match a word,
utterance, or time interval.

The bundled Uvicorn server caps an admitted WebSocket message at the larger of
4096 bytes and `TALKIES_STREAM_MAX_FRAME_BYTES`, and holds at most one pending
message per connection. If decoding is slower than capture, transport-level
backpressure reaches the client instead of growing an unbounded application
queue. Equivalent limits must be configured when embedding `talkies.server.app`
in a different ASGI server.

To convert an arbitrary input before streaming it:

```bash
ffmpeg -i input.webm -f s16le -acodec pcm_s16le -ar 16000 -ac 1 input.raw
```

### Session lifecycle

```text
client                                  talkies
  │── JSON start ─────────────────────────▶│
  │◀─ JSON ready ──────────────────────────│
  │── binary PCM frame(s) ────────────────▶│
  │◀─ partial / endpoint event(s) ─────────│
  │── {"type":"end"} ───────────────────▶│
  │◀─ final ─ stats ─ close(1000) ─────────│
```

Send `{"type":"end"}` to flush remaining decoder state. The server sends one
`final`, then `stats` with `canceled=false`, then closes normally.

Send `{"type":"cancel"}` to discard the stream. Cancellation does not run a
final decode: the server sends `stats` with `canceled=true` and closes normally.
A client disconnect, timeout, or protocol error also cancels and releases the
backend session, but cannot guarantee delivery of terminal stats to a socket
that is already gone.

### Python client

This example streams a headerless `input.raw` file in 20 ms frames while a
second coroutine consumes transcript events. `websockets` is included in the
talkies runtime dependency graph; install it separately in an external client
environment if needed.

```python
import asyncio
import json
import os

from websockets.asyncio.client import connect

URL = "ws://localhost:8000/v1/audio/transcriptions/stream"
FRAME_BYTES = 640  # 20 ms × 16,000 samples/s × 2 bytes/sample


async def send_audio(socket):
    with open("input.raw", "rb") as audio:
        while frame := audio.read(FRAME_BYTES):
            await socket.send(frame)
            await asyncio.sleep(0.02)
    await socket.send(json.dumps({"type": "end"}))


async def receive_events(socket):
    async for message in socket:
        event = json.loads(message)
        print(event)


async def main():
    token = os.environ.get("TALKIES_AUTH_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with connect(URL, additional_headers=headers) as socket:
        await socket.send(
            json.dumps(
                {
                    "type": "start",
                    "model": "nemotron-3.5-asr-0.6b",
                    "encoding": "pcm_s16le",
                    "sample_rate": 16000,
                    "channels": 1,
                    "language": "auto",
                    "interim_results": True,
                    "word_timestamps": True,
                }
            )
        )
        ready = json.loads(await socket.recv())
        if ready["type"] != "ready":
            raise RuntimeError(ready)
        await asyncio.gather(send_audio(socket), receive_events(socket))


asyncio.run(main())
```

For a microphone, feed its 16 kHz mono PCM16 callback output into the same
bounded producer instead of reading `input.raw`. Do not perform blocking audio
capture on the event loop.

### Server events

`ready` confirms the negotiated fixed audio format:

```json
{
  "type": "ready",
  "model": "nemotron-3.5-asr-0.6b",
  "encoding": "pcm_s16le",
  "sample_rate": 16000,
  "channels": 1
}
```

`partial`, `endpoint`, and `final` share one shape:

```json
{
  "type": "partial",
  "revision": 3,
  "text": "current transcript hypothesis",
  "words": [
    {"word": "current", "start": 0.1, "end": 0.4, "confidence": 0.92}
  ],
  "audio_seconds": 1.28,
  "is_final": false
}
```

- `partial` is revisable. Replace the previously displayed hypothesis with the
  newest revision rather than appending every partial.
- `endpoint` marks a native decoder's utterance boundary without closing the
  session. Clients may commit that utterance to their UI.
- `final` is the flush caused by `end` and has `is_final=true`.
- `revision` increases within one connection. It is not a global ID.
- `audio_seconds` counts accepted input audio, not wall-clock connection time.
- `words` is present on every transcript event but is empty unless requested
  and produced by that backend. Word objects may include `confidence` when the
  engine supplies it.

The terminal accounting event is:

```json
{
  "type": "stats",
  "audio_seconds": 8.4,
  "frames": 420,
  "canceled": false
}
```

### Errors and close codes

Application failures send an error object before closing when the socket is
already accepted:

```json
{"type": "error", "code": "frame_too_large", "detail": "audio frame exceeds the configured limit"}
```

| Close code | Meaning |
|---|---|
| 1000 | Normal `end` or `cancel`. |
| 4400 | Invalid JSON/message/control/start/audio frame, or duration limit. |
| 4401 | Missing or invalid bearer token during the upgrade. |
| 4404 | Unknown model slug. |
| 4408 | No message arrived within `TALKIES_STREAM_IDLE_TIMEOUT`. |
| 4409 | Model cannot stream, or a different model is pinned by active streams. |
| 4429 | Global streaming connection limit reached. |
| 4500 | Backend or internal failure. |

Stable error codes include the parser-specific validation code (`missing_field`,
`unknown_field`, `unsupported_encoding`, `unsupported_sample_rate`,
`unsupported_channels`, `invalid_audio`, `empty_audio`, `frame_too_large`,
`message_too_large`, `duplicate_field`, or `unaligned_audio`) plus
`unknown_model`, `streaming_not_supported`,
`connection_limit`, `model_busy`, `duration_limit`, `idle_timeout`, and
`server_error`.

### Backend behavior

| Executor | Streaming mode | Observable behavior |
|---|---|---|
| `parakeet_cpp` | Native | One parakeet.cpp ABI-v4 decoder session per WebSocket. Feed results become `partial` or `endpoint` when the runtime sets end-of-utterance; finalize flushes native state. |
| `sherpa` | Native | Sherpa-ONNX online recognizer with endpoint detection enabled by default. It resets decoder state after each `endpoint`. |
| `vosk` | Native | Vosk `KaldiRecognizer` consumes PCM bytes directly. `AcceptWaveform` yields `endpoint`; interim JSON yields `partial`. |
| `whisper` | Rolling pseudo-stream | Re-decodes a bounded PCM window every 0.5 seconds, reconciles overlapping hypotheses, and emits changed `partial` revisions. It has no native endpoint event. |

The rolling Whisper path trades compute for compatibility: faster-whisper is a
file/window decoder, so overlapping audio is decoded repeatedly. Lowering
`TALKIES_STREAM_MAX_BUFFER_SECONDS` reduces the re-decoded window and memory,
but also reduces context available for stabilizing hypotheses.

Other ASR executors (`parakeet`, `canary_multitask`, and `canary_salm`) do not
implement this WebSocket contract. Starting one returns
`streaming_not_supported` and close code 4409; their file-upload behavior is
unchanged.

### Limits and model pinning

| Environment variable | Default | Valid range | Effect |
|---|---|---|---|
| `TALKIES_STREAM_MAX_CONNECTIONS` | `4` | 1–1024 | Active ASR WebSockets per container. |
| `TALKIES_STREAM_MAX_FRAME_BYTES` | `65536` | 2–16777216 | Maximum binary message size; frames must still be PCM16-aligned. |
| `TALKIES_STREAM_MAX_BUFFER_SECONDS` | `5` | 0.1–300 | Whisper rolling-window length; also must be large enough to accommodate one maximum-size frame. Native decoders process each frame before the next one is accepted. |
| `TALKIES_STREAM_IDLE_TIMEOUT` | `30s` | 1s–1h | Maximum wait for the next client message. |
| `TALKIES_STREAM_MAX_DURATION` | `4h` | 1s–24h | Maximum accepted audio per connection. |

Duration variables accept bare seconds or Go-style values such as `90s`,
`45m`, and `4h`. Configuration fails at startup if the buffer cannot hold one
maximum-size frame: `buffer_seconds × 16000 × 2` must be at least
`TALKIES_STREAM_MAX_FRAME_BYTES`.

All active connections must use the same model slug. Several clients may share
that loaded backend up to the global connection limit, but starting or calling
a different model while streams are active returns a conflict. The first stream
evicts already loaded sibling models before opening its decoder. While pinned:

- `DELETE /api/ps/<model>` and `POST /unload` return HTTP 409.
- HTTP ASR/TTS requests that would load a sibling return HTTP 409.
- The idle sweeper skips the model.

Normal end, explicit cancel, disconnect, timeout, and error paths release the
session and model pin.

### Sherpa-ONNX and Vosk models

Both Docker images contain pinned `sherpa-onnx`, its `sherpa-onnx-core` native
companion, and the `vosk` runtime, but the bundled model registries do not add
model slugs for them. Supply a custom `models.json` and bind-mount it at
`/app/models.json`; the entrypoint downloads each configured Hugging Face
snapshot into `/data/models/<slug>/`.

A Sherpa transducer entry looks like this. File names are model-specific; use
the names from the selected Sherpa-compatible snapshot:

```json
{
  "models": {
    "my-sherpa-stream": {
      "repo": "<org>/<online-transducer-model>",
      "revision": "<immutable-hf-commit-sha>",
      "executor": "sherpa",
      "recognizer_factory": "from_transducer",
      "sherpa_config": {
        "tokens": "tokens.txt",
        "encoder": "encoder.onnx",
        "decoder": "decoder.onnx",
        "joiner": "joiner.onnx"
      },
      "languages": ["<locale>"]
    }
  }
}
```

`recognizer_factory` may be `from_transducer`, `from_paraformer`,
`from_wenet_ctc`, or `from_zipformer2_ctc`. `sherpa_config` is passed to that
Sherpa-ONNX `OnlineRecognizer` factory. Relative values for `tokens`, `encoder`,
`decoder`, `joiner`, `model`, `lm_model`, and `hotwords_file` resolve under the
downloaded snapshot. talkies chooses `cpu` or `cuda` provider from
`TALKIES_DEVICE` and enables endpoint detection unless those values are present
in `sherpa_config`.

A Vosk entry is smaller:

```json
{
  "models": {
    "my-vosk-stream": {
      "repo": "<org>/<vosk-model-snapshot>",
      "revision": "<immutable-hf-commit-sha>",
      "executor": "vosk",
      "languages": ["<locale>"]
    }
  }
}
```

The downloaded repo root must itself be a Vosk model directory that the Vosk
`Model` constructor can open. Vosk runs on CPU; the image/device selection does
not enable GPU decoding for this executor. Pin `revision` to an immutable
Hugging Face commit SHA. Model weights have their own licenses—verify the
chosen snapshot separately from the Apache-2.0 Vosk runtime.

## Qwen3-TTS PCM over HTTP

Real-time audio streaming is available for the `qwen3_tts` backend when
`response_format="pcm"` is requested. Instead of buffering the full utterance
before responding, the server yields raw PCM chunks as the GPU decodes them —
first audio arrives in ~200–700 ms depending on GPU and `chunk_size`.

Non-`pcm` formats (mp3, wav, opus, aac, flac) and non-Qwen3 backends (Kokoro)
are unaffected and continue to use the fully-buffered path.

---

### TTS wire format

| Property | Value |
|---|---|
| HTTP method | `POST /v1/audio/speech` |
| `response_format` | `"pcm"` |
| Transfer-Encoding | `chunked` (HTTP/1.1) |
| Content-Type | `application/octet-stream` |
| `X-Sample-Rate` header | e.g. `24000` |
| Sample encoding | Signed 16-bit little-endian (int16 LE) |
| Channels | Mono |
| Sample rate | 24 000 Hz (Qwen3-TTS fixed rate) |

Each HTTP chunk is a raw contiguous block of int16 LE samples with no WAV
header or framing. Concatenating all chunks produces a valid raw PCM stream
that can be decoded with any tool that understands the parameters above.

---

### TTS quick start

```bash
# Play audio in real time as it arrives (Linux/WSL)
curl -s -N http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
        "model": "qwen3-tts-0.6b",
        "input": "Streaming audio from Qwen3 TTS.",
        "voice": "alloy",
        "response_format": "pcm"
      }' \
  | aplay -f S16_LE -r 24000 -c 1

# Save to file (all platforms)
curl -s -N http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-tts-0.6b","input":"Hello!","voice":"alloy","response_format":"pcm"}' \
  --output speech.raw

# Convert saved raw PCM to WAV with ffmpeg
ffmpeg -f s16le -ar 24000 -ac 1 -i speech.raw speech.wav
```

Python example using `httpx`:

```python
import httpx

with httpx.stream(
    "POST",
    "http://localhost:8000/v1/audio/speech",
    json={
        "model": "qwen3-tts-0.6b",
        "input": "Hello from streaming Qwen3 TTS!",
        "voice": "alloy",
        "response_format": "pcm",
    },
    timeout=None,
) as r:
    r.raise_for_status()
    sample_rate = int(r.headers.get("x-sample-rate", 24000))
    print(f"Sample rate: {sample_rate} Hz")
    with open("speech.raw", "wb") as f:
        for chunk in r.iter_bytes():
            f.write(chunk)
```

---

### TTS configuration

| Environment variable | Default | Description |
|---|---|---|
| `TALKIES_QWEN3_STREAM_CHUNK_SIZE` | `8` | Codec steps per yielded chunk. 12 steps ≈ 1 s; 8 ≈ 667 ms. Smaller = lower time-to-first-audio but more decode overhead per chunk. |

```bash
# Example: trade slightly higher throughput for lower TTFA
docker run ... -e TALKIES_QWEN3_STREAM_CHUNK_SIZE=4 ...
```

Chunk size guidance (0.6B model, RTX 4090):

| `chunk_size` | Audio per chunk | Approx. TTFA |
|---|---|---|
| 4 | ~333 ms | ~156 ms |
| 8 | ~667 ms | ~156 ms |
| 12 | ~1 000 ms | ~156 ms |

TTFA is dominated by CUDA-graph warmup on the first call; subsequent calls are
much faster. Smaller chunks have more decode overhead but lower perceived
latency. `8` is the default as it stays real-time on all tested hardware
including Jetson AGX Orin.

---

### TTS cancellation

When a client disconnects mid-stream, the server detects it via Starlette's
`StreamingResponse` generator teardown (the async generator's `finally` block
is invoked). The implementation:

1. Sets a `threading.Event` to signal the GPU worker thread.
2. Drains the internal `asyncio.Queue` so the worker is not blocked on a
   full-queue `.put()`.
3. Awaits the worker thread task to join cleanly.

No zombie threads or unreleased GPU locks remain after a cancelled request.

---

### TTS architecture notes

```
POST /v1/audio/speech (response_format=pcm, qwen3 backend)
│
├─ server.py: speech()
│   ├─ validates model / voice / format (same as buffered path)
│   ├─ evicts sibling models
│   └─ returns StreamingResponse(_pcm_stream(), headers={"X-Sample-Rate": "24000"})
│
└─ _pcm_stream() async generator
    └─ backend.synthesize_stream(...) async generator
        ├─ pre-yield validation (text, voice) → HTTP 4xx if bad
        ├─ await get_model()  → lazy-loads + CUDA-graph warmup on first call
        └─ async with self._lock:  ← held for full stream duration
            ├─ asyncio.Queue(maxsize=4)   ← bounded backpressure
            ├─ threading.Event            ← cancellation signal
            └─ _stream_worker thread
                └─ model.generate_voice_clone_streaming(chunk_size=N)
                    yields (float32 ndarray, sample_rate, timing)
                    → np.clip + cast to int16 → bytes → queue.put()
                    → async generator yields bytes to StreamingResponse
```

The GPU lock (`self._lock`) is held for the entire stream, matching the
buffered path. Only one Qwen3-TTS synthesis (streaming or not) runs at a time;
concurrent requests queue behind the lock.

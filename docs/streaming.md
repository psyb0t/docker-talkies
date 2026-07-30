# Streaming

Talkies has two streaming surfaces: a bidirectional WebSocket protocol for live
ASR and incremental raw PCM from Qwen3 TTS over HTTP. The WebSocket route is a
Talkies protocol, separate from OpenAI-compatible file transcription.

## Live ASR over WebSocket

Connect to `ws://host:8000/v1/audio/transcriptions/stream` (or `wss://` behind
TLS). If authentication is enabled, supply `Authorization: Bearer <token>` in
the WebSocket upgrade header. Never place the token in the URL.

### Start and audio contract

The first message must be this JSON object shape:

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

| Field | Required | Default | Constraint |
|---|---|---|---|
| `type` | yes | — | exactly `start` |
| `model` | yes | — | enabled streaming-capable ASR slug |
| `encoding` | yes | — | exactly `pcm_s16le` |
| `sample_rate` | yes | — | exactly `16000` |
| `channels` | yes | — | exactly `1` |
| `language` | no | backend default | `auto` or a language/locale tag |
| `interim_results` | no | `true` | suppress `partial` events when false |
| `word_timestamps` | no | `false` | request words when a backend can provide them |

Start/control JSON is capped at 4096 UTF-8 bytes; duplicate and unknown fields
are rejected. After a `ready` event, send headerless binary PCM16LE mono frames.
Each frame must be non-empty, even-sized, and no larger than
`TALKIES_STREAM_MAX_FRAME_BYTES`. Frame boundaries do not need to align with
words or utterances.

Convert another audio format before streaming:

```bash
ffmpeg -i input.webm -f s16le -acodec pcm_s16le -ar 16000 -ac 1 input.raw
```

### Minimal Python file client

This client sends 20 ms frames from an already converted file while receiving
events concurrently. Install `websockets` in the client environment.

```python
import asyncio
import json

from websockets.asyncio.client import connect

URL = "ws://127.0.0.1:8000/v1/audio/transcriptions/stream"
FRAME_BYTES = 640  # 0.02 seconds × 16,000 samples/s × 2 bytes/sample


async def send_audio(socket):
    with open("input.raw", "rb") as audio:
        while frame := audio.read(FRAME_BYTES):
            await socket.send(frame)
            await asyncio.sleep(0.02)
    await socket.send(json.dumps({"type": "end"}))


async def receive_events(socket):
    async for message in socket:
        print(json.loads(message))


async def main():
    async with connect(URL) as socket:
        await socket.send(json.dumps({
            "type": "start", "model": "nemotron-3.5-asr-0.6b",
            "encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1,
        }))
        print(json.loads(await socket.recv()))  # ready
        await asyncio.gather(send_audio(socket), receive_events(socket))


asyncio.run(main())
```

For an authenticated server, pass `additional_headers={"Authorization":
"Bearer <token>"}` to `connect`. For microphone capture, feed the callback's
16 kHz mono PCM16 output into the same bounded sender; do not block the event
loop while capturing audio.

### Lifecycle

```text
client                                  talkies
  │── JSON start ─────────────────────────▶│
  │◀─ JSON ready ──────────────────────────│
  │── binary PCM frame(s) ────────────────▶│
  │◀─ partial / endpoint event(s) ─────────│
  │── {"type":"end"} ───────────────────▶│
  │◀─ final → stats → close(1000) ─────────│
```

`{"type":"end"}` flushes the decoder, then the server emits `final` and
`stats` with `canceled: false`. `{"type":"cancel"}` discards decoder state,
emits `stats` with `canceled: true`, and closes normally. Disconnect, timeout,
and protocol errors release the session; a disconnected client may not receive
the terminal event.

### Events

`ready` echoes the selected model and fixed PCM format. `partial`, `endpoint`,
and `final` share this structure:

```json
{
  "type": "partial",
  "revision": 3,
  "text": "current transcript hypothesis",
  "words": [{"word": "current", "start": 0.1, "end": 0.4}],
  "audio_seconds": 1.28,
  "is_final": false
}
```

- `partial` is revisable; replace the previous hypothesis instead of appending.
- `endpoint` marks a native decoder utterance boundary without closing.
- `final` is the result of `end` and sets `is_final: true`.
- `revision` increases within one connection only.
- `audio_seconds` measures accepted samples, not elapsed wall time.
- `words` is always present but can be empty.

The terminal accounting event is
`{"type":"stats","audio_seconds":8.4,"frames":420,"canceled":false}`.

### Errors and close codes

On an accepted connection, the server sends an error object before closing when
possible: `{"type":"error","code":"...","detail":"..."}`.

| Code | Meaning |
|---|---|
| 1000 | Normal `end` or `cancel` |
| 4400 | Invalid control JSON, start object, frame, or duration limit |
| 4401 | Missing or invalid bearer token during upgrade |
| 4404 | Unknown model slug |
| 4408 | Idle timeout |
| 4409 | Model cannot stream or another model is pinned |
| 4429 | Global connection limit reached |
| 4500 | Backend or internal failure |

Stable error codes include `missing_field`, `unknown_field`,
`unsupported_encoding`, `unsupported_sample_rate`, `unsupported_channels`,
`invalid_audio`, `empty_audio`, `frame_too_large`, `message_too_large`,
`duplicate_field`, `unaligned_audio`, `unknown_model`,
`streaming_not_supported`, `connection_limit`, `model_busy`,
`duration_limit`, `idle_timeout`, and `server_error`.

### Backend behaviour and pinning

| Executor | Mode | Behaviour |
|---|---|---|
| `parakeet_cpp` | native | Isolated parakeet.cpp session; runtime endpoints produce `endpoint` events |
| `sherpa` | native | Sherpa-ONNX online recognizer with endpoint detection enabled by default |
| `vosk` | native | Vosk `KaldiRecognizer`; waveform acceptance creates endpoints |
| `whisper` | rolling | Re-decodes a bounded PCM window and emits changed partial revisions |

`parakeet`, `canary_multitask`, and `canary_salm` do not implement this
protocol and return `streaming_not_supported`. Whisper has no native endpoint
event. The rolling buffer trades more context against extra repeated decoding.

All active connections in one server must use the same model. Clients may share
that model up to the connection limit, but another model cannot start or load
until the last stream releases it. During that time, model-unload endpoints
and conflicting ASR/TTS requests return HTTP 409. Limits are listed in
[Configuration](configuration.md#live-asr-and-qwen3-streaming).

### Custom Sherpa-ONNX and Vosk registries

Both images include the Sherpa-ONNX and Vosk runtimes, but their bundled
registries contain no such slug. Mount a custom registry as described in
[Models](models.md#use-a-custom-registry). A Sherpa transducer entry needs a
non-empty `sherpa_config` and may use these recognizer factories:
`from_transducer`, `from_paraformer`, `from_wenet_ctc`, or
`from_zipformer2_ctc`.

```json
{
  "models": {
    "my-sherpa-stream": {
      "repo": "<org>/<online-model>",
      "revision": "<immutable-hf-commit>",
      "executor": "sherpa",
      "recognizer_factory": "from_transducer",
      "sherpa_config": {
        "tokens": "tokens.txt",
        "encoder": "encoder.onnx",
        "decoder": "decoder.onnx",
        "joiner": "joiner.onnx"
      }
    },
    "my-vosk-stream": {
      "repo": "<org>/<vosk-model>",
      "revision": "<immutable-hf-commit>",
      "executor": "vosk"
    }
  }
}
```

Relative Sherpa paths for `tokens`, `encoder`, `decoder`, `joiner`, `model`,
`lm_model`, and `hotwords_file` resolve under the downloaded snapshot. Talkies
chooses the Sherpa provider from `TALKIES_DEVICE`. The root of a Vosk snapshot
must be a directory the Vosk `Model` constructor can open; Vosk itself decodes
on CPU. Pin the model `revision` and check the selected model's own license.

## Qwen3 PCM over HTTP

`POST /v1/audio/speech` streams only when the selected backend is Qwen3 and
`response_format` is `pcm`. It returns chunks of raw signed 16-bit little-endian
mono PCM with `Content-Type: application/octet-stream` and
`X-Sample-Rate: 24000`. No WAV header or chunk framing is included.

```bash
curl -s -N http://127.0.0.1:8000/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-tts-0.6b","input":"Hello.","voice":"alloy","response_format":"pcm"}' \
  --output speech.raw

ffmpeg -f s16le -ar 24000 -ac 1 -i speech.raw speech.wav
```

Kokoro and all non-PCM output formats use the regular buffered synthesis path.
`TALKIES_QWEN3_STREAM_CHUNK_SIZE` controls codec steps per yielded Qwen3 chunk;
the default is `8`. A Qwen3 backend serializes its own synthesis work while a
stream is active.

The protocol parser is in
[`src/talkies/asr_streaming.py`](../src/talkies/asr_streaming.py); WebSocket
wiring is in [`src/talkies/server.py`](../src/talkies/server.py).

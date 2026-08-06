# Configuration

Talkies reads environment variables at server import. Invalid values fail
startup. Durations accept seconds (`600`) or forms such as `90s`, `45m`, and
`3h30m`; comma-separated variables trim whitespace and ignore empty items.

## Authentication, device, and data

| Variable | Default | Meaning |
|---|---|---|
| `TALKIES_AUTH_TOKEN` | empty | Shared bearer token for every route except `/healthz` |
| `TALKIES_DEVICE` | image default | `auto`, `cpu`, `cuda`, or `cuda:N` |
| `TALKIES_MODELS_FILE` | `/app/models.json` | Registry JSON path |
| `TALKIES_ENABLED_MODELS` | all registry entries | Model allowlist; unknown slugs fail startup |
| `TALKIES_PRELOAD` | empty | Slugs to attempt loading at startup; unknown slugs are logged and skipped |
| `TALKIES_DATA_DIR` | `/data` | Parent of `models/`, `files/`, and `custom-voices/` |

The image sets `HF_HUB_OFFLINE=1` for the server. The entrypoint temporarily
unsets it while fetching selected snapshots, then starts the application.

## Lifecycle and file limits

| Variable | Default | Meaning |
|---|---:|---|
| `TALKIES_MODEL_TTL` | `600` seconds | Idle backend eviction; `0` disables it |
| `TALKIES_SWEEPER_INTERVAL` | `60` seconds | Idle-sweeper frequency |
| `TALKIES_LOAD_TIMEOUT` | `300` seconds | Parsed configuration reserved for a model-load timeout; the current server does not apply it |
| `TALKIES_MODEL_MAX_CONCURRENCY` | `1` | Fallback active inference requests per model, 1–1024 |
| `TALKIES_MODEL_CONCURRENCY` | empty | Per-model overrides such as `model-a=2,model-b=1` |
| `TALKIES_MAX_UPLOAD_BYTES` | `104857600` | Multipart transcription and file-stage upload cap |
| `TALKIES_MAX_DOWNLOAD_BYTES` | `1073741824` | Remote `file_path` download cap |
| `TALKIES_BLOCK_PRIVATE_DOWNLOADS` | `false` | Block private, loopback, link-local, multicast, and metadata URL targets |

## File-transcription VAD

| Variable | Default | Meaning |
|---|---:|---|
| `TALKIES_VAD_CHUNK_THRESHOLD` | `30.0` seconds | Audio above this duration enters VAD segmentation |
| `TALKIES_VAD_MAX_SPEECH` | `28.0` seconds | Maximum detected speech region length |
| `TALKIES_VAD_MIN_SILENCE_MS` | `500` | Silence needed to split a region |
| `TALKIES_VAD_SPEECH_PAD_MS` | `200` | Padding added around detected speech |
| `TALKIES_VAD_THRESHOLD` | `0.5` | Speech-probability threshold |

Audio above the threshold enters VAD segmentation before backend transcription.

## Live ASR and Qwen3 streaming

| Variable | Default | Valid values | Meaning |
|---|---:|---|---|
| `TALKIES_STREAM_MAX_CONNECTIONS` | `4` | 1–1024 | Active WebSockets across the server |
| `TALKIES_STREAM_MAX_FRAME_BYTES` | `65536` | 2–16777216 | Largest accepted binary PCM frame |
| `TALKIES_STREAM_MAX_BUFFER_SECONDS` | `5.0` | 0.1–300 | Rolling Whisper buffer size |
| `TALKIES_STREAM_IDLE_TIMEOUT` | `30s` | 1s–1h | Longest wait between client messages |
| `TALKIES_STREAM_MAX_DURATION` | `4h` | 1s–24h | Accepted PCM duration per connection |
| `TALKIES_QWEN3_STREAM_CHUNK_SIZE` | `8` | — | Codec steps per Qwen3 PCM chunk |

Startup rejects a rolling-Whisper buffer that cannot hold one maximum PCM
frame. Full protocol details are in [Streaming](streaming.md).

`max_concurrency` may also be set on any model registry entry. Precedence is
`TALKIES_MODEL_CONCURRENCY`, registry entry, then
`TALKIES_MODEL_MAX_CONCURRENCY`. The limit counts WebSocket ASR, HTTP ASR, MCP
ASR, buffered TTS, and streaming TTS together. Excess requests fail immediately
with HTTP 429 or a WebSocket `connection_limit` error; Talkies does not build an
unbounded inference queue. Invalid, duplicate, unknown, or disabled model slugs
in the override fail startup.

## Logging

`TALKIES_LOG_LEVEL` defaults to `info`; `LOG_LEVEL` is a fallback. User-facing
levels are `debug`, `info`, `warn`, `error`, and `fatal`; `warning` and
`critical` are also accepted. Logs are structured JSON on stdout. `debug`
includes full ASR transcripts and TTS text/instructions, so do not use it where
that content is sensitive.

Configuration parsing and validation are implemented in
[`src/talkies/config.py`](../src/talkies/config.py).

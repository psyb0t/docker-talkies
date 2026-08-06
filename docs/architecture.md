# Architecture

Talkies is one FastAPI application. A model registry selects lazy backend
objects; a Docker entrypoint obtains their snapshots before the app starts.
The public server is implemented in [`src/talkies/server.py`](../src/talkies/server.py).

## Startup and model storage

```text
models.json / models-cpu.json
        │
        ├─ TALKIES_ENABLED_MODELS allowlist
        ▼
entrypoint.sh ── snapshot download ──► /data/models/<slug>/
        │
        ▼
FastAPI lifespan ── optional TALKIES_PRELOAD ──► backend instances
```

The CPU Dockerfile installs `models-cpu.json` at `/app/models.json`; the CUDA
Dockerfile installs `models.json`. Before launching Python, `entrypoint.sh`
creates the `models`, `files`, and `custom-voices` directories and downloads
only the enabled registry snapshots. A non-empty target model directory is
considered cached. The server normally runs with Hugging Face offline mode
enabled after that prefetch.

## Request flow

```text
client
  │
  ├─ POST /v1/audio/transcriptions
  │      │ multipart file or staged/remote file_path
  │      ▼
  │   normalize audio → optional VAD segments → selected ASR backend → response renderer
  │
  ├─ POST /v1/audio/speech
  │      │ JSON model/input/voice
  │      ▼
  │   selected TTS backend → PCM → ffmpeg encoder or Qwen3 PCM stream
  │
  └─ WS /v1/audio/transcriptions/stream
         │ JSON start + binary PCM frames
         ▼
      streaming session → transcript events
```

The same server function owns HTTP transcription and MCP transcription so they
share model selection, input resolution, VAD handling, and response rendering.
MCP injects those shared operations from `server.py` into
[`src/talkies/mcp_server.py`](../src/talkies/mcp_server.py).

## Backends and resource control

`src/talkies/models/__init__.py` maps each registry executor to its backend.
An ASR backend has `transcribe`; a TTS backend has `synthesize` and `voices`;
a live-ASR backend also has `start_stream`.

Backends load on their first request. Before loading a requested model, Talkies
unloads loaded sibling models. The idle sweeper unloads unused backends after
`TALKIES_MODEL_TTL` unless it is zero. This keeps one process usable on limited
memory, but clients should expect a cold-load delay after eviction.

One admission controller counts active inference by model across HTTP ASR, MCP
ASR, live WebSocket ASR, buffered TTS, and streaming TTS. It unloads siblings
before the first admitted request, refuses conflicting model operations with
HTTP 409, and rejects work above `max_concurrency` with HTTP 429. Reservations
release in every normal, cancellation, disconnect, iterator-close, and error
path. The independent global WebSocket cap is applied to live ASR as well.

## Data boundaries

`/data/files` holds staged uploads and remote-file cache entries. `/data/custom-voices`
holds Qwen3 base-mode voice samples. Both are visible to every caller that can
reach a single Talkies instance; they are operational data, not an access-
controlled user filesystem. Authentication is a server-wide bearer token.

The source modules behind these boundaries are
[`src/talkies/files.py`](../src/talkies/files.py),
[`src/talkies/downloads.py`](../src/talkies/downloads.py), and
[`src/talkies/auth.py`](../src/talkies/auth.py). Deployment considerations are
in [Operations and security](operations.md).

# talkies

[![CI](https://github.com/psyb0t/docker-talkies/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/psyb0t/docker-talkies/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/docker-talkies/badges/version.svg)](https://github.com/psyb0t/docker-talkies/releases)
[![license](https://raw.githubusercontent.com/psyb0t/docker-talkies/badges/license.svg)](LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/psyb0t/talkies?style=flat-square)](https://hub.docker.com/r/psyb0t/talkies)

Self-hosted speech services in one Docker image: OpenAI-compatible file
transcription and text-to-speech, Talkies live ASR over WebSocket, file
staging, model lifecycle controls, and an MCP endpoint for ASR workflows.

## Start here

Restrict the first boot to the models you need; otherwise the entrypoint
downloads every model in the bundled registry.

```bash
docker run --rm -it --name talkies \
  -p 127.0.0.1:8000:8000 \
  -v "$PWD/talkies-data:/data" \
  -e TALKIES_ENABLED_MODELS=whisper-large-v3-turbo,kokoro-82m \
  psyb0t/talkies:latest

curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/v1/audio/transcriptions \
  -F "file=@/path/to/clip.wav" \
  -F "model=whisper-large-v3-turbo"
```

For CUDA-only models and Qwen3 TTS, use `psyb0t/talkies:latest-cuda` with
`--gpus all`. The loopback port mapping keeps the service local; see
[Getting started](docs/getting-started.md) for first boot and authentication.

## What it provides

| Surface | Purpose | Reference |
|---|---|---|
| `POST /v1/audio/transcriptions` | File transcription and subtitles | [HTTP API](docs/api.md#transcription) |
| `WS /v1/audio/transcriptions/stream` | Live 16 kHz PCM ASR | [Streaming](docs/streaming.md#live-asr-over-websocket) |
| `POST /v1/audio/speech` | Speech synthesis in six formats | [HTTP API](docs/api.md#speech) |
| `GET/PUT/DELETE /v1/files/*` | Server-side file staging | [HTTP API](docs/api.md#file-staging) |
| `/api/ps`, `/unload` | Model inspection and eviction | [Operations](docs/operations.md#model-lifecycle) |
| `/v1/mcp` | Streamable HTTP MCP with ASR/file tools | [HTTP API](docs/api.md#mcp) |

The HTTP transcription and speech routes use the corresponding OpenAI wire
shapes where those contracts overlap. Streaming ASR, files, lifecycle controls,
and MCP are Talkies extensions.

## Models at a glance

- CPU: two Whisper models, Canary-180M-Flash, Nemotron ASR via parakeet.cpp,
  four English Sherpa-ONNX Zipformer choices, Vosk small English, and two
  Kokoro TTS backends.
- CUDA: the CPU set plus Parakeet-TDT, Canary 1B/Qwen ASR, and five Qwen3 TTS
  variants.
- Live ASR: bundled Nemotron, Sherpa-ONNX, and Vosk are native; bundled Whisper
  is a bounded rolling decoder. Sherpa and Vosk also work through the
  OpenAI-compatible file-transcription endpoint.
- Per-model concurrency limits cover WebSocket, HTTP, MCP, ASR, and TTS; the
  bundled Nemotron CPU and CUDA entries admit two requests.
- Streaming TTS: Qwen3 returns incremental raw PCM for
  `response_format="pcm"`; other TTS formats and Kokoro are buffered.

Exact slugs, executors, and registry format: [Models and registries](docs/models.md).

## Documentation

| Guide | Contents |
|---|---|
| [Getting started](docs/getting-started.md) | Run CPU/CUDA, persist data, authenticate, verify |
| [Models and registries](docs/models.md) | Bundled slugs, image availability, custom registries |
| [Architecture](docs/architecture.md) | Request flow, backend selection, on-disk layout |
| [HTTP API](docs/api.md) | Requests, responses, files, lifecycle, MCP |
| [Streaming](docs/streaming.md) | Live ASR protocol, streaming backends, PCM TTS |
| [Configuration](docs/configuration.md) | Supported environment variables and limits |
| [Operations and security](docs/operations.md) | Exposure, model memory, data retention, logs |
| [Development](docs/development.md) | Make targets, test suites, image builds |

## Agent integrations

The [Talkies skill](.agents/skills/talkies) teaches agents to use the HTTP,
WebSocket, and MCP surfaces. Install it through the shared `psyb0t` marketplace
or let Codex discover it directly from this checkout.

### Claude Code

```bash
claude plugin marketplace add psyb0t/agents
claude plugin install talkies@psyb0t
```

Claude Code prompts for the Talkies URL and, when enabled, the bearer token;
the sensitive token is stored through the client's protected configuration.

### Codex

```bash
codex plugin marketplace add psyb0t/agents
codex plugin add talkies@psyb0t
```

A marketplace install invokes the skill as `$talkies:talkies`. Codex also
discovers `.agents/skills/talkies` directly in this repository, where it is
invoked as `$talkies` without installation.

### OpenClaw

The skill and MCP bridge are published through ClawHub:

```bash
openclaw skills install @psyb0t/talkies
openclaw plugins install clawhub:@psyb0t/talkies
```

The bridge connects local stdio MCP clients to a running Talkies `/v1/mcp`
endpoint. Set `TALKIES_URL` and, when authentication is enabled,
`TALKIES_AUTH_TOKEN`.

## Security in one minute

`TALKIES_AUTH_TOKEN` enables a shared bearer token for every HTTP and WebSocket
route except `/healthz`. It is unset by default. Keep the port loopback-only or
put Talkies behind TLS, authentication, and rate limiting. If untrusted callers
can supply remote `file_path` URLs, set `TALKIES_BLOCK_PRIVATE_DOWNLOADS=true`.
See [Operations and security](docs/operations.md) for the complete posture.

## Development

```bash
make check                 # lint + unit tests in the dev image
make test-streaming        # real CPU native WebSocket ASR test
make test-streaming-custom # real CPU Sherpa/Vosk WebSocket + HTTP tests
make test-streaming-custom-cuda # real CUDA Sherpa WebSocket + HTTP test
make build-all             # CPU and CUDA production images
```

Talkies is released under the [WTFPL](LICENSE). Model weights are downloaded at
runtime and have their own terms; image component notices are in
[THIRD_PARTY.md](THIRD_PARTY.md). Release notes are in [CHANGELOG.md](CHANGELOG.md).

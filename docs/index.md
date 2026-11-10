---
title: "Home"
description: "Talkies is a self-hosted speech API in one Docker image: OpenAI-compatible transcription and TTS, live WebSocket ASR, file staging and MCP."
nav_order: 1
permalink: /
seo:
  type: WebSite
---

# talkies

Talkies is a self-hosted speech service in one Docker image. It puts open ASR
and TTS models (Whisper, Parakeet, Canary, Nemotron, Sherpa-ONNX, Vosk, two
phoneme recognizers, Kokoro, Qwen3-TTS and Chatterbox Turbo) behind one HTTP
server, loads them on first use, and keeps one model family in memory at a
time.

| Route | What it does |
|---|---|
| `POST /v1/audio/transcriptions` | OpenAI-compatible file transcription and subtitles |
| `POST /v1/audio/speech` | OpenAI-compatible speech synthesis |
| `WS /v1/audio/transcriptions/stream` | Live 16 kHz PCM ASR |
| `/v1/files` | Server-side file staging |
| `/api/ps`, `/unload` | Model inspection and eviction |
| `/v1/mcp` | Streamable HTTP MCP with ASR and file tools |

## Quick start

```bash
docker run --rm -it --name talkies \
  -p 127.0.0.1:8000:8000 \
  -v "$PWD/talkies-data:/data" \
  -e TALKIES_ENABLED_MODELS=whisper-large-v3-turbo,kokoro-82m \
  psyb0t/talkies:latest
```

Without `TALKIES_ENABLED_MODELS` the first boot downloads every model in the
bundled registry. [Getting started](getting-started.md) covers the CUDA image,
a first request and authentication.

## Where to go next

| Page | Read it for |
|---|---|
| [Getting started](getting-started.md) | Run the CPU or CUDA image, persist data, authenticate, verify |
| [Models and registries](models.md) | Bundled slugs, which image has them, registry fields, custom registries |
| [HTTP API](api.md) | Transcription, speech, files, lifecycle routes and MCP tools |
| [Streaming](streaming.md) | The live ASR WebSocket protocol and Qwen3 PCM streaming |
| [Configuration](configuration.md) | Every environment variable, default and limit |
| [Operations and security](operations.md) | Network exposure, model memory, data retention, logs |
| [Agent integrations](agent-integrations.md) | The agent skill and the Claude Code, Codex and OpenClaw plugins |
| [Architecture](architecture.md) | Request flow, backend selection, on-disk layout |
| [Development](development.md) | Make targets, test suites, image builds, this site |

The code lives at
[psyb0t/docker-talkies](https://github.com/psyb0t/docker-talkies), and release
notes are in the
[CHANGELOG](https://github.com/psyb0t/docker-talkies/blob/main/CHANGELOG.md).
Talkies is released under the
[WTFPL](https://github.com/psyb0t/docker-talkies/blob/main/LICENSE). Model
weights download at runtime and carry their own licenses.

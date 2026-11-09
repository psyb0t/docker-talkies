# talkies

[![CI](https://github.com/psyb0t/docker-talkies/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/psyb0t/docker-talkies/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/docker-talkies/badges/version.svg)](https://github.com/psyb0t/docker-talkies/releases)
[![license](https://raw.githubusercontent.com/psyb0t/docker-talkies/badges/license.svg)](https://github.com/psyb0t/docker-talkies/blob/main/LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/psyb0t/talkies?style=flat-square)](https://hub.docker.com/r/psyb0t/talkies)

Self-hosted speech services in one Docker image: OpenAI-compatible file
transcription and text-to-speech, Talkies live ASR over WebSocket, file
staging, model lifecycle controls, and an MCP endpoint for ASR workflows.

## Contents

- [Start here](#start-here)
- [What it provides](#what-it-provides)
- [Models at a glance](#models-at-a-glance)
  - [Reading phonemes](#reading-phonemes)
  - [Prompting Chatterbox with emotion](#prompting-chatterbox-with-emotion)
- [Documentation](#documentation)
- [Agent integrations](#agent-integrations)
- [Security in one minute](#security-in-one-minute)
- [Development](#development)
- [Sponsor](#sponsor)

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

For the CUDA-only models (Parakeet-TDT, the larger Canary models, Qwen3 TTS
and Chatterbox Turbo), use `psyb0t/talkies:latest-cuda` with `--gpus all`. The
loopback port mapping keeps the service local; see
[Getting started](https://psyb0t.github.io/docker-talkies/getting-started/)
for first boot and authentication.

## What it provides

| Surface | Purpose | Reference |
|---|---|---|
| `POST /v1/audio/transcriptions` | File transcription and subtitles | [HTTP API](https://psyb0t.github.io/docker-talkies/api/#transcription) |
| `WS /v1/audio/transcriptions/stream` | Live 16 kHz PCM ASR | [Streaming](https://psyb0t.github.io/docker-talkies/streaming/#live-asr-over-websocket) |
| `POST /v1/audio/speech` | Speech synthesis in six formats | [HTTP API](https://psyb0t.github.io/docker-talkies/api/#speech) |
| `GET /v1/models` | Enabled slugs and their modality | [HTTP API](https://psyb0t.github.io/docker-talkies/api/#discovery-and-health) |
| `GET /v1/audio/voices` | Per-model voice catalog with origin tags | [Models](https://psyb0t.github.io/docker-talkies/models/#bundled-tts-models) |
| `GET/PUT/DELETE /v1/files/*` | Server-side file staging | [HTTP API](https://psyb0t.github.io/docker-talkies/api/#file-staging) |
| `/api/ps`, `/unload` | Model inspection and eviction | [Operations](https://psyb0t.github.io/docker-talkies/operations/#model-lifecycle) |
| `/v1/mcp` | Streamable HTTP MCP with ASR/file tools | [HTTP API](https://psyb0t.github.io/docker-talkies/api/#mcp) |
| `GET /healthz` | Liveness probe; the only unauthenticated route | [Operations](https://psyb0t.github.io/docker-talkies/operations/#network-exposure) |

The HTTP transcription and speech routes use the corresponding OpenAI wire
shapes where those contracts overlap. Streaming ASR, files, lifecycle controls,
and MCP are Talkies extensions.

## Models at a glance

- CPU: two Whisper models, Canary-180M-Flash, Nemotron ASR via parakeet.cpp,
  four English Sherpa-ONNX Zipformer choices, Vosk small English, two phoneme
  recognizers, and two Kokoro TTS backends.
- CUDA: the CPU set plus Parakeet-TDT, Canary 1B/Qwen ASR, five Qwen3 TTS
  variants, and Chatterbox Turbo.
- Live ASR: bundled Nemotron, Sherpa-ONNX, and Vosk are native; bundled Whisper
  is a bounded rolling decoder. Sherpa and Vosk also work through the
  OpenAI-compatible file-transcription endpoint.
- Phoneme recognition: `wav2vec2-xlsr-53-espeak` and `zipa-ipa` return the IPA
  phones that were spoken, not words, with no language model correcting them
  toward the nearest dictionary entry. Same transcription endpoint and
  timestamp options as the other ASR models; see
  [Phoneme recognition](https://psyb0t.github.io/docker-talkies/models/#phoneme-recognition).
- Per-model concurrency limits cover WebSocket, HTTP, MCP, ASR, and TTS; the
  bundled Nemotron CPU and CUDA entries admit two requests.
- Streaming TTS: Qwen3 returns incremental raw PCM for
  `response_format="pcm"`; other TTS formats and Kokoro are buffered.
- Expressive TTS: Chatterbox Turbo (English) takes 19 inline tags such as
  `[sigh]`, `[whispering]` and `[laugh]` directly in the input text. Its output
  carries a neural watermark by default; set `TALKIES_CHATTERBOX_WATERMARK` to
  false to emit unmarked audio.
- Voice cloning: drop a `.wav` into `/data/custom-voices` and it appears on
  `GET /v1/audio/voices`. Qwen3 pairs it with an optional sibling `.txt`
  transcript; Chatterbox needs only the clip, longer than five seconds.

Exact slugs, executors, tag list, and registry format:
[Models and registries](https://psyb0t.github.io/docker-talkies/models/).

### Reading phonemes

`wav2vec2-xlsr-53-espeak` and `zipa-ipa` use the same transcription call as
every other ASR slug; only the model changes. `text` comes back as a
space-separated IPA phone stream rather than words, and no language model
corrects a mispronunciation toward a real word.

```bash
curl -s http://127.0.0.1:8000/v1/audio/transcriptions \
  -F "file=@/path/to/clip.wav" \
  -F "model=zipa-ipa"
# {"text": "a ɪ m k ə n f j u z ...", ...}
```

Add `-F "response_format=verbose_json"` to get each phone as a `words` entry
with `start` and `end` in seconds.

### Prompting Chatterbox with emotion

Tags go inline in `input`, in square brackets, lowercase. They are real tokens
in the model's tokenizer, so only these 19 do anything; any other bracketed
word is spoken as literal text:

```
[angry] [fear] [surprised] [whispering] [advertisement] [dramatic] [narration]
[crying] [happy] [sarcastic] [clear throat] [sigh] [shush] [cough] [groan]
[sniff] [gasp] [chuckle] [laugh]
```

```bash
curl -s http://127.0.0.1:8000/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "chatterbox-turbo",
        "voice": "builtin",
        "input": "Oh, that is hilarious. [chuckle] Anyway [sigh] back to work.",
        "response_format": "mp3"
      }' --output out.mp3
```

Swap `"voice"` for the name of any `.wav` you dropped in `/data/custom-voices`
(extension stripped) to speak the same line in a cloned voice.

## Documentation

The full documentation is at
**[psyb0t.github.io/docker-talkies](https://psyb0t.github.io/docker-talkies/)**.

| Guide | Contents |
|---|---|
| [Getting started](https://psyb0t.github.io/docker-talkies/getting-started/) | Run CPU/CUDA, persist data, authenticate, verify |
| [Models and registries](https://psyb0t.github.io/docker-talkies/models/) | Bundled slugs, image availability, registry fields, custom registries |
| [HTTP API](https://psyb0t.github.io/docker-talkies/api/) | Requests, responses, errors, files, lifecycle, MCP |
| [Streaming](https://psyb0t.github.io/docker-talkies/streaming/) | Live ASR protocol, streaming backends, PCM TTS |
| [Configuration](https://psyb0t.github.io/docker-talkies/configuration/) | Supported environment variables and limits |
| [Operations and security](https://psyb0t.github.io/docker-talkies/operations/) | Exposure, model memory, data retention, logs |
| [Agent integrations](https://psyb0t.github.io/docker-talkies/agent-integrations/) | Skill, Claude Code, Codex and OpenClaw plugins, MCP bridge |
| [Architecture](https://psyb0t.github.io/docker-talkies/architecture/) | Request flow, backend selection, on-disk layout |
| [Development](https://psyb0t.github.io/docker-talkies/development/) | Make targets, test suites, image builds, docs site |

## Agent integrations

The [Talkies skill](https://github.com/psyb0t/docker-talkies/tree/main/.agents/skills/talkies)
teaches agents to use the HTTP, WebSocket, and MCP routes. It installs as a
Claude Code or Codex plugin from the shared `psyb0t` marketplace, and OpenClaw
installs the skill and a stdio MCP bridge from ClawHub. For Claude Code:

```bash
claude plugin marketplace add psyb0t/agents
claude plugin install talkies@psyb0t
```

The Codex and OpenClaw commands and the bridge settings are in
[Agent integrations](https://psyb0t.github.io/docker-talkies/agent-integrations/).

## Security in one minute

`TALKIES_AUTH_TOKEN` enables a shared bearer token for every HTTP and WebSocket
route except `/healthz`. It is unset by default. Keep the port loopback-only or
put Talkies behind TLS, authentication, and rate limiting. If untrusted callers
can supply remote `file_path` URLs, set `TALKIES_BLOCK_PRIVATE_DOWNLOADS=true`.
See [Operations and security](https://psyb0t.github.io/docker-talkies/operations/)
for the complete posture.

## Development

```bash
make check                 # lint + unit tests in the dev image
make lint                  # flake8 + mypy only
make test-unit             # fast offline unit tests
make run                   # run the CPU image locally
make test-streaming        # real CPU native WebSocket ASR test
make test-streaming-custom # real CPU Sherpa/Vosk WebSocket + HTTP tests
make test-streaming-custom-cuda # real CUDA Sherpa WebSocket + HTTP test
make compile-heavy         # regenerate the hash-locked ML requirements
make build-all             # CPU and CUDA production images
make docs                  # build the documentation site into .testing/docs-site
```

`make help` lists every target, and
[Development](https://psyb0t.github.io/docker-talkies/development/) describes each one.

Talkies is released under the
[WTFPL](https://github.com/psyb0t/docker-talkies/blob/main/LICENSE).
Model weights are downloaded at runtime and have their own terms; image
component notices are in
[THIRD_PARTY.md](https://github.com/psyb0t/docker-talkies/blob/main/THIRD_PARTY.md).
Release notes are in
[CHANGELOG.md](https://github.com/psyb0t/docker-talkies/blob/main/CHANGELOG.md).

## Sponsor

<a href="https://www.buymeacoffee.com/psyb0t"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-blue.png" alt="Buy Me a Coffee" height="60" width="217"></a>

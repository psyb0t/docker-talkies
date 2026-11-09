---
title: "HTTP API"
description: "Talkies HTTP routes: OpenAI-compatible transcription and speech, voices, file staging, model lifecycle, error statuses and the MCP tools."
nav_order: 4
---

# HTTP API

Talkies serves HTTP on port 8000. When `TALKIES_AUTH_TOKEN` is set, send
`Authorization: Bearer <token>` on every request except `/healthz`.

## Discovery and health

| Route | Result |
|---|---|
| `GET /healthz` | `{"ok": true, "device": "...", "models": ["..."]}` |
| `GET /v1/models` | OpenAI-style enabled models with `modality` and `max_concurrency` |
| `GET /v1/audio/voices` | `{"voices": [...]}` with `voice`, `model`, `default`, and optional `origin` |

Voice names are scoped to their model. For the Qwen3 base voice-cloning
models and Chatterbox Turbo, `origin` marks a voice as `builtin` (shipped in
the image or checkpoint) or `custom` (found under `/data/custom-voices`).

## Transcription

`POST /v1/audio/transcriptions` accepts multipart form data. Supply exactly one
of `file` or `file_path`.

| Field | Required | Default | Behaviour |
|---|---|---|---|
| `file` | one of file/path | none | Multipart audio upload, limited by `TALKIES_MAX_UPLOAD_BYTES` |
| `file_path` | one of file/path | none | Relative staged path or `http(s)` URL fetched and cached by the server |
| `model` | yes | none | Enabled ASR slug; unknown returns 404 |
| `language` | no | registry default | Source-language hint |
| `response_format` | no | `json` | `json`, `text`, `verbose_json`, `srt`, or `vtt` |
| `diarization` | no | `false` | `true` splits stereo left/right channels into speakers `L` and `R` |
| `timestamp_granularities[]` | no | empty | Accepted for compatibility; verbose JSON emits available segments and words |
| `prompt`, `temperature` | no | none | Accepted for OpenAI compatibility and ignored |

```bash
curl -s http://127.0.0.1:8000/v1/audio/transcriptions \
  -F "file=@lecture.mp3" \
  -F "model=whisper-large-v3-turbo" \
  -F "response_format=verbose_json"
```

| Format | Response |
|---|---|
| `json` | `{"text": "..."}` |
| `text` | `text/plain` transcript |
| `verbose_json` | JSON with `task`, `language`, `duration`, `text`, `segments`, and `words` |
| `srt` | `application/x-subrip` subtitles |
| `vtt` | `text/vtt` subtitles |

Verbose JSON always contains `segments` and `words`; an executor without
alignment data returns empty arrays. Stereo diarization only accepts 2-channel
audio. It transcribes each channel independently, merges the timelines, and
marks segments and words with `channel: "L"` or `channel: "R"`.

For audio longer than the configured threshold, Talkies normalizes audio and
uses VAD segmentation before calling the ASR backend. See [Configuration](configuration.md#file-transcription-vad).

The four bundled English Sherpa-ONNX variants and bundled Vosk small English
are valid `model` values here as well as on the live WebSocket route. Their
file requests are decoded through a short-lived native stream after
normalization; see [Streaming](streaming.md#sherpa-onnx-and-vosk) for the
engine-specific behavior and [Models](models.md#sherpa-onnx-and-vosk-choices)
for selectable slugs.

If the model has no free inference slot, transcription and speech requests
return HTTP 429. A request for a different model while inference is active
returns HTTP 409 rather than evicting the active backend. Every status is
listed under [Errors](#errors).

## Speech

`POST /v1/audio/speech` accepts a JSON body. It returns encoded audio bytes or,
for Qwen3 with `response_format: "pcm"`, an incremental raw PCM response.

| Field | Required | Default | Rules |
|---|---|---|---|
| `model` | yes | none | Enabled TTS slug |
| `input` | yes | none | Non-empty source text |
| `voice` | no | model default | Must exist in that model's voice catalog |
| `response_format` | no | `mp3` | `mp3`, `opus`, `aac`, `flac`, `wav`, or `pcm` |
| `speed` | no | `1.0` | 0.25 to 4.0; backend support varies |
| `instructions` | no | none | Qwen3 mode-specific instruction/voice text; Kokoro ignores it |
| `language` | no | model default | Qwen3 language label |
| `temperature` | no | backend default | 0 to 2, Qwen3 sampling option |
| `top_k` | no | backend default | 1 to 1000, Qwen3 sampling option |
| `top_p` | no | backend default | 0 to 1, Qwen3 sampling option |
| `repetition_penalty` | no | backend default | 0.5 to 2, Qwen3 sampling option |
| `max_new_tokens` | no | backend default | 1 to 2048, Qwen3 sampling option |
| `do_sample` | no | backend default | Qwen3 sampling option |

```bash
curl -s http://127.0.0.1:8000/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"kokoro-82m","input":"Hello.","voice":"af_heart","response_format":"wav"}' \
  --output hello.wav
```

`pcm` is headerless signed 16-bit little-endian mono audio. All formats except
Qwen3 PCM are generated as a complete response. Qwen3 PCM sends
`Content-Type: application/octet-stream` and `X-Sample-Rate: 24000`; see
[Qwen3 streaming TTS](streaming.md#qwen3-pcm-over-http).

## File staging

The staging area is under `$TALKIES_DATA_DIR/files`. Leading slashes are
normalized away; traversal segments, backslashes, null bytes, and paths whose
resolved target escapes the staging root are rejected.

| Route | Behaviour |
|---|---|
| `GET /v1/files` | List staged files with paths, sizes, and modification times |
| `PUT /v1/files/{path}` | Store raw request bytes; returns 201 with path and size |
| `GET /v1/files/{path}` | Return a staged file |
| `DELETE /v1/files/{path}` | Delete a staged file and prune empty parents |

```bash
curl --upload-file clip.wav http://127.0.0.1:8000/v1/files/jobs/clip.wav
curl -s http://127.0.0.1:8000/v1/audio/transcriptions \
  -F 'file_path=jobs/clip.wav' \
  -F 'model=whisper-large-v3-turbo'
```

Staging is shared by all callers of an instance. It is not a per-user store;
follow the data-retention guidance in [Operations](operations.md#remote-files-and-staged-data).

## Model lifecycle

| Route | Behaviour |
|---|---|
| `GET /api/ps` | Loaded models with `id`, `repo`, `loaded`, `idle_seconds`, `active_requests`, and `max_concurrency` |
| `DELETE /api/ps/{model_id}` | Unload one loaded model; 404 if unknown or not loaded |
| `POST /unload` | Unload every loaded model; returns `{"unloaded": [...]}` |

Any active inference request pins its model: HTTP or MCP transcription,
speech, or a live ASR stream. `DELETE /api/ps/{model_id}` returns HTTP 409
while the target model has active requests, and `POST /unload` returns 409
while any model has them. A request that needs a different model also gets
409 until the pin is released.

## Errors

Error responses are JSON with a `detail` field: a message string, or a list of
field errors for 422.

| Status | When |
|---|---|
| 400 | Both or neither of `file` and `file_path`; a TTS slug on the transcription route or an ASR slug on the speech route; audio ffmpeg cannot decode; `diarization` on audio that is not 2-channel; an invalid staged path or a refused URL download; an unknown voice, an unsupported `response_format`, or empty `input`; a backend rejecting the request, such as a Chatterbox reference clip of 5 seconds or less |
| 401 | `TALKIES_AUTH_TOKEN` is set and the bearer token is missing or wrong |
| 404 | Unknown model slug; a staged `file_path` or `/v1/files/{path}` that does not exist |
| 409 | Another model has active inference requests, or an unload targets a model in use |
| 413 | An upload larger than `TALKIES_MAX_UPLOAD_BYTES` |
| 422 | A request body that fails field validation, such as `speed` outside 0.25 to 4.0 |
| 429 | The model's `max_concurrency` is reached |
| 500 | ffmpeg failed to encode the speech output |
| 503 | A TTS backend cannot list its voices or find its model files |

A Qwen3 PCM stream has already sent its headers when synthesis starts, so a
failure during the stream ends the response early instead of returning a
status.

## MCP

Talkies mounts a stateless Streamable HTTP MCP server at `/v1/mcp`; both the
bare path and the trailing-slash form work. It exposes six tools:

| Tool | Arguments | Returns |
|---|---|---|
| `list_models` | none | ASR models with `slug`, `executor`, `default_source_lang`, `default_target_lang`, `default_task`, and `loaded` |
| `transcribe` | `file_path`, `model`, optional `language`, `response_format` (default `json`), `diarization` (default `false`) | The transcript as a string; JSON formats are JSON-encoded |
| `list_files` | none | The same listing as `GET /v1/files` |
| `put_file` | `path`, `content_base64` | `{"path": ..., "size": ...}` |
| `get_file` | `path` | `path`, `size`, and `content_base64` |
| `delete_file` | `path` | `{"deleted": ...}` |

`transcribe` takes an `http(s)` URL or a staged path, not an upload, and runs
the same pipeline as the HTTP route. File bytes travel as base64 in JSON and
are capped at `TALKIES_MAX_UPLOAD_BYTES` in both directions. A failed tool call
returns a tool error with a readable message. The MCP server offers ASR and
file tools only; use the HTTP speech route for TTS.

The HTTP routes live in [`src/talkies/server.py`](https://github.com/psyb0t/docker-talkies/blob/main/src/talkies/server.py);
the MCP tools live in [`src/talkies/mcp_server.py`](https://github.com/psyb0t/docker-talkies/blob/main/src/talkies/mcp_server.py).

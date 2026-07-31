# HTTP API

Talkies serves HTTP on port 8000. When `TALKIES_AUTH_TOKEN` is set, send
`Authorization: Bearer <token>` on every request except `/healthz`.

## Discovery and health

| Route | Result |
|---|---|
| `GET /healthz` | `{"ok": true, "device": "...", "models": ["..."]}` |
| `GET /v1/models` | OpenAI-style list of enabled models, with `modality` set to `asr` or `tts` |
| `GET /v1/audio/voices` | `{"voices": [...]}` with `voice`, `model`, `default`, and optional `origin` |

Voice names are scoped to their model. For Qwen3 base voice-cloning models,
`origin` identifies a discovered voice as `builtin` or `custom`.

## Transcription

`POST /v1/audio/transcriptions` accepts multipart form data. Supply exactly one
of `file` or `file_path`.

| Field | Required | Default | Behaviour |
|---|---|---|---|
| `file` | one of file/path | — | Multipart audio upload, limited by `TALKIES_MAX_UPLOAD_BYTES` |
| `file_path` | one of file/path | — | Relative staged path or `http(s)` URL fetched and cached by the server |
| `model` | yes | — | Enabled ASR slug; unknown returns 404 |
| `language` | no | registry default | Source-language hint |
| `response_format` | no | `json` | `json`, `text`, `verbose_json`, `srt`, or `vtt` |
| `diarization` | no | `false` | `true` splits stereo left/right channels into speakers `L` and `R` |
| `timestamp_granularities[]` | no | empty | Accepted for compatibility; verbose JSON emits available segments and words |
| `prompt`, `temperature` | no | — | Accepted for OpenAI compatibility and ignored |

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

## Speech

`POST /v1/audio/speech` accepts a JSON body. It returns encoded audio bytes or,
for Qwen3 with `response_format: "pcm"`, an incremental raw PCM response.

| Field | Required | Default | Rules |
|---|---|---|---|
| `model` | yes | — | Enabled TTS slug |
| `input` | yes | — | Non-empty source text |
| `voice` | no | model default | Must exist in that model's voice catalog |
| `response_format` | no | `mp3` | `mp3`, `opus`, `aac`, `flac`, `wav`, or `pcm` |
| `speed` | no | `1.0` | 0.25–4.0; backend support varies |
| `instructions` | no | — | Qwen3 mode-specific instruction/voice text; Kokoro ignores it |
| `language` | no | model default | Qwen3 language label |
| `temperature` | no | backend default | 0–2, Qwen3 sampling option |
| `top_k` | no | backend default | 1–1000, Qwen3 sampling option |
| `top_p` | no | backend default | 0–1, Qwen3 sampling option |
| `repetition_penalty` | no | backend default | 0.5–2, Qwen3 sampling option |
| `max_new_tokens` | no | backend default | 1–2048, Qwen3 sampling option |
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
| `GET /api/ps` | Loaded models, their source repo, and idle duration |
| `DELETE /api/ps/{model_id}` | Unload one loaded model; 404 if unknown/not loaded |
| `POST /unload` | Attempt to unload every loaded model |

These endpoints return HTTP 409 while an active live-ASR stream pins the
target model. A request that needs a different model also returns 409 until the
stream releases its pin.

## MCP

Talkies mounts a stateless streamable-HTTP MCP server at `/v1/mcp`; both the
bare path and trailing-slash form work. It exposes six tools:

- `list_models`: list configured ASR models and whether each is loaded;
- `transcribe`: transcribe an `http(s)` URL or staged `file_path`;
- `list_files`, `put_file`, `get_file`, `delete_file`: manage staged files.

MCP file bytes are base64 in JSON and use the normal upload limit. The MCP
server offers ASR and file tools only; use the HTTP speech endpoint for TTS.

The HTTP routes live in [`src/talkies/server.py`](../src/talkies/server.py);
the MCP tools live in [`src/talkies/mcp_server.py`](../src/talkies/mcp_server.py).

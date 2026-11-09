# API design rules

Prefix: API.

The surface: `GET /healthz`, `GET /v1/models`, `GET /v1/audio/voices`, `POST /v1/audio/transcriptions`, `WS /v1/audio/transcriptions/stream`, `POST /v1/audio/speech`, `GET /v1/files`, `PUT|GET|DELETE /v1/files/{path}`, `GET /api/ps`, `DELETE /api/ps/{model_id}`, `POST /unload`, and the MCP mount at `/v1/mcp`. All are in `src/talkies/server.py`.

## OpenAI compatibility

- **API1:** `POST /v1/audio/transcriptions` and `POST /v1/audio/speech` keep the OpenAI wire shapes where the contracts overlap: the same field names, `response_format` values and response bodies. Accept OpenAI fields Talkies does not use instead of rejecting them (`prompt` and `temperature` are accepted and discarded in `transcribe`).
- **API2:** Talkies extensions are optional additions that never change an OpenAI field's meaning: `diarization` on transcription, `language` and the Qwen3 sampling fields on `SpeechRequest` (ignored by backends that do not use them, with pydantic bounds on each), `modality` and `max_concurrency` on `/v1/models` entries, `origin` on voices. `verbose_json` fills the Whisper-only fields with null or empty values so OpenAI clients do not break (`TranscribeResult` docstring in `src/talkies/models/base.py`).
- **API3:** Model ids in path parameters use `{model_id:path}` and are passed through `unquote`, so URL-encoded slugs work (`unload_one`, and the LiteLLM note in the `server.py` module docstring).

## Errors

- **API4:** HTTP errors are `HTTPException` with a string `detail`, which FastAPI renders as `{"detail": "..."}`; the auth middleware answers 401 in the same shape. Keep the status meanings: 404 unknown model or missing file, 400 invalid input or the wrong modality (the detail names the other endpoint, as `speech` and `transcribe` do), 413 over the upload cap, 409 another model has active work or an unload conflicts with active work, 429 model capacity, 503 a backend that cannot serve yet (missing files, no default voice).
- **API5:** `ModelAdmissionError` carries `code`, `detail` and `status_code`. HTTP routes convert it to `HTTPException(status_code=exc.status_code, detail=exc.detail)`; the WebSocket route sends its `code` (API8).

## Admission and model lifecycle

- **API6:** Every inference path calls `_reserve_model(model)` before backend work and `_release_model(model)` on every exit: in a `finally` for buffered work (`run_transcription_pipeline`, `speech`), inside the generator's `finally` for a streaming response (`_pcm_stream`), and in the handler's `finally` for WebSocket sessions. Covered by `test_pipeline_failure_releases_model_capacity`, `test_buffered_tts_releases_model_capacity` and `test_streaming_tts_holds_capacity_until_iterator_finishes` in `tests/test_server_streaming.py`.
- **API7:** Load and unload decisions happen under `_model_lock`. Every unload (sibling eviction, `DELETE /api/ps/{model_id}`, `POST /unload`, the idle sweeper) is followed by `_wait_for_gpu_drain()`, which must not initialize a Torch CUDA context that is not already initialized (`test_gpu_drain_does_not_initialize_an_unused_torch_context`).

## Live ASR WebSocket

- **API8:** Report a stream failure with `_send_stream_error`: one JSON event `{"type": "error", "code": "...", "detail": "..."}`, then close with the matching `_STREAM_CLOSE_*` code (4400 protocol, 4404 unknown model, 4408 idle, 4409 conflict, 4429 limit, 4500 internal; 4401 comes from the auth middleware). The `detail` is a fixed sentence: never an exception message or the list of configured models (`test_stream_rejects_unknown_model_without_leaking_registry`). Unexpected exceptions are logged and answered with `server_error`.
- **API9:** Start and control messages are strict: unknown or duplicate fields, a missing required field or a message over `MAX_CONTROL_MESSAGE_BYTES` is a protocol error (`parse_start_message`, `parse_control_message` in `src/talkies/asr_streaming.py`). Transcript events go out as `TranscriptEvent.to_dict()`; other server events go through `pack_event`. A new event type, error code or close code is added to the tables in `docs/streaming.md` in the same change.

## Speech output

- **API10:** TTS backends return mono int16 PCM and `src/talkies/tts.py` does all encoding; add a format in `_FORMATS` there, not in a backend. Stream incrementally only when `response_format` is `pcm` and the backend has `synthesize_stream`; send the rate in the `X-Sample-Rate` header. Once streaming headers are sent, an error is logged and the stream ends, because the status can no longer change.

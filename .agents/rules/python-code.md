# Python code rules

Prefix: PY.

`make lint` (`flake8 src`, `mypy src`) and `make format` (`isort src`, `black src`) run in the dev container. CI runs neither, so run them yourself (TST1).

## Style and typing

- **PY1:** Start every module in `src/talkies/` with `from __future__ import annotations` (only `__init__.py` and `__main__.py` lack it), and annotate every function signature, using `X | None` rather than `Optional[X]`. mypy runs with `ignore_missing_imports = true` (`pyproject.toml`) because the dev image has no runtime or ML packages; it still checks first-party code against itself, so keep first-party annotations accurate.
- **PY2:** Format with black at its default 88 columns and sort imports with isort (`make format`). flake8 is set to the same 88 columns and ignores `E203` and `W503` for black compatibility (`.flake8`). Every line in `src/` currently fits in 88 columns; keep it that way.
- **PY3:** Name fixed values as module constants with a comment saying why the value is what it is, instead of inline literals: `_MAX_REDIRECTS`, `_READ_TIMEOUT`, `_CHUNK_BYTES` in `src/talkies/downloads.py`; the `_STREAM_CLOSE_*` codes and `_STREAM_MAX_BUFFER_FRAMES` in `src/talkies/server.py`; `MODEL_CONCURRENCY_MIN` and `MODEL_CONCURRENCY_MAX` in `src/talkies/config.py`.
- **PY4:** Docstrings and comments explain why, including the incident or constraint behind a choice: the IPython stub in `src/talkies/__init__.py`, the empty-value handling of `TALKIES_CHATTERBOX_WATERMARK` in `src/talkies/config.py`, the build-time watermarker check in `Dockerfile.cuda`. Do not write comments that restate the code.

## Configuration

- **PY5:** Read environment variables only in `src/talkies/config.py`, once, at import, into typed module constants named after the setting (`TALKIES_MODEL_TTL` becomes `MODEL_IDLE_TIMEOUT_SECONDS`). Every other module imports `config` and reads the constant. The only existing exceptions are `src/talkies/logging.py` (`TALKIES_LOG_LEVEL`, fallback `LOG_LEVEL`) and `TALKIES_PARAKEET_CPP_LIB` in `src/talkies/models/parakeet_cpp.py`, which the Dockerfiles set.
- **PY6:** New settings use the `TALKIES_` prefix and the existing parsers: `_int_env`, `_float_env`, `_bounded_int_env`, `_bounded_float_env`, `_duration_env` (seconds or Go-style `3h30m5s`), `_bounded_duration_env` and `_list_env` (comma-separated, trimmed, empties dropped).
- **PY7:** An invalid value stops startup with a `ValueError` that names the variable and the accepted values or range. Never fall back to the default on a bad value. Boolean settings accept exactly `true`, `false`, `1`, `0`, `yes`, `no` (case-insensitive) or empty, reject anything else, and treat empty as the documented default (`TALKIES_BLOCK_PRIVATE_DOWNLOADS`, `TALKIES_CHATTERBOX_WATERMARK`).

## Async and blocking work

- **PY8:** Never block the event loop. File reads and writes, ffmpeg conversion, model loading and synchronous inference run through `asyncio.to_thread` (`load_audio_from_path` and `files_put` in `src/talkies/server.py`, `get_model` in `src/talkies/models/whisper.py`), or through `asyncio.create_subprocess_exec` (`encode_audio` in `src/talkies/tts.py`).

## Errors

- **PY9:** Raise a typed exception for each failure a caller must tell apart, and map it to a status at the route: `FilePathError` (`files.py`), `DownloadError` (`downloads.py`), `AudioConversionError` and `NotStereoError` (`audio.py`), `TTSEncodingError` (`tts.py`), `StreamingProtocolError` with a `code` (`asr_streaming.py`), `ModelAdmissionError` with `code` and `status_code` (`errors.py`).
- **PY10:** Re-raise with `from exc` so the cause survives, as every `HTTPException` in `src/talkies/server.py` does.
- **PY11:** Catch `Exception` broadly only to clean up and re-raise (`write_atomic` in `src/talkies/files.py`, `_stream_to_disk` in `src/talkies/downloads.py`, the temp-file handling in `src/talkies/audio.py`), or where an optional step must not break the main one; then log with `log.exception` and mark the line `# noqa: BLE001` (`_idle_sweeper`, preload in `_lifespan`, `voice_origins()` in `list_voices`, all in `src/talkies/server.py`). Never swallow an exception silently.

## Logging

- **PY12:** `src/talkies/logging.py` owns the root handler: one JSON formatter on stdout, configured by `configure()` from `main()`. Do not add handlers or call `logging.basicConfig` anywhere else, and do not `print()` in `src/`.
- **PY13:** Get loggers by explicit name under `talkies.`: module-level `logging.getLogger("talkies.<module>")` (`talkies.server`, `talkies.downloads`, `talkies.mcp`, `talkies.vad`), and per backend instance `logging.getLogger(f"talkies.<executor>.<model_id>")` stored as `self._log` (every backend in `src/talkies/models/`). `src/talkies/models/sherpa.py` is the one module that uses `logging.getLogger(__name__)`, which still lands under `talkies.`.
- **PY14:** Use %-style arguments in log calls (`log.info("idle sweeper: unloading %s ...", model_id, ...)`), and put structured fields in `extra={...}`; the formatter emits every JSON-serializable `extra` key as a top-level field. What may and may not be logged is SEC9.

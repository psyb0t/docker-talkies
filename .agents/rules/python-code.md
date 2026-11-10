# Python code rules

Prefix: PY.

`make lint` (`flake8 src`, `mypy src`) and `make format` (`isort src`, `black src`) run in the dev container. CI runs neither, so run them yourself (TST1).

## Style and typing

- **PY1:** Every module in `src/talkies/` starts with `from __future__ import annotations` (only `__init__.py` and `__main__.py` lack it). mypy runs with `ignore_missing_imports = true` (`pyproject.toml`) because the dev image has no runtime or ML packages, so it checks first-party code only against itself.
- **PY2:** black runs at its default 88 columns; `.flake8` uses the same 88 columns and ignores `E203` and `W503` for black compatibility.

## Configuration

- **PY5:** Read environment variables only in `src/talkies/config.py`, once, at import, into typed module constants named after the setting (`TALKIES_MODEL_TTL` becomes `MODEL_IDLE_TIMEOUT_SECONDS`). Every other module imports `config` and reads the constant. The only existing exceptions are `src/talkies/logging.py` (`TALKIES_LOG_LEVEL`, fallback `LOG_LEVEL`) and `TALKIES_PARAKEET_CPP_LIB` in `src/talkies/models/parakeet_cpp.py`, which the Dockerfiles set.
- **PY6:** New settings use the `TALKIES_` prefix and the existing parsers: `_int_env`, `_float_env`, `_bounded_int_env`, `_bounded_float_env`, `_duration_env` (seconds or Go-style `3h30m5s`), `_bounded_duration_env` and `_list_env` (comma-separated, trimmed, empties dropped).
- **PY7:** An invalid value stops startup with a `ValueError` that names the variable and the accepted values or range. Never fall back to the default on a bad value. Boolean settings accept exactly `true`, `false`, `1`, `0`, `yes`, `no` (case-insensitive) or empty, reject anything else, and treat empty as the documented default (`TALKIES_BLOCK_PRIVATE_DOWNLOADS`, `TALKIES_CHATTERBOX_WATERMARK`).

## Errors

- **PY9:** Raise a typed exception for each failure a caller must tell apart, and map it to a status at the route: `FilePathError` (`files.py`), `DownloadError` (`downloads.py`), `AudioConversionError` and `NotStereoError` (`audio.py`), `TTSEncodingError` (`tts.py`), `StreamingProtocolError` with a `code` (`asr_streaming.py`), `ModelAdmissionError` with `code` and `status_code` (`errors.py`).
- **PY11:** A broad `except Exception` around an optional step that must not break the main one logs with `log.exception` and carries `# noqa: BLE001` (`_idle_sweeper`, preload in `_lifespan`, `voice_origins()` in `list_voices`, all in `src/talkies/server.py`).

## Logging

- **PY12:** `src/talkies/logging.py` owns the root handler: one JSON formatter on stdout, configured by `configure()` from `main()`. Do not add handlers or call `logging.basicConfig` anywhere else, and do not `print()` in `src/`.
- **PY13:** Get loggers by explicit name under `talkies.`: module-level `logging.getLogger("talkies.<module>")` (`talkies.server`, `talkies.downloads`, `talkies.mcp`, `talkies.vad`), and per backend instance `logging.getLogger(f"talkies.<executor>.<model_id>")` stored as `self._log` (every backend in `src/talkies/models/`). `src/talkies/models/sherpa.py` is the one module that uses `logging.getLogger(__name__)`, which still lands under `talkies.`.
- **PY14:** Put structured fields in `extra={...}`; the formatter emits every JSON-serializable `extra` key as a top-level field. What may and may not be logged is SEC9.

"""Env-driven config — parsed at import time, fail-fast on bad input."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not an integer") from exc


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not a number") from exc


def _bounded_int_env(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = _int_env(name, default)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name}={value} must be between {minimum} and {maximum}")
    return value


def _bounded_float_env(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = _float_env(name, default)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name}={value} must be between {minimum} and {maximum}")
    return value


def _list_env(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [s.strip() for s in raw.split(",") if s.strip()]


_DURATION_RE = re.compile(
    r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+(?:\.\d+)?)\s*s)?\s*$",
    re.IGNORECASE,
)


def _duration_env(name: str, default: float) -> float:
    """Parse a duration env var.

    Accepts a bare number (seconds) or Go-style strings like "3h30m5s",
    "45m", "10s", "1h30m". Returns total seconds.
    """
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        pass
    match = _DURATION_RE.match(raw)
    if not match or not any(match.groups()):
        raise ValueError(
            f"{name}={raw!r} must be seconds (e.g. 600) or Go-style "
            "duration like '3h30m5s', '45m', '90s'"
        )
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = float(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def _bounded_duration_env(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = _duration_env(name, default)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name}={value} must be between {minimum}s and {maximum}s")
    return value


# Optional bearer token gating every HTTP route (including /v1/mcp).
# Empty/unset = wide open (current default). When set, every request must
# carry `Authorization: Bearer <token>` or it gets 401. /healthz stays
# unauthenticated so k8s / docker probes keep working.
AUTH_TOKEN: str = os.environ.get("TALKIES_AUTH_TOKEN", "").strip()

DEVICE: str = os.environ.get("TALKIES_DEVICE", "auto").strip() or "auto"
if DEVICE not in ("auto", "cpu", "cuda") and not DEVICE.startswith("cuda:"):
    raise ValueError(
        f"TALKIES_DEVICE={DEVICE!r} must be 'auto', 'cpu', 'cuda', or 'cuda:N'"
    )

MODELS_FILE: Path = Path(
    os.environ.get("TALKIES_MODELS_FILE", "/app/models.json")
).resolve()

DATA_DIR: Path = Path(os.environ.get("TALKIES_DATA_DIR", "/data")).resolve()

# Flat per-model snapshot directory: each enabled model gets
# DATA_DIR / models / <slug> / ... — populated by entrypoint.sh via
# snapshot_download(local_dir=...). Backends load directly from here;
# no HF cache, no models--org--repo/snapshots/<hash> indirection.
MODELS_DIR: Path = DATA_DIR / "models"

# Server-side file staging area for the /v1/files API. Clients PUT files
# here under user-supplied relative paths, then either GET them back or
# reference them by path in /v1/audio/transcriptions (`file_path` field)
# instead of re-uploading on every call.
FILES_DIR: Path = DATA_DIR / "files"

# User-supplied voice samples for one-shot voice-cloning TTS backends
# (currently qwen3_tts). Each .wav file becomes a voice whose name is the
# file's path relative to this dir, with the `.wav` stripped — so
# `/data/custom-voices/foo/bar/me.wav` shows up as voice `foo/bar/me`.
# Optional sibling `<name>.txt` provides the reference transcript and
# `<name>.lang` the language label.
CUSTOM_VOICES_DIR: Path = DATA_DIR / "custom-voices"

# Built-in voice samples baked into the image at build time. Same naming
# convention as CUSTOM_VOICES_DIR. Custom voices with the same name win.
BUILTIN_VOICES_DIR: Path = Path("/opt/talkies/qwen3-voices")

MODEL_IDLE_TIMEOUT_SECONDS: float = _duration_env("TALKIES_MODEL_TTL", 600.0)
SWEEPER_INTERVAL_SECONDS: float = _duration_env("TALKIES_SWEEPER_INTERVAL", 60.0)
LOAD_TIMEOUT_SECONDS: float = _duration_env("TALKIES_LOAD_TIMEOUT", 300.0)

MAX_UPLOAD_BYTES: int = _int_env("TALKIES_MAX_UPLOAD_BYTES", 100 * 1024 * 1024)

# URL downloads (when file_path is an http(s) URL). Bigger default than
# the upload cap — downloads stream to disk, no in-memory buffering.
MAX_DOWNLOAD_BYTES: int = _int_env("TALKIES_MAX_DOWNLOAD_BYTES", 1024 * 1024 * 1024)

STREAM_MAX_CONNECTIONS: int = _bounded_int_env(
    "TALKIES_STREAM_MAX_CONNECTIONS", 4, 1, 1024
)
MODEL_CONCURRENCY_MIN = 1
MODEL_CONCURRENCY_MAX = 1024
MODEL_CONCURRENCY_OVERRIDE_MAX_BYTES = 65536
MODEL_MAX_CONCURRENCY: int = _bounded_int_env(
    "TALKIES_MODEL_MAX_CONCURRENCY",
    1,
    MODEL_CONCURRENCY_MIN,
    MODEL_CONCURRENCY_MAX,
)
MODEL_CONCURRENCY_OVERRIDES_RAW: str = os.environ.get(
    "TALKIES_MODEL_CONCURRENCY", ""
).strip()
if len(MODEL_CONCURRENCY_OVERRIDES_RAW.encode("utf-8")) > (
    MODEL_CONCURRENCY_OVERRIDE_MAX_BYTES
):
    raise ValueError(
        "TALKIES_MODEL_CONCURRENCY exceeds the 65536-byte configuration limit"
    )
STREAM_MAX_FRAME_BYTES: int = _bounded_int_env(
    "TALKIES_STREAM_MAX_FRAME_BYTES", 65536, 2, 16 * 1024 * 1024
)
STREAM_MAX_BUFFER_SECONDS: float = _bounded_float_env(
    "TALKIES_STREAM_MAX_BUFFER_SECONDS", 5.0, 0.1, 300.0
)
STREAM_MAX_BUFFER_BYTES: int = int(STREAM_MAX_BUFFER_SECONDS * 16000 * 2)
if STREAM_MAX_BUFFER_BYTES < STREAM_MAX_FRAME_BYTES:
    raise ValueError(
        "TALKIES_STREAM_MAX_BUFFER_SECONDS must hold at least one "
        "TALKIES_STREAM_MAX_FRAME_BYTES frame"
    )
STREAM_IDLE_TIMEOUT_SECONDS: float = _bounded_duration_env(
    "TALKIES_STREAM_IDLE_TIMEOUT", 30.0, 1.0, 3600.0
)
STREAM_MAX_DURATION_SECONDS: float = _bounded_duration_env(
    "TALKIES_STREAM_MAX_DURATION", 4 * 3600.0, 1.0, 24 * 3600.0
)

# SSRF guard for URL downloads. Default off (LAN-fetch use cases dominate
# in self-hosted deployments). Set to true to refuse URLs whose hostname
# resolves to private / loopback / link-local / multicast / metadata IPs.
_BLOCK_PRIVATE_RAW: str = (
    os.environ.get("TALKIES_BLOCK_PRIVATE_DOWNLOADS", "false").strip().lower()
)
if _BLOCK_PRIVATE_RAW not in ("", "true", "false", "1", "0", "yes", "no"):
    raise ValueError(
        f"TALKIES_BLOCK_PRIVATE_DOWNLOADS={_BLOCK_PRIVATE_RAW!r} must be "
        "true/false/1/0/yes/no"
    )
BLOCK_PRIVATE_DOWNLOADS: bool = _BLOCK_PRIVATE_RAW in ("true", "1", "yes")

# Chatterbox embeds Resemble's PerTh neural watermark in every waveform it
# generates. Upstream applies it unconditionally with no option to disable, so
# the backend substitutes a passthrough when this is false. Defaults on to match
# upstream behaviour.
_CHATTERBOX_WATERMARK_RAW: str = (
    os.environ.get("TALKIES_CHATTERBOX_WATERMARK", "true").strip().lower()
)
if _CHATTERBOX_WATERMARK_RAW not in ("", "true", "false", "1", "0", "yes", "no"):
    raise ValueError(
        f"TALKIES_CHATTERBOX_WATERMARK={_CHATTERBOX_WATERMARK_RAW!r} must be "
        "true/false/1/0/yes/no"
    )
# Empty means unset, which keeps the ON default. The flags above default off, so
# empty collapsing to false matches their default; here it would strip the
# watermark on a blank value.
CHATTERBOX_WATERMARK: bool = _CHATTERBOX_WATERMARK_RAW in ("", "true", "1", "yes")

PRELOAD: list[str] = _list_env("TALKIES_PRELOAD")
ENABLED_MODELS: list[str] = _list_env("TALKIES_ENABLED_MODELS")

# VAD chunking — audio longer than this triggers VAD-based segmentation
# regardless of backend. SALM uses the same chunker but, because it has
# no alignment head, concatenates per-chunk text instead of stitching a
# segments timeline.
VAD_CHUNK_THRESHOLD_SECONDS: float = _float_env("TALKIES_VAD_CHUNK_THRESHOLD", 30.0)
VAD_MAX_SPEECH_SECONDS: float = _float_env("TALKIES_VAD_MAX_SPEECH", 28.0)
VAD_MIN_SILENCE_MS: int = _int_env("TALKIES_VAD_MIN_SILENCE_MS", 500)
VAD_SPEECH_PAD_MS: int = _int_env("TALKIES_VAD_SPEECH_PAD_MS", 200)
VAD_THRESHOLD: float = _float_env("TALKIES_VAD_THRESHOLD", 0.5)

# Qwen3-TTS streaming — number of codec steps to decode per yielded chunk.
# 12 steps ≈ 1 s of audio; 8 steps ≈ 667 ms (good balance of TTFA vs overhead).
# Only relevant when response_format="pcm" with a qwen3_tts backend.
QWEN3_STREAM_CHUNK_SIZE: int = _int_env("TALKIES_QWEN3_STREAM_CHUNK_SIZE", 8)


# Executors a models.json entry may declare. Single source of truth for the
# registry validator + the "must be one of" error message.
VALID_EXECUTORS = (
    "whisper",
    "parakeet",
    "parakeet_cpp",
    "canary_multitask",
    "canary_salm",
    "kokoro",
    "kokoro_nvidia",
    "qwen3_tts",
    "sherpa",
    "vosk",
    "chatterbox",
)


def _model_concurrency_overrides(model_ids: set[str]) -> dict[str, int]:
    overrides: dict[str, int] = {}
    if not MODEL_CONCURRENCY_OVERRIDES_RAW:
        return overrides
    for raw_pair in MODEL_CONCURRENCY_OVERRIDES_RAW.split(","):
        pair = raw_pair.strip()
        if not pair or pair.count("=") != 1:
            raise ValueError(
                "TALKIES_MODEL_CONCURRENCY must contain comma-separated "
                "model-slug=limit pairs"
            )
        model_id, raw_limit = (part.strip() for part in pair.split("=", 1))
        if not model_id or not raw_limit:
            raise ValueError(
                "TALKIES_MODEL_CONCURRENCY must contain non-empty "
                "model-slug=limit pairs"
            )
        if model_id in overrides:
            raise ValueError(f"TALKIES_MODEL_CONCURRENCY repeats model {model_id!r}")
        if model_id not in model_ids:
            raise ValueError(
                "TALKIES_MODEL_CONCURRENCY references unknown or disabled "
                f"model {model_id!r}"
            )
        try:
            limit = int(raw_limit)
        except ValueError as exc:
            raise ValueError(
                f"TALKIES_MODEL_CONCURRENCY limit for {model_id!r} "
                f"is not an integer: {raw_limit!r}"
            ) from exc
        if not MODEL_CONCURRENCY_MIN <= limit <= MODEL_CONCURRENCY_MAX:
            raise ValueError(
                f"TALKIES_MODEL_CONCURRENCY limit for {model_id!r} must be "
                f"between {MODEL_CONCURRENCY_MIN} and {MODEL_CONCURRENCY_MAX}"
            )
        overrides[model_id] = limit
    return overrides


def load_registry() -> dict[str, dict]:
    """Read models.json and return {model_id: {repo, executor, language?, ...}}."""
    if not MODELS_FILE.exists():
        raise FileNotFoundError(f"models.json not found at {MODELS_FILE}")
    with MODELS_FILE.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict) or "models" not in raw:
        raise ValueError(f"{MODELS_FILE}: expected top-level object with 'models' key")
    models = raw["models"]
    if not isinstance(models, dict) or not models:
        raise ValueError(f"{MODELS_FILE}: 'models' must be a non-empty object")
    for model_id, entry in models.items():
        if not isinstance(entry, dict):
            raise ValueError(
                f"{MODELS_FILE}: model {model_id!r} entry must be an object"
            )
        if "repo" not in entry:
            raise ValueError(f"{MODELS_FILE}: model {model_id!r} missing 'repo'")
        executor = entry.get("executor", "whisper")
        if executor not in VALID_EXECUTORS:
            raise ValueError(
                f"{MODELS_FILE}: model {model_id!r} executor={executor!r} "
                f"must be one of {VALID_EXECUTORS}"
            )
        configured_limit = entry.get("max_concurrency")
        if configured_limit is not None and (
            isinstance(configured_limit, bool)
            or not isinstance(configured_limit, int)
            or not MODEL_CONCURRENCY_MIN <= configured_limit <= MODEL_CONCURRENCY_MAX
        ):
            raise ValueError(
                f"{MODELS_FILE}: model {model_id!r} max_concurrency must be "
                f"an integer between {MODEL_CONCURRENCY_MIN} and "
                f"{MODEL_CONCURRENCY_MAX}"
            )
    if ENABLED_MODELS:
        missing = [s for s in ENABLED_MODELS if s not in models]
        if missing:
            raise ValueError(
                f"TALKIES_ENABLED_MODELS references unknown slug(s) {missing}; "
                f"available in {MODELS_FILE}: {sorted(models)}"
            )
        models = {s: models[s] for s in ENABLED_MODELS}
    overrides = _model_concurrency_overrides(set(models))
    normalized: dict[str, dict] = {}
    for model_id, entry in models.items():
        normalized_entry = dict(entry)
        configured_limit = entry.get("max_concurrency")
        normalized_entry["max_concurrency"] = overrides.get(
            model_id,
            configured_limit if configured_limit is not None else MODEL_MAX_CONCURRENCY,
        )
        normalized[model_id] = normalized_entry
    return normalized

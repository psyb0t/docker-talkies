"""FastAPI app — OpenAI-compatible speech endpoints + resource-mgmt API.

Endpoints (mirror the speaches surface where possible so the LiteLLM resource
manager can drive both with the same client code):

  GET    /healthz                          unauthenticated liveness
  GET    /v1/models                        list configured model_ids (with modality)
  GET    /v1/audio/voices                  list TTS voices by model
  GET    /api/ps                           list currently loaded model_ids
  DELETE /api/ps/{model_id}                evict one model from VRAM/RAM
  POST   /unload                           evict all loaded models
  POST   /v1/audio/transcriptions          OpenAI-compatible ASR
  POST   /v1/audio/speech                  OpenAI-compatible TTS

The DELETE /api/ps/{model_id} path accepts URL-encoded model_ids so the LiteLLM
resource manager's existing `model_id.replace("/", "%2F")` call works against
both speaches and this service.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import unquote

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from . import config
from . import downloads as downloads_mod
from . import files as files_mod
from . import tts as tts_mod
from .audio import (
    AudioConversionError,
    NotStereoError,
    to_wav_16k_mono,
    to_wav_16k_split_lr,
)
from .auth import BearerAuthMiddleware
from .asr_streaming import (
    EVENT_CANCEL,
    EVENT_END,
    EVENT_ENDPOINT,
    EVENT_FINAL,
    EVENT_PARTIAL,
    MAX_CONTROL_MESSAGE_BYTES,
    StreamConfig,
    StreamSessionState,
    StreamingProtocolError,
    TranscriptEvent,
    decode_json_message,
    pack_event,
    parse_control_message,
    parse_start_message,
)
from .logging import configure as configure_logging
from .mcp_server import build_mcp_server
from .models import (
    build_backends,
    is_asr_backend,
    is_streaming_asr_backend,
    is_tts_backend,
)
from .models.base import TranscribeResult


async def _wait_for_gpu_drain() -> None:
    """Block until in-flight CUDA deallocations have actually completed.

    Each backend's ``unload()`` calls ``torch.cuda.empty_cache()`` and
    returns. That call hands the work to the CUDA driver which finishes
    asynchronously — the Python side gets control back BEFORE the GPU
    blocks are actually returned to the allocator pool. On a memory-tight
    host the next ``backend.get_model()`` then races against the still-
    freeing buffers and the load OOMs (typical failure: ctranslate2 +
    NeMo back-to-back on a single GPU).

    A single ``torch.cuda.synchronize()`` waits for the device to finish
    every queued op, including the dealloc work, before we return. Cheap
    when the device is already idle (microseconds); the right barrier
    otherwise. Importing torch lazily so non-CUDA images stay light.
    """

    def _sync() -> None:
        try:
            import torch
        except ImportError:
            return
        if not torch.cuda.is_available():
            return
        torch.cuda.synchronize()
        torch.cuda.empty_cache()

    await asyncio.to_thread(_sync)


_VERBOSE_FORMATS = {"verbose_json", "srt", "vtt"}


log = logging.getLogger("talkies.server")


def _resolve_device(req: str) -> str:
    if req != "auto":
        return req
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


REGISTRY = config.load_registry()
DEVICE = _resolve_device(config.DEVICE)
BACKENDS = build_backends(REGISTRY, DEVICE)

_STREAM_CLOSE_NORMAL = 1000
_STREAM_CLOSE_PROTOCOL_ERROR = 4400
_STREAM_CLOSE_IDLE_TIMEOUT = 4408
_STREAM_CLOSE_NOT_FOUND = 4404
_STREAM_CLOSE_CONFLICT = 4409
_STREAM_CLOSE_LIMIT = 4429
_STREAM_CLOSE_INTERNAL_ERROR = 4500
_STREAM_MAX_BUFFER_FRAMES = 256
_STREAM_MAX_PENDING_MESSAGES = 1
_stream_lock = asyncio.Lock()
_active_streams: dict[str, int] = {}


def _active_stream_count(model_id: str | None = None) -> int:
    if model_id is None:
        return sum(_active_streams.values())
    return _active_streams.get(model_id, 0)


async def _reserve_stream(model_id: str) -> None:
    async with _stream_lock:
        if _active_stream_count() >= config.STREAM_MAX_CONNECTIONS:
            raise StreamingProtocolError(
                "connection_limit",
                "stream connection limit reached",
            )
        active_siblings = [
            sibling_id
            for sibling_id, count in _active_streams.items()
            if sibling_id != model_id and count > 0
        ]
        if active_siblings:
            raise StreamingProtocolError(
                "model_busy",
                "another model has active streaming sessions",
            )
        siblings = [
            backend
            for sibling_id, backend in BACKENDS.items()
            if sibling_id != model_id and backend.loaded()
        ]
        if siblings:
            await asyncio.gather(
                *(backend.unload() for backend in siblings),
                return_exceptions=False,
            )
            await _wait_for_gpu_drain()
        _active_streams[model_id] = _active_stream_count(model_id) + 1


async def _release_stream(model_id: str) -> None:
    async with _stream_lock:
        remaining = _active_stream_count(model_id) - 1
        if remaining > 0:
            _active_streams[model_id] = remaining
            return
        _active_streams.pop(model_id, None)


async def _evict_siblings(model_id: str) -> None:
    async with _stream_lock:
        active_siblings = [
            sibling_id
            for sibling_id, count in _active_streams.items()
            if sibling_id != model_id and count > 0
        ]
        if active_siblings:
            raise HTTPException(
                status_code=409,
                detail="another model has active streaming sessions",
            )
        siblings = [
            backend
            for sibling_id, backend in BACKENDS.items()
            if sibling_id != model_id and backend.loaded()
        ]
        if not siblings:
            return
        log.info(
            "evicting sibling backends",
            extra={
                "model_id": model_id,
                "siblings": [
                    sibling_id
                    for sibling_id, backend in BACKENDS.items()
                    if sibling_id != model_id and backend.loaded()
                ],
            },
        )
        await asyncio.gather(
            *(backend.unload() for backend in siblings),
            return_exceptions=False,
        )
        await _wait_for_gpu_drain()


async def _idle_sweeper() -> None:
    """Unload backends idle longer than TALKIES_MODEL_TTL."""
    while True:
        try:
            await asyncio.sleep(config.SWEEPER_INTERVAL_SECONDS)
            ttl = config.MODEL_IDLE_TIMEOUT_SECONDS
            if ttl <= 0:
                continue
            for model_id, backend in BACKENDS.items():
                if not backend.loaded():
                    continue
                last = backend.last_used_secs_ago()
                if last is None:
                    continue
                if last < ttl:
                    continue
                log.info(
                    "idle sweeper: unloading %s (idle %.1fs >= %.1fs)",
                    model_id,
                    last,
                    ttl,
                )
                try:
                    async with _stream_lock:
                        if _active_stream_count(model_id):
                            continue
                        await backend.unload()
                except Exception:  # noqa: BLE001
                    log.exception("idle sweeper: unload %s failed", model_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("idle sweeper iteration failed")


_sweeper_task: asyncio.Task[None] | None = None


# Forward-declared so _lifespan can drive its session_manager.run() — the
# actual FastMCP instance is built (and assigned here) further down, after
# `load_audio_from_path` / `run_transcription_pipeline` exist, since both
# are injected as callables into the MCP tools.
MCP_SERVER: Any = None


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    files_mod.ensure_base(config.FILES_DIR)
    log.info(
        "talkies starting: device=%s models=%s ttl=%.0fs files_dir=%s auth=%s",
        DEVICE,
        list(BACKENDS.keys()),
        config.MODEL_IDLE_TIMEOUT_SECONDS,
        config.FILES_DIR,
        "on" if config.AUTH_TOKEN else "off",
    )

    for model_id in config.PRELOAD:
        if model_id not in BACKENDS:
            log.warning("preload: unknown model %s — skipping", model_id)
            continue
        log.info("preload: %s", model_id)
        try:
            await BACKENDS[model_id].get_model()
        except Exception:  # noqa: BLE001
            log.exception("preload %s failed", model_id)

    global _sweeper_task
    _sweeper_task = asyncio.create_task(_idle_sweeper(), name="talkies-sweeper")
    try:
        # MCP's streamable HTTP transport needs its session manager
        # running for the lifetime of the app.
        async with MCP_SERVER.session_manager.run():
            yield
    finally:
        if _sweeper_task is not None:
            _sweeper_task.cancel()
            try:
                await _sweeper_task
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(
    title="talkies",
    description=(
        "OpenAI-compatible speech wrapper — /v1/audio/transcriptions over "
        "Whisper / Parakeet / Canary ASR + /v1/audio/speech over Kokoro TTS. "
        "Lazy-loads models on first request, idle-unloads after TALKIES_MODEL_TTL."
    ),
    lifespan=_lifespan,
)


class _MCPSlashRewriteMiddleware:
    """Rewrite ``/v1/mcp`` to ``/v1/mcp/`` before routing.

    Starlette's ``Mount("/v1/mcp", ...)`` serves requests at ``/v1/mcp/*``
    but emits a 307 redirect for the bare ``/v1/mcp`` form. Compliant
    clients re-POST to the new location, but enough MCP clients / curl
    scripts trip on it that rewriting the path here is cheaper than the
    docs churn of "remember the trailing slash".
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/v1/mcp":
            scope = dict(scope)
            scope["path"] = "/v1/mcp/"
            scope["raw_path"] = b"/v1/mcp/"
        await self.app(scope, receive, send)


# Optional bearer auth covers EVERY route — including the mounted MCP
# sub-app. Pass-through when AUTH_TOKEN is empty (the historical default).
app.add_middleware(BearerAuthMiddleware, token=config.AUTH_TOKEN)
# Outermost: normalise `/v1/mcp` -> `/v1/mcp/` so the Mount redirect
# never fires. Has to wrap the auth middleware so the rewritten path is
# what auth + routing see.
app.add_middleware(_MCPSlashRewriteMiddleware)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "device": DEVICE, "models": list(BACKENDS.keys())}


def _modality_of(model_id: str) -> str:
    backend = BACKENDS[model_id]
    if is_tts_backend(backend):
        return "tts"
    return "asr"


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": mid,
                "object": "model",
                "owned_by": "talkies",
                "modality": _modality_of(mid),
            }
            for mid in BACKENDS.keys()
        ],
    }


@app.get("/v1/audio/voices")
def list_voices() -> dict[str, Any]:
    """List available TTS voices across all loaded TTS models.

    Returned shape: ``{"voices": [{"voice": str, "model": str,
    "default": bool, "origin": str?}]}``. Pass any ``voice`` value to
    ``POST /v1/audio/speech`` together with its ``model`` slug — voices
    are not interchangeable across models (each TTS engine owns its own
    catalog).

    ``origin`` is set to ``"builtin"`` (shipped in the image) or
    ``"custom"`` (mounted via ``/data/custom-voices/``) for backends that
    expose a ``voice_origins()`` method; omitted otherwise.
    """
    out: list[dict[str, Any]] = []
    for mid, backend in BACKENDS.items():
        if not is_tts_backend(backend):
            continue
        try:
            default = backend.default_voice()
            catalog = backend.voices()
        except RuntimeError as exc:
            log.warning("voice listing failed for %s: %s", mid, exc)
            continue
        origins: dict[str, str] = {}
        if hasattr(backend, "voice_origins"):
            try:
                origins = backend.voice_origins()
            except Exception:  # noqa: BLE001
                log.exception("voice_origins() failed for %s", mid)
        for v in catalog:
            entry: dict[str, Any] = {"voice": v, "model": mid, "default": v == default}
            if v in origins:
                entry["origin"] = origins[v]
            out.append(entry)
    return {"voices": out}


@app.get("/api/ps")
def list_loaded() -> dict[str, Any]:
    return {
        "models": [
            {
                "id": mid,
                "repo": BACKENDS[mid].repo,
                "loaded": BACKENDS[mid].loaded(),
                "idle_seconds": BACKENDS[mid].last_used_secs_ago(),
            }
            for mid in BACKENDS.keys()
            if BACKENDS[mid].loaded()
        ]
    }


async def _send_stream_error(
    websocket: WebSocket,
    code: str,
    detail: str,
    close_code: int,
) -> None:
    await websocket.send_json(pack_event("error", code=code, detail=detail))
    await websocket.close(code=close_code, reason=detail)


async def _receive_stream_json(
    websocket: WebSocket,
    timeout_seconds: float,
) -> object:
    message = await asyncio.wait_for(
        websocket.receive(),
        timeout=timeout_seconds,
    )
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", _STREAM_CLOSE_NORMAL))
    text = message.get("text")
    if text is None:
        raise StreamingProtocolError(
            "invalid_message",
            "expected a JSON text message",
        )
    return decode_json_message(text)


async def _start_streaming_backend(
    backend: Any,
    stream_config: StreamConfig,
) -> Any:
    executor = REGISTRY[stream_config.model].get("executor", "whisper")
    if executor == "parakeet_cpp":
        return await backend.start_stream(
            language=stream_config.language,
            max_frame_bytes=config.STREAM_MAX_FRAME_BYTES,
        )
    return await backend.start_stream(stream_config)


async def _send_backend_events(
    websocket: WebSocket,
    output: object,
    state: StreamSessionState,
    stream_config: StreamConfig,
    force_final: bool = False,
) -> None:
    outputs = output if isinstance(output, list) else [output]
    for item in outputs:
        if isinstance(item, TranscriptEvent):
            if item.event_type == EVENT_PARTIAL and not stream_config.interim_results:
                continue
            await websocket.send_json(item.to_dict())
            continue
        if not isinstance(item, dict):
            raise RuntimeError("streaming backend returned an invalid event")
        event_type = EVENT_FINAL if force_final else EVENT_PARTIAL
        if not force_final and item.get("eou") is True:
            event_type = EVENT_ENDPOINT
        if event_type == EVENT_PARTIAL and not stream_config.interim_results:
            continue
        words = item.get("words") if stream_config.word_timestamps else []
        if not isinstance(words, list):
            words = []
        text = str(item.get("text", "")).strip()
        if event_type == EVENT_PARTIAL and not text and not words:
            continue
        event = state.transcript_event(
            event_type,
            text,
            words=[word for word in words if isinstance(word, dict)],
        )
        await websocket.send_json(event.to_dict())


@app.websocket("/v1/audio/transcriptions/stream")
async def transcribe_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    session: Any | None = None
    stream_config: StreamConfig | None = None
    reserved = False
    state = StreamSessionState(
        max_buffer_bytes=config.STREAM_MAX_BUFFER_BYTES,
        max_buffer_frames=_STREAM_MAX_BUFFER_FRAMES,
    )
    frame_count = 0
    try:
        start_message = await _receive_stream_json(
            websocket,
            config.STREAM_IDLE_TIMEOUT_SECONDS,
        )
        stream_config = parse_start_message(start_message)
        if stream_config.model not in BACKENDS:
            await _send_stream_error(
                websocket,
                "unknown_model",
                "requested model is not configured",
                _STREAM_CLOSE_NOT_FOUND,
            )
            return
        backend = BACKENDS[stream_config.model]
        if not is_streaming_asr_backend(backend):
            await _send_stream_error(
                websocket,
                "streaming_not_supported",
                "requested model does not support streaming",
                _STREAM_CLOSE_CONFLICT,
            )
            return

        await _reserve_stream(stream_config.model)
        reserved = True
        session = await _start_streaming_backend(backend, stream_config)
        await websocket.send_json(
            pack_event(
                "ready",
                model=stream_config.model,
                encoding=stream_config.encoding,
                sample_rate=stream_config.sample_rate,
                channels=stream_config.channels,
            )
        )

        while True:
            message = await asyncio.wait_for(
                websocket.receive(),
                timeout=config.STREAM_IDLE_TIMEOUT_SECONDS,
            )
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", _STREAM_CLOSE_NORMAL))
            frame = message.get("bytes")
            if frame is not None:
                state.reserve_frame(
                    frame,
                    max_frame_bytes=config.STREAM_MAX_FRAME_BYTES,
                )
                if state.audio_seconds > config.STREAM_MAX_DURATION_SECONDS:
                    state.release_frame(len(frame))
                    raise StreamingProtocolError(
                        "duration_limit",
                        "stream duration limit reached",
                    )
                frame_count += 1
                try:
                    output = await session.feed(frame)
                finally:
                    state.release_frame(len(frame))
                await _send_backend_events(
                    websocket,
                    output,
                    state,
                    stream_config,
                )
                continue

            text = message.get("text")
            if text is None:
                raise StreamingProtocolError(
                    "invalid_message",
                    "message must be binary PCM or JSON control",
                )
            control_message = decode_json_message(text)
            control = parse_control_message(control_message)
            if control == EVENT_CANCEL:
                await session.cancel()
                await websocket.send_json(
                    pack_event(
                        "stats",
                        audio_seconds=state.audio_seconds,
                        frames=frame_count,
                        canceled=True,
                    )
                )
                await websocket.close(code=_STREAM_CLOSE_NORMAL)
                return
            if control == EVENT_END:
                final_event = await session.finalize()
                await _send_backend_events(
                    websocket,
                    final_event,
                    state,
                    stream_config,
                    force_final=True,
                )
                await websocket.send_json(
                    pack_event(
                        "stats",
                        audio_seconds=state.audio_seconds,
                        frames=frame_count,
                        canceled=False,
                    )
                )
                await websocket.close(code=_STREAM_CLOSE_NORMAL)
                return
    except asyncio.TimeoutError:
        if session is not None:
            await session.cancel()
        await _send_stream_error(
            websocket,
            "idle_timeout",
            "stream timed out waiting for client data",
            _STREAM_CLOSE_IDLE_TIMEOUT,
        )
    except StreamingProtocolError as exc:
        if session is not None:
            await session.cancel()
        close_code = _STREAM_CLOSE_PROTOCOL_ERROR
        if exc.code == "connection_limit":
            close_code = _STREAM_CLOSE_LIMIT
        if exc.code == "model_busy":
            close_code = _STREAM_CLOSE_CONFLICT
        await _send_stream_error(
            websocket,
            exc.code,
            exc.detail,
            close_code,
        )
    except WebSocketDisconnect:
        if session is not None:
            await session.cancel()
    except Exception:  # noqa: BLE001
        log.exception("streaming transcription failed")
        if session is not None:
            await session.cancel()
        await _send_stream_error(
            websocket,
            "server_error",
            "streaming transcription failed",
            _STREAM_CLOSE_INTERNAL_ERROR,
        )
    finally:
        if session is not None:
            await session.close()
        if reserved and stream_config is not None:
            await _release_stream(stream_config.model)


@app.delete("/api/ps/{model_id:path}")
async def unload_one(model_id: str) -> JSONResponse:
    decoded = unquote(model_id)
    backend = BACKENDS.get(decoded)
    if backend is None:
        return JSONResponse({"detail": f"unknown model {decoded!r}"}, status_code=404)
    if not backend.loaded():
        return JSONResponse({"detail": "not loaded"}, status_code=404)
    async with _stream_lock:
        if _active_stream_count(decoded):
            return JSONResponse(
                {"detail": "model has active streaming sessions"},
                status_code=409,
            )
        await backend.unload()
    return JSONResponse({"unloaded": decoded}, status_code=200)


@app.post("/unload")
async def unload_all() -> dict[str, Any]:
    async with _stream_lock:
        if _active_stream_count():
            raise HTTPException(
                status_code=409,
                detail="models have active streaming sessions",
            )
        unloaded = []
        for model_id, backend in BACKENDS.items():
            if not backend.loaded():
                continue
            try:
                await backend.unload()
                unloaded.append(model_id)
            except Exception:  # noqa: BLE001
                log.exception("unload %s failed", model_id)
    return {"unloaded": unloaded}


_DIARIZATION_TRUE = {"true", "1", "yes", "on"}


def _parse_diarization(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in _DIARIZATION_TRUE


async def load_audio_from_path(file_path: str) -> tuple[bytes, str]:
    """Return ``(raw_bytes, original_name)`` for a URL or staged-file path.

    Raises ``downloads_mod.DownloadError`` / ``files_mod.FilePathError`` for
    bad input, ``FileNotFoundError`` if a staged path is missing. The
    ``TALKIES_MAX_UPLOAD_BYTES`` cap intentionally does NOT apply here —
    the file already lives on disk (or got fetched into the cache under
    the URL-download cap), and this function just hands the bytes back.
    """
    if downloads_mod.is_url(file_path):
        src_path = await downloads_mod.ensure_downloaded(file_path)
    else:
        rel = files_mod.sanitize_path(file_path)
        src_path = files_mod.resolve_under(config.FILES_DIR, rel)
        if not src_path.is_file():
            raise FileNotFoundError(f"file_path not found: {file_path}")
    raw = await asyncio.to_thread(src_path.read_bytes)
    return raw, src_path.name


class SpeechRequest(BaseModel):
    """OpenAI-compatible POST body for ``/v1/audio/speech``.

    ``instructions`` controls speech style for backends that support it
    (Qwen3-TTS: passed as the ``instruct`` parameter to ``generate_voice_clone``;
    works best in ICL mode i.e. when a ``.txt`` transcript exists for the voice).
    Ignored by Kokoro, which has no instruction-prompt input.
    ``speed`` clamped to 0.25-4.0 (matches OpenAI's documented range).
    """

    model: str
    input: str
    voice: str | None = None
    response_format: str | None = None
    speed: float | None = Field(default=None, ge=0.25, le=4.0)
    instructions: str | None = None
    # Non-OpenAI extra params — OpenAI SDKs send via extra_body={...}; cURL just
    # adds JSON keys. All are silently ignored by backends that don't use them
    # (Kokoro). Qwen3-TTS reads them across all three modes.
    #
    # `language`           — spoken language (Qwen3 custom_voice / voice_design;
    #                        base mode reads voice's sibling .lang file first).
    # `temperature`        — sampler temperature (Qwen3 only; default 0.9).
    # `top_k`              — top-k truncation (Qwen3 only; default 50).
    # `top_p`              — nucleus sampling (Qwen3 only; default 1.0).
    # `repetition_penalty` — penalize codec-token repeats (Qwen3 only; default 1.05).
    # `max_new_tokens`     — codec-step cap (Qwen3 only; default 2048 = model max).
    # `do_sample`          — false = greedy decode (Qwen3 only; default true).
    language: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_k: int | None = Field(default=None, ge=1, le=1000)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    repetition_penalty: float | None = Field(default=None, ge=0.5, le=2.0)
    max_new_tokens: int | None = Field(default=None, ge=1, le=2048)
    do_sample: bool | None = None


@app.post("/v1/audio/speech")
async def speech(body: SpeechRequest) -> Response:
    model = body.model
    if log.isEnabledFor(logging.DEBUG):
        # Full request body — DEBUG only, PII-bearing (input text +
        # instructions). Logged before validation so rejected requests are
        # captured too. `input_len` stays visible at higher levels' mental
        # model; the raw text is DEBUG-gated.
        log.debug(
            "tts request",
            extra={
                "endpoint": "/v1/audio/speech",
                "req_model": model,
                "req_voice": body.voice,
                "req_response_format": body.response_format,
                "req_speed": body.speed,
                "req_language": body.language,
                "req_instructions": body.instructions,
                "req_sampling": {
                    "temperature": body.temperature,
                    "top_k": body.top_k,
                    "top_p": body.top_p,
                    "repetition_penalty": body.repetition_penalty,
                    "max_new_tokens": body.max_new_tokens,
                    "do_sample": body.do_sample,
                },
                "input_len": len(body.input),
                "input_text": body.input,
            },
        )
    if model not in BACKENDS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown model {model!r}; configured: {list(BACKENDS.keys())}",
        )
    backend = BACKENDS[model]
    if not is_tts_backend(backend):
        raise HTTPException(
            status_code=400,
            detail=f"model {model!r} is not a TTS model — try /v1/audio/transcriptions",
        )

    try:
        default_voice = backend.default_voice()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    voice = body.voice or default_voice
    catalog = backend.voices()
    if voice not in catalog:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown voice {voice!r} for model {model!r}; "
                f"{len(catalog)} voice(s) available — call GET /v1/audio/voices "
                "to list them"
            ),
        )

    fmt = (body.response_format or "mp3").lower()
    if fmt not in tts_mod.SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unsupported response_format {fmt!r}; supported: "
                f"{list(tts_mod.SUPPORTED_FORMATS)}"
            ),
        )

    speed = body.speed if body.speed is not None else 1.0

    if not body.input.strip():
        raise HTTPException(status_code=400, detail="input text is empty")

    await _evict_siblings(model)

    # PCM streaming path — only when the backend natively supports it
    # (currently Qwen3-TTS only). Yields int16 LE PCM chunks as they are
    # decoded; no container header. The X-Sample-Rate header signals the
    # waveform parameters to the client.
    # Non-OpenAI sampling knobs (Qwen3-TTS only — Kokoro silently drops).
    # Pack into a single dict so the backend signature stays clean as we
    # add or remove knobs over time.
    sampling: dict[str, Any] = {
        k: v
        for k, v in {
            "temperature": body.temperature,
            "top_k": body.top_k,
            "top_p": body.top_p,
            "repetition_penalty": body.repetition_penalty,
            "max_new_tokens": body.max_new_tokens,
            "do_sample": body.do_sample,
        }.items()
        if v is not None
    }

    if fmt == "pcm" and hasattr(backend, "synthesize_stream"):
        chunk_size = config.QWEN3_STREAM_CHUNK_SIZE
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "tts stream start",
                extra={
                    "resp_model": model,
                    "resp_voice": voice,
                    "resp_format": fmt,
                    "resp_sample_rate": backend.sample_rate,  # type: ignore[union-attr]
                    "chunk_size": chunk_size,
                },
            )

        async def _pcm_stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in backend.synthesize_stream(  # type: ignore
                    body.input,
                    voice=voice,
                    speed=speed,
                    instructions=body.instructions,
                    language=body.language,
                    sampling=sampling,
                    chunk_size=chunk_size,
                ):
                    yield chunk
            except (ValueError, FileNotFoundError, RuntimeError) as exc:
                # Headers are already committed; log and stop the stream.
                log.error("qwen3_tts streaming error (stream aborted): %s", exc)

        return StreamingResponse(
            _pcm_stream(),
            media_type="application/octet-stream",
            headers={"X-Sample-Rate": str(backend.sample_rate)},  # type: ignore
        )

    try:
        synth = await backend.synthesize(
            body.input,
            voice=voice,
            speed=speed,
            instructions=body.instructions,
            language=body.language,
            sampling=sampling,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        audio_bytes, content_type = await tts_mod.encode_audio(
            synth.pcm_int16, synth.sample_rate, fmt
        )
    except tts_mod.TTSEncodingError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "tts response",
            extra={
                "resp_model": model,
                "resp_voice": voice,
                "resp_format": fmt,
                "resp_sample_rate": synth.sample_rate,
                "resp_bytes": len(audio_bytes),
                "resp_content_type": content_type,
            },
        )
    return Response(content=audio_bytes, media_type=content_type)


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile | None = File(default=None),
    file_path: str | None = Form(default=None),
    model: str = Form(...),
    language: str | None = Form(default=None),
    response_format: str = Form(default="json"),
    prompt: str | None = Form(default=None),
    temperature: float | None = Form(default=None),
    timestamp_granularities: list[str] = Form(
        default=[], alias="timestamp_granularities[]"
    ),
    diarization: str | None = Form(default=None),
) -> Any:
    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "asr request",
            extra={
                "endpoint": "/v1/audio/transcriptions",
                "req_model": model,
                "req_language": language,
                "req_response_format": response_format,
                "req_diarization": diarization,
                "req_timestamp_granularities": timestamp_granularities,
                "req_prompt": prompt,
                "req_temperature": temperature,
                "req_has_upload": file is not None and (file.filename or "") != "",
                "req_file_path": file_path,
            },
        )
    del prompt, temperature  # accepted for OpenAI compatibility, not used

    has_upload = file is not None and (file.filename or "") != ""
    has_path = file_path is not None and file_path.strip() != ""
    if has_upload == has_path:
        raise HTTPException(
            status_code=400,
            detail="must specify exactly one of `file` (multipart upload) "
            "or `file_path` (path under /v1/files)",
        )

    if model not in BACKENDS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown model {model!r}; configured: {list(BACKENDS.keys())}",
        )
    if not is_asr_backend(BACKENDS[model]):
        raise HTTPException(
            status_code=400,
            detail=f"model {model!r} is not an ASR model — try /v1/audio/speech",
        )

    do_diarize = _parse_diarization(diarization)

    if has_upload:
        assert file is not None  # for type checkers; has_upload guarantees this
        raw = await file.read()
        if len(raw) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"upload too large ({len(raw)} bytes "
                    f"> {config.MAX_UPLOAD_BYTES})"
                ),
            )
        original_name = file.filename or "audio"
    else:
        assert file_path is not None
        try:
            raw, original_name = await load_audio_from_path(file_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (downloads_mod.DownloadError, files_mod.FilePathError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        payload = await run_transcription_pipeline(
            raw=raw,
            original_name=original_name,
            model=model,
            language=language,
            response_format=response_format,
            do_diarize=do_diarize,
            granularities=timestamp_granularities,
        )
    except KeyError as exc:
        # Race: model went away between the 404 check above and the
        # pipeline's own check (config reload, etc.). Map to 404.
        raise HTTPException(
            status_code=404,
            detail=f"unknown model {exc.args[0]!r}",
        ) from exc
    except NotStereoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AudioConversionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fmt = (response_format or "json").lower()
    if log.isEnabledFor(logging.DEBUG):
        transcript = payload.get("text") if isinstance(payload, dict) else payload
        log.debug(
            "asr transcript",
            extra={
                "resp_model": model,
                "resp_format": fmt,
                "resp_source": original_name,
                "resp_bytes": len(raw),
                "transcript": transcript,
            },
        )
    return _wrap_payload(payload, fmt=fmt)


async def run_transcription_pipeline(
    *,
    raw: bytes,
    original_name: str,
    model: str,
    language: str | None,
    response_format: str,
    do_diarize: bool,
    granularities: list[str] | None = None,
) -> str | dict[str, Any]:
    """Run the post-audio-resolution transcribe flow; return the raw payload.

    Returns:
      str for fmt in {text/txt/srt/vtt}; dict for fmt in {json/verbose_json}.

    Raises:
      KeyError                — unknown model slug (caller maps to 404)
      NotStereoError          — diarization=true on a mono / >2ch input (400)
      AudioConversionError    — ffmpeg couldn't decode the bytes (400)

    Shared between the HTTP endpoint and the MCP ``transcribe`` tool so
    both stay in lock-step on eviction, language defaults, and the render.
    """
    if model not in BACKENDS:
        raise KeyError(model)
    backend = BACKENDS[model]
    entry = REGISTRY[model]
    source_lang = language or entry.get("default_source_lang")
    target_lang = entry.get("default_target_lang", source_lang)
    task = entry.get("default_task", "asr")
    fmt = (response_format or "json").lower()
    # Timestamps are needed for verbose_json (always) and srt/vtt (built from
    # segments). Cheaper formats (text/json) skip the timestamp pass — except
    # under diarization, where we use per-segment timestamps to interleave
    # L/R speakers chronologically. Without timestamps the text/json output
    # would degenerate to "L: <all L text>\nR: <all R text>" blocks.
    needs_timestamps = fmt in _VERBOSE_FORMATS or do_diarize
    grans = list(granularities or [])

    await _evict_siblings(model)

    if do_diarize:
        l_path, r_path = await asyncio.to_thread(
            to_wav_16k_split_lr, raw, original_name
        )
        try:
            duration = await asyncio.to_thread(_wav_duration_seconds, l_path)
            # Transcribe channels sequentially through the same backend so the
            # model only sits resident once.
            l_res = await backend.transcribe(
                l_path,
                source_lang=source_lang,
                target_lang=target_lang,
                task=task,
                with_timestamps=needs_timestamps,
            )
            r_res = await backend.transcribe(
                r_path,
                source_lang=source_lang,
                target_lang=target_lang,
                task=task,
                with_timestamps=needs_timestamps,
            )
        finally:
            for p in (l_path, r_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

        result = _merge_lr_results(l_res, r_res)
        if duration is not None and result.duration is None:
            result.duration = duration
        if result.language is None:
            result.language = source_lang
        return _render_payload(
            result, fmt=fmt, task=task, granularities=grans, diarized=True
        )

    wav_path = await asyncio.to_thread(to_wav_16k_mono, raw, original_name)
    try:
        duration = await asyncio.to_thread(_wav_duration_seconds, wav_path)
        result = await backend.transcribe(
            wav_path,
            source_lang=source_lang,
            target_lang=target_lang,
            task=task,
            with_timestamps=needs_timestamps,
        )
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass

    if duration is not None and result.duration is None:
        result.duration = duration
    if result.language is None:
        result.language = source_lang
    return _render_payload(
        result, fmt=fmt, task=task, granularities=grans, diarized=False
    )


def _wrap_payload(payload: str | dict[str, Any], *, fmt: str) -> Any:
    """Translate the pipeline payload into the FastAPI response object."""
    if fmt in ("text", "txt"):
        return PlainTextResponse(str(payload))
    if fmt == "srt":
        return PlainTextResponse(str(payload), media_type="application/x-subrip")
    if fmt == "vtt":
        return PlainTextResponse(str(payload), media_type="text/vtt")
    return payload


def _resolve_files_path(raw: str) -> Any:
    try:
        rel = files_mod.sanitize_path(raw)
        return files_mod.resolve_under(config.FILES_DIR, rel), str(rel)
    except files_mod.FilePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/files")
def files_list() -> dict[str, Any]:
    return {"files": files_mod.list_files(config.FILES_DIR)}


@app.put("/v1/files/{path:path}")
async def files_put(path: str, request: Request) -> JSONResponse:
    dest, rel_str = _resolve_files_path(path)
    body = await request.body()
    if len(body) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"upload too large ({len(body)} bytes > {config.MAX_UPLOAD_BYTES})",
        )
    await asyncio.to_thread(files_mod.write_atomic, dest, body)
    return JSONResponse(
        {"path": rel_str, "size": len(body)},
        status_code=201,
    )


@app.get("/v1/files/{path:path}")
def files_get(path: str) -> FileResponse:
    src, rel_str = _resolve_files_path(path)
    if src.is_symlink() or not src.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {rel_str}")
    mime, _ = mimetypes.guess_type(src.name)
    return FileResponse(
        path=str(src),
        media_type=mime or "application/octet-stream",
        filename=src.name,
    )


@app.delete("/v1/files/{path:path}")
def files_delete(path: str) -> JSONResponse:
    target, rel_str = _resolve_files_path(path)
    if target.is_symlink() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {rel_str}")
    try:
        target.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"unlink failed: {exc}") from exc
    files_mod.prune_empty_parents(target, config.FILES_DIR)
    return JSONResponse({"deleted": rel_str}, status_code=200)


def _render_payload(
    result: TranscribeResult,
    *,
    fmt: str,
    task: str,
    granularities: list[str],
    diarized: bool,
) -> str | dict[str, Any]:
    """Convert a TranscribeResult into the raw payload for the response.

    Returns a string for plain-text formats (text/txt/srt/vtt) and a dict
    for JSON formats (json/verbose_json). The HTTP wrapper / MCP tool then
    decides how to serialise + transport it.
    """
    if fmt in ("text", "txt"):
        return _diarized_text(result) if diarized else result.text
    if fmt == "verbose_json":
        return _verbose_json_response(result, task=task, granularities=granularities)
    if fmt == "srt":
        return _segments_to_srt(_segments_for_subtitles(result), diarized=diarized)
    if fmt == "vtt":
        return _segments_to_vtt(_segments_for_subtitles(result), diarized=diarized)
    if diarized:
        return {"text": _diarized_text(result)}
    return {"text": result.text}


def _tag_channel(items: list[dict], channel: str) -> list[dict]:
    """Return a fresh list with `channel` annotated on each segment/word."""
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        copy = dict(item)
        copy["channel"] = channel
        out.append(copy)
    return out


def _merge_lr_results(
    l_res: TranscribeResult, r_res: TranscribeResult
) -> TranscribeResult:
    """Combine left+right per-channel results into one diarized TranscribeResult.

    Segments and words are tagged with channel ("L"/"R") and merged by start
    time so the timeline reads chronologically across speakers. Text is
    rebuilt by walking the merged segments and prefixing each with its
    channel (one transcript line per channel switch / segment), matching the
    text/srt/vtt output shape.
    """
    l_segs = _tag_channel(list(l_res.segments), "L")
    r_segs = _tag_channel(list(r_res.segments), "R")
    merged_segs = sorted(
        l_segs + r_segs, key=lambda s: (float(s.get("start") or 0), s["channel"])
    )
    # Re-id after merge so downstream consumers see contiguous ids.
    for idx, seg in enumerate(merged_segs):
        seg["id"] = idx

    l_words = _tag_channel(list(l_res.words), "L")
    r_words = _tag_channel(list(r_res.words), "R")
    merged_words = sorted(
        l_words + r_words, key=lambda w: (float(w.get("start") or 0), w["channel"])
    )

    if merged_segs:
        # Plain-text / json output collapses consecutive same-channel segments
        # into one turn line so the reader sees "L: <whole sentence>" instead
        # of one short line per breath. Verbose_json / srt / vtt keep the raw
        # granular segments — clients reading those need the per-segment
        # timestamps. See _merge_consecutive_same_channel for the rule.
        turn_text_lines: list[str] = []
        cur_channel: str | None = None
        cur_parts: list[str] = []
        for seg in merged_segs:
            chan = seg["channel"]
            piece = str(seg.get("text", "")).strip()
            if not piece:
                continue
            if chan != cur_channel:
                if cur_channel is not None and cur_parts:
                    turn_text_lines.append(
                        f"{cur_channel}: {' '.join(cur_parts).strip()}"
                    )
                cur_channel = chan
                cur_parts = [piece]
            else:
                cur_parts.append(piece)
        if cur_channel is not None and cur_parts:
            turn_text_lines.append(f"{cur_channel}: {' '.join(cur_parts).strip()}")
        text = "\n".join(turn_text_lines)
    else:
        # Backends that don't emit segments (SALM): fall back to whole-channel
        # text blocks, one per channel that produced output.
        parts: list[str] = []
        if l_res.text.strip():
            parts.append(f"L: {l_res.text.strip()}")
        if r_res.text.strip():
            parts.append(f"R: {r_res.text.strip()}")
        text = "\n".join(parts)

    duration = max(
        (d for d in (l_res.duration, r_res.duration) if d is not None), default=None
    )
    language = l_res.language or r_res.language

    return TranscribeResult(
        text=text,
        segments=merged_segs,
        words=merged_words,
        language=language,
        duration=duration,
        supports_timestamps=(l_res.supports_timestamps or r_res.supports_timestamps),
    )


def _diarized_text(result: TranscribeResult) -> str:
    """Render the channel-prefixed text body. `_merge_lr_results` already
    formats text this way; this is a thin alias for clarity at call sites.
    """
    return result.text


def _wav_duration_seconds(wav_path: str) -> float | None:
    """Return duration of a 16-bit mono PCM WAV in seconds."""
    import wave

    try:
        with wave.open(wav_path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if not rate:
                return None
            return frames / float(rate)
    except (OSError, wave.Error):
        return None


def _verbose_json_response(
    result: TranscribeResult,
    *,
    task: str,
    granularities: list[str],
) -> dict[str, Any]:
    """Build OpenAI-shaped verbose_json. Whisper-only fields are null-filled
    so clients reading e.g. segment.avg_logprob don't crash.

    Note on granularities: OpenAI defaults to `segment` only, with `word`
    opt-in via `timestamp_granularities[]=word`. The LiteLLM proxy collapses
    repeated form fields to a single value (last wins), so a client asking
    for both via LiteLLM arrives here as `['word']`. To avoid that footgun
    we always emit both — it's free for us since the backend computes both
    in the same pass, and clients that only read one are unaffected.
    """
    del granularities  # always emit both; see docstring

    segments_out: list[dict] = []
    for seg in result.segments:
        item: dict[str, Any] = {
            "id": seg.get("id", 0),
            "seek": 0,
            "start": seg["start"],
            "end": seg["end"],
            "text": seg.get("text", ""),
            "tokens": [],
            "temperature": 0.0,
            "avg_logprob": None,
            "compression_ratio": None,
            "no_speech_prob": None,
        }
        if "channel" in seg:
            item["channel"] = seg["channel"]
        segments_out.append(item)

    return {
        "task": "translate" if task == "ast" else "transcribe",
        "language": result.language or "en",
        "duration": result.duration if result.duration is not None else 0.0,
        "text": result.text,
        "segments": segments_out,
        "words": list(result.words),
    }


def _segments_for_subtitles(result: TranscribeResult) -> list[dict]:
    if result.segments:
        return result.segments
    # Fallback: one segment spanning the full audio duration.
    end = result.duration if result.duration is not None else 0.0
    return [{"id": 0, "start": 0.0, "end": end, "text": result.text}]


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _format_vtt_timestamp(seconds: float) -> str:
    return _format_srt_timestamp(seconds).replace(",", ".")


def _seg_text(seg: dict, *, diarized: bool) -> str:
    text = str(seg.get("text", "")).strip()
    if diarized:
        channel = seg.get("channel")
        if channel:
            return f"{channel}: {text}"
    return text


def _segments_to_srt(segments: list[dict], *, diarized: bool = False) -> str:
    lines: list[str] = []
    for idx, seg in enumerate(segments, start=1):
        lines.append(str(idx))
        lines.append(
            f"{_format_srt_timestamp(seg['start'])} --> "
            f"{_format_srt_timestamp(seg['end'])}"
        )
        lines.append(_seg_text(seg, diarized=diarized))
        lines.append("")
    return "\n".join(lines)


def _segments_to_vtt(segments: list[dict], *, diarized: bool = False) -> str:
    lines: list[str] = ["WEBVTT", ""]
    for seg in segments:
        lines.append(
            f"{_format_vtt_timestamp(seg['start'])} --> "
            f"{_format_vtt_timestamp(seg['end'])}"
        )
        lines.append(_seg_text(seg, diarized=diarized))
        lines.append("")
    return "\n".join(lines)


# MCP server wiring. Built once at import time so the lifespan can drive
# its session manager and FastAPI routes know about the mount.
MCP_SERVER = build_mcp_server(
    backends=BACKENDS,
    registry=REGISTRY,
    transcribe_runner=run_transcription_pipeline,
    audio_loader=load_audio_from_path,
)
app.mount("/v1/mcp", MCP_SERVER.streamable_http_app())


def main() -> int:
    configure_logging()
    import uvicorn

    log.info("talkies: starting on 0.0.0.0:8000")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config=None,
        ws_max_size=max(
            config.STREAM_MAX_FRAME_BYTES,
            MAX_CONTROL_MESSAGE_BYTES,
        ),
        ws_max_queue=_STREAM_MAX_PENDING_MESSAGES,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

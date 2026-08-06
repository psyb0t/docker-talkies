"""MCP server for talkies — mounted at ``/v1/mcp`` in the main FastAPI app.

Exposes six tools so an agent can drive the same surface a human gets via
the HTTP API:

  - ``list_models``  — what ASR slugs are loadable
  - ``transcribe``   — run ASR on a URL or pre-staged file path
  - ``list_files``   — what's currently staged under the files area
  - ``put_file``     — upload a file (base64-encoded body)
  - ``get_file``     — read a staged file (base64-encoded body back out)
  - ``delete_file``  — remove a staged file

Why a separate module: avoids a circular import between ``server.py``
(which holds the shared ``BACKENDS`` / ``REGISTRY`` state + the
``run_transcription_pipeline`` callable) and this module. Server.py calls
``build_mcp_server(...)`` once at startup and mounts the returned ASGI app.

Why base64 for ``put_file`` / ``get_file``: MCP tool args / results travel
over JSON-RPC, which can't carry raw bytes. The decoded bytes are
hard-capped at ``config.MAX_UPLOAD_BYTES`` to match the HTTP
``PUT /v1/files/{path}`` ceiling.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP

from . import config
from . import files as files_mod

_log = logging.getLogger("talkies.mcp")


TranscribeRunner = Callable[..., Awaitable[Any]]
"""``run_transcription_pipeline`` from server.py, injected to dodge circularity."""

AudioLoader = Callable[[str], Awaitable[tuple[bytes, str]]]
"""``load_audio_from_path`` from server.py, injected for the same reason."""


def build_mcp_server(
    *,
    backends: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    transcribe_runner: TranscribeRunner,
    audio_loader: AudioLoader,
) -> FastMCP:
    """Construct the FastMCP server with all tools wired up.

    The HTTP path for the streamable transport is set to ``/`` so when the
    server is mounted at ``/v1/mcp`` clients connect to ``/v1/mcp``
    directly (not ``/v1/mcp/mcp``, which would be the SDK default).
    """
    mcp = FastMCP(
        name="talkies",
        instructions=(
            "Server-side ASR + file staging. Use list_models to discover "
            "available slugs, then transcribe with file_path = either an "
            "http(s) URL or a path under the staging area (upload first "
            "via put_file)."
        ),
        stateless_http=True,
        json_response=True,
    )
    mcp.settings.streamable_http_path = "/"

    @mcp.tool()
    async def list_models() -> dict[str, Any]:
        """List the ASR models this server can run.

        Returns a list of ``{slug, executor, default_source_lang,
        default_target_lang, default_task, loaded}`` entries. Pick one and
        pass its ``slug`` to ``transcribe`` as ``model``. TTS-only models
        (e.g. ``kokoro-82m``) are filtered out — call them over the HTTP
        ``/v1/audio/speech`` endpoint instead.
        """
        out: list[dict[str, Any]] = []
        for slug, backend in backends.items():
            if not hasattr(backend, "transcribe"):
                continue
            entry = registry.get(slug, {})
            out.append(
                {
                    "slug": slug,
                    "executor": entry.get("executor", "whisper"),
                    "default_source_lang": entry.get("default_source_lang"),
                    "default_target_lang": entry.get("default_target_lang"),
                    "default_task": entry.get("default_task", "asr"),
                    "loaded": backend.loaded(),
                }
            )
        return {"models": out}

    @mcp.tool()
    async def transcribe(
        file_path: str,
        model: str,
        language: str | None = None,
        response_format: str = "json",
        diarization: bool = False,
    ) -> str:
        """Run ASR on a server-side or remote audio file.

        Args:
            file_path: Either an ``http://`` / ``https://`` URL (downloaded
                once, cached under ``downloads/<hash>-<basename>``) or a
                path relative to the staging area populated via ``put_file``.
                Leading ``/`` is stripped; ``..`` segments are rejected.
            model: Model slug. Discover with ``list_models``.
            language: Optional BCP-47 source-language hint (e.g. ``en``,
                ``de``). Models that need this default to their configured
                ``default_source_lang`` when omitted.
            response_format: One of ``json``, ``verbose_json``, ``text``,
                ``srt``, ``vtt``. ``json`` returns a compact ``{"text": ...}``
                object; ``verbose_json`` returns the full Whisper-shape
                payload with segments + words.
            diarization: If true and the audio is stereo, splits left/right
                channels as two speakers and returns a chronological L/R
                interleaved transcript. Errors with non-stereo input.

        Returns:
            For ``json`` / ``verbose_json``: JSON-encoded string. For
            ``text`` / ``srt`` / ``vtt``: the raw text / subtitle stream.
        """
        try:
            raw, original_name = await audio_loader(file_path)
        except FileNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            # downloads_mod.DownloadError / files_mod.FilePathError both
            # land here. We can't import them at module-load without
            # tightening the dependency surface further, but ValueError
            # is what FastMCP serialises to a tool-error response anyway.
            raise ValueError(str(exc)) from exc

        try:
            payload = await transcribe_runner(
                raw=raw,
                original_name=original_name,
                model=model,
                language=language,
                response_format=response_format,
                do_diarize=diarization,
                granularities=[],
            )
        except KeyError as exc:
            raise ValueError(
                f"unknown model {exc.args[0]!r}; configured: "
                f"{list(backends.keys())}"
            ) from exc
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc

        if isinstance(payload, str):
            return payload
        return json.dumps(payload, ensure_ascii=False)

    @mcp.tool()
    async def list_files() -> dict[str, Any]:
        """List files currently staged under the server's file area.

        Returns ``{"files": [{path, size, modified}]}``. ``path`` values
        can be passed straight to ``transcribe`` as ``file_path``.
        """
        return {"files": files_mod.list_files(config.FILES_DIR)}

    @mcp.tool()
    async def put_file(path: str, content_base64: str) -> dict[str, Any]:
        """Upload a file to the staging area.

        Args:
            path: Destination path relative to the staging root. Leading
                ``/`` is stripped; ``..`` segments are rejected.
            content_base64: File contents, base64-encoded. After decode the
                size is capped at ``TALKIES_MAX_UPLOAD_BYTES``.

        Returns:
            ``{"path": str, "size": int}`` on success.
        """
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"content_base64 is not valid base64: {exc}") from exc
        if len(data) > config.MAX_UPLOAD_BYTES:
            raise ValueError(
                f"upload too large ({len(data)} bytes > " f"{config.MAX_UPLOAD_BYTES})"
            )
        try:
            rel = files_mod.sanitize_path(path)
            dest = files_mod.resolve_under(config.FILES_DIR, rel)
        except files_mod.FilePathError as exc:
            raise ValueError(str(exc)) from exc
        files_mod.write_atomic(dest, data)
        return {"path": str(rel), "size": len(data)}

    @mcp.tool()
    async def get_file(path: str) -> dict[str, Any]:
        """Read a staged file back as base64.

        Args:
            path: Path relative to the staging root.

        Returns:
            ``{"path": str, "size": int, "content_base64": str}``. Use
            sparingly — bytes round-trip through JSON, so big files chew
            through token budget on the client side.
        """
        try:
            rel = files_mod.sanitize_path(path)
            src = files_mod.resolve_under(config.FILES_DIR, rel)
        except files_mod.FilePathError as exc:
            raise ValueError(str(exc)) from exc
        if src.is_symlink() or not src.is_file():
            raise ValueError(f"file not found: {rel}")
        data = src.read_bytes()
        if len(data) > config.MAX_UPLOAD_BYTES:
            raise ValueError(
                f"file too large to return over MCP "
                f"({len(data)} bytes > {config.MAX_UPLOAD_BYTES})"
            )
        return {
            "path": str(rel),
            "size": len(data),
            "content_base64": base64.b64encode(data).decode("ascii"),
        }

    @mcp.tool()
    async def delete_file(path: str) -> dict[str, Any]:
        """Delete a staged file.

        Empty parent directories are pruned up to (but not including) the
        staging root.

        Returns:
            ``{"deleted": str}`` on success.
        """
        try:
            rel = files_mod.sanitize_path(path)
            target = files_mod.resolve_under(config.FILES_DIR, rel)
        except files_mod.FilePathError as exc:
            raise ValueError(str(exc)) from exc
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"file not found: {rel}")
        target.unlink()
        files_mod.prune_empty_parents(target, config.FILES_DIR)
        return {"deleted": str(rel)}

    _log.info("mcp server initialised: 6 tools")
    return mcp

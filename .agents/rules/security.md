# Security rules

Prefix: SEC.

Talkies is a self-hosted service with one optional shared token and server-wide staged data; it is not multi-tenant (`docs/operations.md`). The repository is public.

## Secrets

- **SEC1:** Never commit, print or quote secrets: `.env` files, tokens, keys, credentials. `.gitignore` and `.dockerignore` exclude the usual secret files; keep them excluded. The server logs only whether auth is on (`"auth=%s"` in `_lifespan`), never the token.
- **SEC2:** Secret scanning uses `.gitleaks.toml`. Its allowlist names paths only, never a regex on the matched text, and lists every large gitignored directory because gitleaks `dir` mode ignores `.gitignore`. A new local cache or clone directory at the root gets an entry there.

## Authentication

- **SEC3:** Auth stays ASGI middleware (`BearerAuthMiddleware` in `src/talkies/auth.py`) so one check covers HTTP routes, the WebSocket upgrade (closed with 4401) and mounted sub-apps such as `/v1/mcp`. The token is compared with `hmac.compare_digest`. Only `/healthz` and `OPTIONS` preflights are exempt; any new mount or route sits behind the middleware. A rejected WebSocket never echoes the token (`tests/test_auth.py`).

## Input handling

- **SEC4:** Every caller-supplied staging path goes through `files_mod.sanitize_path` and then `files_mod.resolve_under(config.FILES_DIR, ...)` before any disk access, on HTTP and MCP alike: no null bytes, backslashes, empty, `.` or `..` segments, and the resolved path must stay under the root. Reads and deletes refuse symlinks, listings skip them, and writes go through `write_atomic` (`src/talkies/files.py`).
- **SEC5:** Voice directories are scanned with the same containment: each resolved file must be `is_relative_to` the resolved voice root, so symlinks that escape it and dotfiles are skipped (`src/talkies/models/chatterbox.py`, `src/talkies/models/qwen3_tts.py`, `test_symlink_escaping_voices_dir_is_skipped` and `test_dotfile_wavs_are_skipped` in `tests/test_chatterbox.py`).
- **SEC6:** Fetch remote audio only through `downloads.ensure_downloaded`: redirects are followed by hand (`follow_redirects=False`, at most `_MAX_REDIRECTS`) so the SSRF check runs on every hop when `TALKIES_BLOCK_PRIVATE_DOWNLOADS` is on, the body streams to disk under `TALKIES_MAX_DOWNLOAD_BYTES`, and a partial file is removed on failure. The guard defaults to off for LAN use, as the module docstring explains; do not change that default on your own.
- **SEC7:** Every ingress path has a size cap. `TALKIES_MAX_UPLOAD_BYTES` covers multipart transcription uploads, `PUT /v1/files/{path}` and both MCP file tools; WebSocket frames, control messages, buffers and duration are capped by the `TALKIES_STREAM_*` settings, and `main()` passes matching `ws_max_size` and `ws_max_queue` to uvicorn (`test_main_bounds_uvicorn_websocket_ingress`). A new input path gets a cap in the same change.
- **SEC8:** Run external programs with an argument list and a timeout, never through a shell: `subprocess.run(cmd, capture_output=True, timeout=...)` in `src/talkies/audio.py`, `asyncio.create_subprocess_exec` with `asyncio.wait_for` in `src/talkies/tts.py`.

## Logs

- **SEC9:** Speech content (TTS `input`, `instructions`, reference transcripts, ASR transcripts, prompts, full request bodies) is logged only at DEBUG and only inside `if log.isEnabledFor(logging.DEBUG):` blocks, as `speech` and `transcribe` in `src/talkies/server.py` do. At INFO and above log slugs, sizes, counts and durations. `configure()` in `src/talkies/logging.py` warns at startup when DEBUG is active; keep that warning (`test_configure_debug_emits_pii_warning`).

## Models and images

- **SEC10:** The images never `torch.load` caller-supplied files, never call Lightning `load_from_checkpoint`, and never train; the open CVEs listed in the `Dockerfile.cuda` header and `scripts/heavy-deps-cuda.in` are accepted on exactly that basis. A change that loads a model or checkpoint chosen by a caller reopens those findings and must say so.
- **SEC11:** Pin everything an image pulls: base images and the uv image by digest, parakeet.cpp by commit SHA (`Dockerfile`), downloaded wheels and bundles by URL plus a SHA-256 checked with `sha256sum --check` before use (`Dockerfile.cuda`), the heavy stack with `--require-hashes`, light dependencies from the frozen `uv.lock` behind the seven-day `exclude-newer` gate (`pyproject.toml`, `scripts/bump_exclude_newer.sh`), and `mcp-remote` by exact version. The maintainer's reusable workflows are referenced at `@master` on purpose; do not pin them.
- **SEC12:** Both images run as the non-root `talkies` user (uid 1000) and keep the `HEALTHCHECK` against `/healthz`.
- **SEC13:** `docker run` examples in `README.md` and `docs/` publish the port on loopback (`-p 127.0.0.1:8000:8000`), as `docs/operations.md` recommends.
- **SEC14:** Every third-party component that ends up inside a published image is listed in `THIRD_PARTY.md` with its license, source and where it lives, and a license text goes into `LICENSES/` when the license requires shipping it (GPL-3.0 for espeak-ng, MIT for the faster-qwen3-tts voices).

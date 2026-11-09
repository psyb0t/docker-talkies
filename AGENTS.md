# AGENTS.md

Guide for coding agents working on this repository. If you only want to call a running Talkies server from an agent, read [.agents/skills/talkies/SKILL.md](.agents/skills/talkies/SKILL.md) instead; this file is about changing the code.

The project rules live in `.agents/rules/`, one file per topic. They are authoritative for this repository.

## Agent setup

This repository expects the psyb0t rig (`.claude-rig`, or `.codex-rig` once that exists).

`CLAUDE.md` is a committed symlink to `AGENTS.md`, and `.claude` is a committed symlink to `.agents`, so Claude Code reads the same instructions, rules and skills as Codex. Claude's Edit and Write tools refuse to write through a symlink: edit `AGENTS.md` and `.agents/rules/*.md` directly, never the paths under `CLAUDE.md` or `.claude/`.

## What it is

Talkies is a self-hosted speech service in one Docker image: OpenAI-compatible file transcription (`POST /v1/audio/transcriptions`) and text-to-speech (`POST /v1/audio/speech`), live ASR over WebSocket, server-side file staging, model lifecycle controls and a Streamable HTTP MCP endpoint for ASR and file tools.

It puts many open ASR and TTS models (Whisper, Parakeet, Canary, Nemotron through parakeet.cpp, Sherpa-ONNX, Vosk, two phoneme recognizers, Kokoro, Qwen3-TTS, Chatterbox Turbo) behind one HTTP surface, loads them lazily, keeps one model family resident at a time on limited memory, and ships as a CPU image and a CUDA image.

## Architecture

- **One FastAPI process.** `src/talkies/server.py` builds the app, every route, the admission controller, the idle sweeper and the MCP mount, and runs uvicorn on `0.0.0.0:8000` (`main()`). `python -m talkies` (`src/talkies/__main__.py`) is the entry point the image runs.
- **Configuration at import.** `src/talkies/config.py` reads every `TALKIES_*` variable when it is imported and raises `ValueError` on a bad value, so the process does not start. `load_registry()` reads the model registry (`TALKIES_MODELS_FILE`, default `/app/models.json`), applies `TALKIES_ENABLED_MODELS` and resolves each entry's `max_concurrency`.
- **Model registry and backends.** `models.json` (CUDA image) and `models-cpu.json` (CPU image) map a slug to a Hugging Face `repo`, an `executor` and executor-specific fields. `build_backends()` in `src/talkies/models/__init__.py` turns each entry into a backend object from `src/talkies/models/`. Backends are duck-typed: ASR has `transcribe`, TTS has `synthesize` and `voices`, live ASR adds `start_stream` (`src/talkies/models/base.py`).
- **Entrypoint prefetch.** `entrypoint.sh` creates `models/`, `files/` and `custom-voices/` under `TALKIES_DATA_DIR` (`/data`), downloads each enabled slug's snapshot into `/data/models/<slug>/` with Hugging Face offline mode unset for that step only, then `exec`s the server, which runs with `HF_HUB_OFFLINE=1`.
- **Admission and eviction.** `_reserve_model()` and `_release_model()` in `server.py` count active inference per model across HTTP ASR, MCP ASR, WebSocket ASR, buffered TTS and streaming TTS. A request for a model while another model has active work gets 409, work above `max_concurrency` gets 429, and loading a model unloads its loaded siblings and drains Torch CUDA caches (`_wait_for_gpu_drain()`). `_idle_sweeper()` unloads backends idle longer than `TALKIES_MODEL_TTL`.
- **File transcription pipeline.** Input is a multipart upload, a staged path under `/data/files` (`src/talkies/files.py`) or an `http(s)` URL downloaded once into `/data/files/downloads/` (`src/talkies/downloads.py`). Audio is converted to 16 kHz mono WAV with ffmpeg (`src/talkies/audio.py`), long audio is split by Silero VAD (`src/talkies/vad.py`), and the result is rendered as `json`, `verbose_json`, `text`, `srt` or `vtt`. Stereo diarization transcribes left and right channels separately and interleaves them.
- **Speech.** TTS backends return mono int16 PCM (`SynthesisResult`); `src/talkies/tts.py` encodes it with ffmpeg into `mp3`, `opus`, `aac`, `flac` or `wav`, or returns raw `pcm`. Qwen3 streams PCM chunks when `response_format` is `pcm`.
- **Live ASR.** `WS /v1/audio/transcriptions/stream` takes a JSON `start` message, then binary 16 kHz PCM frames, then `end` or `cancel`. Protocol primitives are in `src/talkies/asr_streaming.py`. Sherpa, Vosk and parakeet.cpp stream natively; Whisper streams through the bounded rolling adapter `src/talkies/models/whisper_stream.py`.
- **MCP.** `src/talkies/mcp_server.py` builds a stateless FastMCP server with six tools (`list_models`, `transcribe`, `list_files`, `put_file`, `get_file`, `delete_file`), mounted at `/v1/mcp`. Its `transcribe` tool runs the same pipeline function as the HTTP route.
- **Auth.** `BearerAuthMiddleware` (`src/talkies/auth.py`) is ASGI middleware, so one optional token (`TALKIES_AUTH_TOKEN`) covers HTTP, WebSocket and the mounted MCP app. `/healthz` is always open.
- **Agent integrations.** `.agents/skills/talkies/` is the agent skill; `.agents/plugins/talkies/` is an OpenClaw plugin, a small Node bridge (`bin/cli.js`) that runs `mcp-remote` against `$TALKIES_URL/v1/mcp`; `.agents/.claude-plugin/` and `.agents/.codex-plugin/` hold the Claude Code and Codex plugin manifests; `server.json` is the MCP Registry entry.

Data flow:

```text
models.json / models-cpu.json + TALKIES_ENABLED_MODELS
  -> entrypoint.sh (snapshot_download) -> /data/models/<slug>/
  -> server.py import: config.load_registry() -> build_backends()

client -> BearerAuthMiddleware -> route
  POST /v1/audio/transcriptions  -> upload | /data/files | URL cache
                                 -> _reserve_model -> ffmpeg 16 kHz WAV -> VAD -> backend.transcribe -> renderer
  POST /v1/audio/speech          -> _reserve_model -> backend.synthesize -> ffmpeg encode | PCM stream
  WS   /v1/audio/transcriptions/stream -> _reserve_model(streaming) -> backend.start_stream -> events
  /v1/mcp (FastMCP)              -> same transcription pipeline and file helpers
```

## Stack

- Language: Python 3.12 in every image (`requires-python = ">=3.10,<3.13"` in `pyproject.toml`).
- Framework: FastAPI and uvicorn, pydantic, the `mcp` SDK (FastMCP), httpx.
- ML runtimes: PyTorch with NeMo (`nemo_toolkit[asr]`), faster-whisper (CTranslate2), silero-vad, kokoro, ONNX Runtime, sherpa-onnx, vosk, transformers, faster-qwen3-tts and chatterbox-tts (CUDA image only), parakeet.cpp loaded through ctypes. ffmpeg for audio conversion and encoding, espeak-ng for Kokoro G2P.
- Packaging: hatchling; light dependencies in `uv.lock`, the heavy ML stack in hash-locked `requirements-heavy-cpu.txt` and `requirements-heavy-cuda.txt`, Chatterbox wheels in `requirements-chatterbox.txt`.
- Deploy: Docker images `psyb0t/talkies:<tag>` (CPU, `Dockerfile`) and `psyb0t/talkies:<tag>-cuda` (`Dockerfile.cuda`), both `linux/amd64`, built and pushed by `.github/workflows/pipeline.yml`. No compose file ships.
- CI: GitHub Actions through `psyb0t/reusable-github-workflows` at `@master`.

## Layout

| Path | What it owns |
| --- | --- |
| `src/talkies/server.py` | FastAPI app, every route, admission (`_reserve_model`, `_release_model`), idle sweeper, GPU drain, transcription pipeline and renderers, MCP mount, `main()`. |
| `src/talkies/config.py` | All `TALKIES_*` settings, parsed at import; `VALID_EXECUTORS`; `load_registry()`. |
| `src/talkies/auth.py` | Optional bearer-token ASGI middleware (HTTP and WebSocket). |
| `src/talkies/logging.py` | JSON log formatter on stdout and level parsing (`TALKIES_LOG_LEVEL`, fallback `LOG_LEVEL`). |
| `src/talkies/errors.py` | `ModelAdmissionError`, shared by HTTP, WebSocket and MCP. |
| `src/talkies/files.py`, `src/talkies/downloads.py` | Staging-area path sanitizing and containment; URL download cache with the optional SSRF guard and size cap. |
| `src/talkies/audio.py`, `src/talkies/vad.py`, `src/talkies/tts.py` | ffmpeg conversion to 16 kHz WAV and stereo split; Silero VAD chunking; TTS output encoding. |
| `src/talkies/asr_streaming.py` | Framework-independent live ASR protocol: start and control parsing, events, buffers. |
| `src/talkies/mcp_server.py` | FastMCP server and its six tools. |
| `src/talkies/models/` | One module per executor (`whisper.py`, `parakeet.py`, `parakeet_cpp.py`, `multitask.py` for `canary_multitask`, `salm.py` for `canary_salm`, `sherpa.py`, `sherpa_offline_ctc.py`, `vosk.py`, `wav2vec2_phoneme.py`, `kokoro.py`, `kokoro_nvidia.py`, `qwen3_tts.py`, `chatterbox.py`), plus `base.py` (result types and protocols), `whisper_stream.py`, `stream_batch.py`, `_nemo_paths.py` and the factory in `__init__.py`. |
| `src/talkies/__init__.py` | `__version__` from package metadata, and an IPython stub installed before any NeMo import. |
| `models.json`, `models-cpu.json` | The bundled registries for the CUDA and CPU images. |
| `voices/qwen3/` | Built-in Qwen3-TTS reference voices (`alloy`, `echo`, `fable`: `.wav`, `.txt` transcript, `.lang`), copied into the CUDA image at `/opt/talkies/qwen3-voices/`. |
| `entrypoint.sh` | Creates the data directories, prefetches enabled snapshots, starts the server. |
| `Dockerfile`, `Dockerfile.cuda`, `Dockerfile.dev` | CPU image (builds parakeet.cpp from a pinned commit), CUDA image (hash-verified Sherpa CUDA wheel and parakeet.cpp release bundle, Chatterbox wheels), and the light dev image the Makefile runs tools in. |
| `pyproject.toml`, `uv.lock` | Project metadata, the canonical version, light runtime dependencies, the uv `exclude-newer` age gate, mypy settings. |
| `requirements-heavy-cpu.txt`, `requirements-heavy-cuda.txt` | Generated, hash-locked ML stacks; inputs are `scripts/heavy-deps-cpu.in` and `scripts/heavy-deps-cuda.in`. |
| `requirements-chatterbox.txt` | Hand-maintained, hash-pinned Chatterbox wheels for the CUDA image; its header explains why they bypass the resolver. |
| `scripts/` | `compile-heavy-deps.sh`, `bump_exclude_newer.sh` and the two `heavy-deps-*.in` inputs. Excluded from the Docker build context. |
| `tests/` | Offline unit tests (`test_*.py`) and `tests/integration/` (bash harness that runs the real images). |
| `docs/` | User documentation, one file per area; `README.md` links to it. |
| `.agents/` | Agent skill (`skills/talkies/`), OpenClaw plugin (`plugins/talkies/`), Claude and Codex plugin manifests, and these rules (`rules/`). Excluded from the Docker build context. |
| `.github/workflows/` | `pipeline.yml` (images, badges, ClawHub and MCP Registry publish), `collaborators-only.yml` (closes outside pull requests), `mirror-and-archive.yml` and `issue-pull.yml` (housekeeping). |
| `server.json`, `glama.json` | MCP Registry entry and Glama maintainer claim. |
| `THIRD_PARTY.md`, `LICENSES/` | Third-party components inside the published images and their license texts. |
| `.flake8`, `.gitleaks.toml`, `.gitattributes`, `.gitignore`, `.dockerignore` | Lint settings, secret-scan allowlist, LF for shell scripts, ignore lists. |

## Dev workflow

Every tool runs in the dev container (`Dockerfile.dev`, tag `psyb0t/talkies-dev:latest`) with the repo mounted at `/work`, except the image builds and the integration tests, which run on the host. `make help` lists the targets.

| Target | What it does |
| --- | --- |
| `make dev-image` | Builds the dev image (Python, uv, flake8, black, isort, mypy, pytest, the light web deps; no ML stack). Every dev target depends on it. |
| `make shell` | Interactive shell in the dev container. |
| `make lint` | `flake8 src` and `mypy src` in the dev container. |
| `make format` | `isort src` then `black src`. |
| `make test`, `make test-unit` | `pytest tests -v --ignore=tests/integration` in the dev container. |
| `make check` | `lint` then `test`. |
| `make build`, `make build-cuda`, `make build-all` | Builds the CPU and/or CUDA image, tagged `local`, `v<version>` and `latest` (`-cuda` suffix for CUDA). |
| `make run`, `make run-cuda` | Builds and runs the image on `PORT` (default 8000) with `~/.talkies-data` mounted as `/data`; `run-cuda` adds `--gpus all`. |
| `make test-integration` | `tests/integration/run.sh` on the host: builds the CUDA image unless `TALKIES_SKIP_BUILD=1`, then runs every `test_*.sh` and `e2e_*.sh`. Needs Docker with the NVIDIA runtime, `curl` and `jq`. |
| `make test-streaming` | Builds the CPU image and runs the real WebSocket streaming test (`e2e_streaming_asr.sh`). |
| `make test-streaming-custom`, `make test-streaming-custom-cuda` | Real Sherpa-ONNX and Vosk WebSocket and HTTP tests against `tests/integration/streaming-custom-models.json`, on the CPU image or (Sherpa only) the CUDA image. |
| `make pkg-add PKG=...`, `make pkg-remove PKG=...`, `make pkg-update PKG=...`, `make pkg-upgrade` | Move `exclude-newer` to UTC midnight seven days ago (`scripts/bump_exclude_newer.sh`), then change dependencies through uv. |
| `make pkg-lock` | `uv lock` at the current `exclude-newer`. |
| `make compile-heavy` | Regenerates both `requirements-heavy-*.txt` from `scripts/heavy-deps-*.in` with hashes. |
| `make version` | Prints `v` plus the `pyproject.toml` version. |
| `make clean` | Removes the local images and Python build and cache artifacts. |

What each test layer covers and when to use it: [.agents/rules/testing.md](.agents/rules/testing.md).

## Project-specific landmines

- **CI runs no tests and no lint.** `.github/workflows/pipeline.yml` builds and scans the two images, makes badges, and publishes on tags. `make check` and the integration tests are local only; nothing else catches a lint or unit-test failure before a release (TST1).
- **Model weights never enter git or the image.** `.gitignore` and `.dockerignore` both exclude `*.safetensors`, `*.bin`, `*.gguf`, `*.pt`, `*.pth`, `*.onnx`, `*.nemo`, `*.ckpt` and `/models/`. Weights reach a container only through `entrypoint.sh` at boot.
- **A non-empty model directory counts as cached.** `entrypoint.sh` skips any `/data/models/<slug>/` that has files in it, so an interrupted download is never resumed or repaired. Delete the directory to force a fresh fetch. Dependency repos (`dependencies` in a registry entry) go to the standard Hugging Face cache instead (`HF_HOME=/data/hf` in the CUDA image).
- **Without `TALKIES_ENABLED_MODELS` the first boot downloads every registry entry.** Restrict it when running images locally.
- **Two images, two registries.** The CPU image copies `models-cpu.json` to `/app/models.json`; the CUDA image copies `models.json`. Parakeet-TDT, Canary 1B, Canary-Qwen, Qwen3-TTS and Chatterbox exist only in the CUDA registry and image. The Qwen3 built-in voices, the Chatterbox wheels and the Sherpa CUDA wheel are CUDA only. The two images get parakeet.cpp differently: the CPU image compiles it from a pinned commit, the CUDA image installs a pinned release bundle.
- **One bad registry entry stops the whole server.** `load_registry()` runs when `server.py` is imported. An executor missing from `VALID_EXECUTORS` once killed the CUDA image at startup for every model (v0.15.1); `tests/test_config.py` now loads both shipped registries.
- **The dev image has no ML stack and no `mcp` package.** Unit tests stub `talkies.models` (whose `__init__.py` imports every backend) and `talkies.mcp_server`, and skip with `importorskip` where numpy, torch or sherpa-onnx is needed (TST3). `Dockerfile.dev` installs its own pinned copies of fastapi, httpx, uvicorn, pydantic and python-multipart, separate from `uv.lock` (MOD9).
- **Generated lock files.** `requirements-heavy-cpu.txt` and `requirements-heavy-cuda.txt` are generated by `make compile-heavy`; `uv.lock` is written by the `pkg-*` targets; `requirements-chatterbox.txt` is the hand-maintained exception (MOD9, MOD10). `setuptools<81` in `scripts/heavy-deps-cuda.in` is load-bearing: newer setuptools drops `pkg_resources` and Chatterbox fails at model load, which `Dockerfile.cuda` now checks at build time (v0.15.2).
- **The Docker build context excludes `scripts/`, `tests/`, `.agents/`, `README.md`, `Makefile` and `Dockerfile.dev`** (`.dockerignore`). Nothing in those paths can be `COPY`ed into an image.
- **`.e2e-cache/` and `.e2e-cache-streaming-custom/`** hold tens of GB of model snapshots for the integration harness (`HARNESS_CACHE_DIR`). They are gitignored and excluded from the build context and the secret scan; never commit or `COPY` them.
- **Untracked local directories.** `index-tts/` and `.research_files/` are research clones, not vendored code; `.testing/` and `.telemetry/` are local state. All are gitignored. `git-update.sh` and `rebase.sh` are the maintainer's local release scripts and are gitignored too; they are not part of the project and are not referenced by CI.
- **Debug logging records speech content.** At `debug` the server logs full TTS input, instructions and ASR transcripts, and `src/talkies/logging.py` warns about it at startup. See SEC9.
- **Outside pull requests are closed automatically** by `collaborators-only.yml` with a message that asks for an issue instead. Issues filed on the GitLab and Codeberg mirrors are pulled in by `issue-pull.yml`.

## Common changes

Adding a bundled model with an existing executor:

1. Add the entry to `models.json`, and to `models-cpu.json` if it runs on CPU (MOD1, MOD2, MOD3).
2. Update `docs/models.md`, the README model summary and the skill's slug lists and counts (DOC2).
3. Add a CHANGELOG entry (DOC7). If it adds an image dependency, see MOD9 and MOD10, and SEC14 for `THIRD_PARTY.md`.

Adding an executor:

1. Write the backend module in `src/talkies/models/` (MOD5, MOD6, MOD7).
2. Add the name to `VALID_EXECUTORS` in `src/talkies/config.py` and a branch in `build_backends()` (MOD4).
3. Add unit tests that run without the ML stack (TST2, TST3), and update the executor list in `docs/models.md` (DOC2).

Adding a setting:

1. Read it in `src/talkies/config.py` with validation (PY5, PY6, PY7).
2. Document it in `docs/configuration.md` and the skill's `references/setup.md` (DOC2), and test its parsing in `tests/test_config.py` (TST5).

Adding a route or an MCP tool: [api-design.md](.agents/rules/api-design.md) and [mcp.md](.agents/rules/mcp.md).

Releasing: [docs-and-releases.md](.agents/rules/docs-and-releases.md). Never commit, tag, push or publish unless the user asked for that specific action.

## Rules

Rules for every change: `.agents/rules/`. Read the area file before touching that area:

| File | Read before touching |
| --- | --- |
| `python-code.md` | Any `src/talkies/*.py`, `pyproject.toml`, `.flake8` |
| `api-design.md` | `src/talkies/server.py` routes, `src/talkies/asr_streaming.py`, `src/talkies/tts.py`, `src/talkies/errors.py` |
| `mcp.md` | `src/talkies/mcp_server.py`, the `/v1/mcp` mount, `.agents/plugins/talkies/`, `server.json` |
| `models.md` | `src/talkies/models/`, `models.json`, `models-cpu.json`, `entrypoint.sh`, `voices/`, the Dockerfiles, any `requirements-*.txt`, `scripts/heavy-deps-*.in`, `uv.lock` |
| `testing.md` | `tests/`, `tests/integration/` |
| `security.md` | `src/talkies/auth.py`, `files.py`, `downloads.py`, `logging.py`, voice directory handling, Dockerfiles, `.github/workflows/`, `.gitleaks.toml`, `THIRD_PARTY.md` |
| `docs-and-releases.md` | `README.md`, `docs/`, `CHANGELOG.md`, `.agents/skills/`, any version manifest, commits and tags |

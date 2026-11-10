# Model and image rules

Prefix: MOD.

Covers the registries (`models.json`, `models-cpu.json`), the backends in `src/talkies/models/`, model storage, voices, and how the images get their dependencies.

## Registries

- **MOD1:** Every registry entry declares `repo` and an explicit `executor`, as every shipped entry does (the code defaults a missing `executor` to `whisper`). When you add an entry, pin `revision` to a commit SHA and narrow the download with `download_patterns` when the repository holds variants or files the backend does not read, as the Sherpa, Vosk, `wav2vec2-xlsr-53-espeak` and `zipa-ipa` entries do (`docs/models.md`, "Use a custom registry"). `gguf_file` is kept only for the existing `parakeet_cpp` entries (`entrypoint.sh`); new entries use `download_patterns`.
- **MOD2:** A model that runs on CPU goes into both `models.json` and `models-cpu.json` with an identical entry; every slug in `models-cpu.json` has an equal entry in `models.json` today. A CUDA-only model goes into `models.json` only.
- **MOD3:** `max_concurrency` on an entry is an integer from 1 to 1024 (`load_registry()`); set it only when the backend can serve parallel requests, as the Nemotron entry does with 2. Relative paths in `sherpa_config` resolve under `/data/models/<slug>/` (`_sherpa_config()` in `src/talkies/models/__init__.py`).

## Executors and backends

- **MOD4:** A new executor needs, in the same change: its name in `VALID_EXECUTORS` (`src/talkies/config.py`), its own branch in `build_backends()` (`src/talkies/models/__init__.py`), and the executor list in `docs/models.md`. `build_backends()` ends with an unconditional `MultitaskBackend` fallback, so a missing branch silently builds a Canary backend instead of failing.
- **MOD5:** A backend takes `model_id`, `repo`, `model_path` and `device` (plus executor-specific arguments) and exposes the lifecycle surface of `BackendBase` in `src/talkies/models/base.py`: `loaded()`, `last_used_secs_ago()` measured with `time.monotonic()`, `async get_model()` that loads once under an `asyncio.Lock` with a re-check inside the lock, and `async unload()` that drops the model and frees its device memory. ASR backends return `TranscribeResult`; TTS backends return `SynthesisResult` (mono int16 PCM) and implement `voices()` and `default_voice()`. `src/talkies/models/whisper.py` is the reference shape.
- **MOD6:** Import heavy ML libraries (torch, numpy, NeMo, transformers, sherpa-onnx, vosk, chatterbox, ...) inside the methods that need them, never at module top: `server.py` imports every backend module at startup through `build_backends`, the CPU image lacks the CUDA-only packages, and the dev image has no ML stack. `sherpa.py`, `sherpa_offline_ctc.py` and `vosk.py` load theirs with `importlib`.
- **MOD7:** Backends load only from their flat snapshot directory `config.MODELS_DIR / <slug>` and never download at request time: the server runs with `HF_HUB_OFFLINE=1`, and `entrypoint.sh` fetched everything at boot (see the `src/talkies/models/kokoro.py` docstring). Repos a model needs besides its own go in the entry's `dependencies` list, which the entrypoint prefetches into the Hugging Face cache.

## Voices

- **MOD8:** Voice names are paths relative to the voice directory with `.wav` removed (`foo/bar/me.wav` is voice `foo/bar/me`), with optional `<name>.txt` transcript and `<name>.lang` siblings (`CUSTOM_VOICES_DIR` in `src/talkies/config.py`). Built-in Qwen3 voices live in `voices/qwen3/` and are copied into the CUDA image only; a custom voice with the same name wins. Each TTS backend owns its catalog, and `speech` validates `voice` against `backend.voices()` for the requested model; voices are never shared across models.

## Dependencies and images

- **MOD9:** Change light runtime dependencies only through `make pkg-add`, `pkg-remove`, `pkg-update` or `pkg-upgrade`, which move `exclude-newer` first; never hand-edit `uv.lock`. When a pin in `pyproject.toml` that `Dockerfile.dev` also installs changes (fastapi, httpx, uvicorn, pydantic, python-multipart), update `Dockerfile.dev` to the same version.
- **MOD10:** Change the heavy ML stack by editing `scripts/heavy-deps-cpu.in` or `scripts/heavy-deps-cuda.in`, then running `make compile-heavy`; never hand-edit `requirements-heavy-*.txt`. A wheel whose metadata cannot go through the resolver goes into its own hand-pinned file installed with `--no-deps --require-hashes`, as `requirements-chatterbox.txt` does; bump its version and hash together.
- **MOD11:** When an image dependency can break only at model load, make the build fail instead: `Dockerfile.cuda` imports Chatterbox and constructs `perth.PerthImplicitWatermarker` at build time because an incompatible setuptools otherwise surfaced only on the first synthesis request (v0.15.2).

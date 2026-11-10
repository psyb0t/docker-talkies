# Testing rules

Prefix: TST.

How to run each layer: the Dev workflow table in [AGENTS.md](../../AGENTS.md#dev-workflow) and [docs/development.md](../../docs/development.md).

## What every change needs

- **TST1:** `make check` (lint plus unit tests in the dev container) passes before a change is called done. CI does not run it (`.github/workflows/pipeline.yml` only builds images), so this is the only gate.
- **TST2:** Unit tests go in `tests/test_<module or backend>.py`. The suite has no pytest configuration file and no custom markers.

## Unit tests (`tests/`, `make test-unit`)

- **TST3:** Unit tests run offline in the dev image, which has no ML stack and no `mcp` package. Before importing a backend module, replace the `talkies.models` package with a bare module whose `__path__` points at `src/talkies/models`, so `__init__.py` and its eager backend imports are skipped (`tests/test_chatterbox.py`, `tests/test_parakeet_cpp_streaming.py`). Tests that import `talkies.server` also stub `talkies.mcp_server` (the `streaming_server` fixture in `tests/test_server_streaming.py`). Use `pytest.importorskip` for numpy, torch, transformers or sherpa-onnx.
- **TST4:** A test that runs a real model inside `tests/` skips when the library is missing or the snapshot is not already cached, and never downloads (`tests/test_wav2vec2_phoneme.py`, `tests/test_sherpa_offline_ctc.py`).
- **TST5:** Settings are tested by reloading `talkies.config` under a patched environment: clear the related variables with `monkeypatch`, set the ones under test, pop `talkies.config` from `sys.modules` and import it again (`_reload_config` in `tests/test_config.py`). A new setting gets cases for its default, its accepted values and its rejected values.
- **TST6:** Both shipped registries are under test: every declared executor must be in `VALID_EXECUTORS` and each file must load (`test_shipped_registry_executors_are_all_valid`, `test_shipped_registry_loads` in `tests/test_config.py`), and the built-in streaming entries' repos, revisions and download patterns are pinned in `tests/test_builtin_streaming_models.py`. A change to a pinned built-in entry updates that test in the same change.

## Integration tests (`tests/integration/`)

- **TST7:** Each `test_*.sh` and `e2e_*.sh` file sources `harness.sh`, declares the slugs it needs, starts its own `--rm` container on an ephemeral port, and tears it down on exit, so any file runs alone (`bash tests/integration/test_speech.sh`) or through `run.sh`. The harness defaults to the CUDA image with `--gpus all`; CPU tests set `HARNESS_IMAGE`, `HARNESS_DEVICE` and `HARNESS_USE_GPU` (`e2e_streaming_asr.sh`), and custom registries come in through `HARNESS_MODELS_FILE` (`e2e_streaming_custom_backends.sh` with `streaming-custom-models.json`). Model snapshots are cached in `.e2e-cache/` (`HARNESS_CACHE_DIR`).
- **TST8:** Integration tests build and run the real images and download model snapshots; none of them is part of `make check` or CI. When a change alters real inference, run the integration or streaming test that covers it.
- **TST9:** Only `tests/integration/.fixtures/audio.mp3` and its transcript `audio.mp3.txt` are tracked fixtures; anything else dropped into `.fixtures/` is gitignored (`.gitignore`). `e2e_qwen3_modes.sh` uses the pair as its voice-cloning reference, so keep the transcript matching the audio.

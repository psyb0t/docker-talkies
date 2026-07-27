# Changelog

All notable changes per release. Versions follow [semver](https://semver.org)
pre-1.0 conventions: minor bumps may include breaking changes (called out
explicitly with **Breaking.**), patch bumps are docs / build / fixes only.

## v0.11.7 — 2026-07-27

- Added self-hosted version and license badges plus a Docker Hub pulls badge; wired a badges job into pipeline.yml.

## v0.11.6 — 2026-07-26

Listed on the official MCP Registry — no behavior change.

- Added `server.json` — published to the official Model Context Protocol Registry (`registry.modelcontextprotocol.io`) as `io.github.psyb0t/talkies`, pointing at the `psyb0t/talkies` Docker image. Ownership is proven by an `io.modelcontextprotocol.server.name` LABEL on the image; publishing runs on tag pushes via GitHub OIDC (secretless). Also added a `glama.json` maintainer claim.

## v0.11.5 — 2026-07-26

Third-party license notices. Documentation only, no behavior change.

- Added `THIRD_PARTY.md` + `LICENSES/` documenting the GPL-3.0-or-later espeak-ng runtime dependency of the Kokoro TTS backend, and the three Qwen3-TTS reference voice samples under `voices/qwen3/` (MIT — byte-identical copies of faster-qwen3-tts's `ref_audio*.wav`). The project's own code stays WTFPL.

## v0.11.4 — 2026-07-26

Skill docs de-duplicated (round 2). Documentation only, no behavior change.

- The previous pass only consolidated the top Security bullets; the endpoint reference, tips, and examples still repeated the `DELETE /v1/files/{path}` and `DELETE /api/ps/{model_id}` endpoints ~10 times with "Destructive" labels. Reduced to one neutral endpoint-table row + one usage example per endpoint, dropped the redundant repeats and the "Destructive/irreversible" flagging. Same behavior, same endpoints, far less repetition.

## v0.11.3 — 2026-07-26

Skill docs de-duplicated. Documentation only, no behavior change.

- Consolidated the repeated file-staging and no-auth warnings in `.agents/skills/talkies/SKILL.md` into one clear note each; the external-transmission and voice-cloning notes stay in the Security & safety section.

## v0.11.2 — 2026-07-26

Skill security-documentation hardening. Docs only, no service or behavior change.

- Hardened the skill docs with explicit destructive-operation guardrails on `DELETE /v1/files/{path}` (shared, unisolated staging namespace — no undo, no per-caller ownership) and an auth/exfil warning summarizing that TTS `input` text and voice-cloning reference samples leave your host via `$TALKIES_URL`.
- Rolled both points, plus the existing voice-cloning consent note, into the top-level "Security & safety" summary so agents see them before reaching the detailed sections.

## v0.11.1 — 2026-07-25

Skill security hardening — clears the ClawHub SkillSpector DO_NOT_INSTALL rating. Docs only, no service change.

- Declared a `permissions:` capability block (network / shell / filesystem) in the skill frontmatter.
- Marked the destructive `DELETE /api/ps/{model_id}` model-eviction endpoint (+ `POST /unload`) and added an agent guardrail against unsolicited calls.
- Added `/v1/files` staging guardrails (only touch self-created paths; no enumerating/deleting other callers'; clean up), mirrored in `references/setup.md`.
- Fixed a logging-doc contradiction: DEBUG logs full request/response bodies (TTS input, ASR transcripts) while normal levels don't — never enable debug in production with real data.
- Added a voice-cloning acceptable-use / consent warning near the custom-voices section.

## v0.11.0 — 2026-07-25

ClawHub plugin + skill accuracy/security pass. No service change.

- **New `@psyb0t/talkies` code plugin** (`.agents/plugins/talkies/`) — a stdio↔HTTP MCP bridge (`mcp-remote`) to the box's `/v1/mcp` endpoint. MIT-licensed. CI publishes it alongside the skill via `clawhub-publish.yml`.
- **Skill hardening + accuracy** (verified against source): added a Security & safety capability declaration, a server-side URL-fetch warning, and staged-file persistence notes; corrected the ASR model count (7 on the CUDA/registry image), the Qwen3-TTS 24 kHz sample rate, the documented sampling params (`temperature`/`top_k`/`top_p`/`repetition_penalty`/`max_new_tokens`/`do_sample`), the Qwen3-TTS PCM streaming surface, and env vars `TALKIES_QWEN3_STREAM_CHUNK_SIZE` / `TALKIES_LOG_LEVEL`.

## v0.10.1 — 2026-07-24

Skill packaging + docs. No service change.

- **Skill published to ClawHub.** The `talkies` agent skill moves to the
  standard `.agents/skills/` layout; the CI pipeline gains a tag-gated
  `publish-skills-to-clawhub` job (runs after the image build + GitHub release).
- **Skill doc sync:** caught the skill up to the current model catalog — ASR
  6 → 8 backends (adds `nemotron-3.5-asr-0.6b`), TTS 2 → 7 slugs (adds
  `kokoro-82m-nvidia` and the `qwen3-tts-1.7b` / `*-custom` / `*-design`
  family) with a modes table and corrected per-slug languages + `instructions`
  semantics.
- Build: `scan_fail_build: false` (Grype findings go to the Security tab
  without failing the run).

## v0.10.0 — 2026-07-02

Configurable log level + opt-in full-request DEBUG logging, plus a repo-wide
lint/format pass.

- **New env var `TALKIES_LOG_LEVEL`** (falls back to `LOG_LEVEL`): `debug` /
  `info` / `warn` / `error` / `fatal` (case-insensitive; `warning` /
  `critical` also accepted). Unrecognized values fail fast at startup.
  Default `info`. Resolution lives in `src/talkies/logging.py`.
- **`debug` logs full request + response bodies** at the HTTP boundary as
  structured JSON — TTS `input` text / `instructions`, cloned-voice
  reference transcripts, and ASR transcripts. This is PII: a one-time
  `WARNING` fires at startup when `debug` is active. `info` and above log
  no body content. Wiring in `speech()` / `transcribe()` in
  `src/talkies/server.py`, gated on `log.isEnabledFor(logging.DEBUG)`.
- Unit tests for level resolution + the PII warning in
  `tests/test_logging.py` (wired into `make test-unit`).
- Integration harness (`tests/integration/harness.sh`) forwards
  `TALKIES_LOG_LEVEL` into the container so the DEBUG path is testable.
- Extracted the model-executor allowlist into a single `VALID_EXECUTORS`
  constant in `src/talkies/config.py` (was duplicated between the validator
  and its error message).
- Repo-wide `black` + `isort` format pass; added `.flake8` (line length 88
  to match `black`, `extend-ignore = E203,W503`) and a `[tool.mypy]`
  `ignore_missing_imports` section in `pyproject.toml` so `make lint`
  (flake8 + mypy) runs clean. No runtime behavior change from the format
  pass.

No API or wire-format change — every request shape from v0.9.0 works
identically.

## v0.9.0 — 2026-06-09

Nemotron-3.5-ASR via parakeet.cpp + GPU drain barrier + integration-harness
per-test filter.

- **New ASR slug `nemotron-3.5-asr-0.6b`** — NVIDIA Nemotron-3.5-ASR-Streaming-0.6B
  (OpenMDW-1.1, 40+ locales), served through [mudler/parakeet.cpp](https://github.com/mudler/parakeet.cpp)
  (C++17/ggml, WER-0 vs NeMo). CPU inference in both images. Per-word
  timestamps + confidence; Whisper-shape `segments` synthesized via
  silence-gap grouping so `verbose_json` matches the OpenAI shape. Register
  more parakeet.cpp GGUF checkpoints via a custom `models.json`.
- Fixed a GPU OOM race in `server.py`: sibling eviction now issues a CUDA
  `synchronize()` between unloading the old backend and loading the next, so
  a tight GPU can't race the still-freeing allocator pool.
- Integration harness per-test filter: positional args to any
  `e2e_*.sh` / `test_*.sh` act as an exact-or-substring whitelist over test
  functions, so a single failing case can be re-run without recycling the
  whole harness.
- `.dockerignore` additions cut the build-context transfer from ~24 GB to
  ~3 KB when the local test cache is warm.

Wire-compatible with v0.8.0.

## v0.8.0 — 2026-05-31

Qwen3-TTS CustomVoice + VoiceDesign + 1.7B Base + per-request sampling controls.

- **Four new TTS slugs**: `qwen3-tts-1.7b` (Base 1.7B cloning),
  `qwen3-tts-0.6b-custom` + `qwen3-tts-1.7b-custom` (9 preset speakers;
  1.7B adds emotion via `instructions`), `qwen3-tts-1.7b-design` (synthesize
  a voice from a natural-language description). Mode is implicit in the slug;
  `voice` / `instructions` semantics shift per mode. `GET /v1/audio/voices`
  returns the right catalog shape per slug.
- Per-request sampling controls on `POST /v1/audio/speech` as OpenAI extras
  (sent via `extra_body` on the official SDKs): `temperature`, `top_k`,
  `top_p`, `repetition_penalty`, `max_new_tokens`, `do_sample`, plus
  `language` for CustomVoice / VoiceDesign. Out-of-range → 422.
- Build fix: `--no-config` on the heavy `--require-hashes` install in both
  Dockerfiles (v0.7.1's hash-locked install failed once a transitive dep
  landed newer than the `[tool.uv] exclude-newer` gate).

Backwards compatible — new request fields are all optional.

## v0.7.1 — 2026-05-31

Supply-chain hardening — hash-locked requirements + `uv.lock`.

- Added `uv.lock` (frozen, hash-verified lightweight runtime deps) and
  `requirements-heavy-{cpu,cuda}.txt` (hash-locked full dep graphs, generated
  by `scripts/compile-heavy-deps.sh` via `uv pip compile --generate-hashes`).
- Dockerfiles install lightweight deps with `uv sync --frozen` and the heavy
  ML stack with `--require-hashes`, so every wheel's bytes are verified on
  each build (previously only version-pinned).
- `make compile-heavy` regenerates the hash files after editing
  `scripts/heavy-deps-*.in`.

No API, env-var, or behavior change — build layer only.

## v0.7.0 — 2026-05-31

Qwen3-TTS PCM streaming + `pkg-*` Makefile workflow.

- `response_format="pcm"` against a `qwen3_tts` model streams the raw PCM body
  via HTTP/1.1 chunked transfer-encoding instead of buffering the full
  utterance; first-audio latency drops from ~3-8 s to ~200-700 ms. New env
  var `TALKIES_QWEN3_STREAM_CHUNK_SIZE` (default 8). Other formats + Kokoro
  unchanged.
- **Breaking (narrow).** Callers that relied on `Content-Length` for the
  `qwen3_tts` + `response_format=pcm` case must adapt to a chunked body.
  Every other path is wire-compatible with v0.6.1.
- `make pkg-lock` / `pkg-add` / `pkg-update` / `pkg-upgrade` / `pkg-remove`
  bump the `[tool.uv] exclude-newer` age gate to the moment of the mutation.
- `.gitattributes` enforces LF on shell scripts.

## v0.6.2 — 2026-05-31

Supply-chain bump-on-mutation Makefile workflow. (Local-only tag — superseded
by the same workflow shipped in v0.7.0.)

- `make pkg-*` targets bump `[tool.uv] exclude-newer` before any `uv`
  operation. No runtime / API change.

## v0.6.1 — 2026-05-30

Fix Qwen3-TTS kwarg regression from v0.6.0.

- v0.6.0 called `generate_voice_clone(...)` with the wrong kwarg name, 500ing
  every Qwen3 synth request. Fixed `x_vector_only_mode=` → `xvec_only=` (the
  correct name in `faster_qwen3_tts==0.2.6`). Kokoro slugs were unaffected.
- Added tests guarding the `instructions` field, the x-vector fallback, and
  Kokoro compatibility.

## v0.6.0 — 2026-05-30

`kokoro-82m-nvidia` ONNX backend + Qwen3-TTS `instructions` wiring +
self-spawning integration test harness.

- **New TTS slug `kokoro-82m-nvidia`** (`nvidia/kokoro-82M-onnx-opt`,
  Apache-2.0): same Kokoro-82M weights / 40-voice catalog / wire shape as
  `kokoro-82m`, served via ONNXRuntime (no PyTorch on the inference path),
  G2P via espeak-ng.
- Qwen3-TTS honours the OpenAI `instructions` field; falls back to
  x-vector-only cloning when a voice has no reference transcript.
- Self-spawning CUDA integration harness under `tests/integration/`.

## v0.5.0 — 2026-05-28

Drop `distil-whisper-large-v3`.

- **Breaking.** Removed the English-only `distil-whisper-large-v3` slug —
  redundant next to multilingual `whisper-large-v3` and the 8×-faster
  `whisper-large-v3-turbo`. CUDA registry is now 6 ASR (whisper ×2,
  parakeet, canary ×3) + 2 TTS.

## v0.4.1 — 2026-05-28

README rewrite for above-the-fold conversion. Docs only — one-sentence
tagline + Python drop-in snippet in the first 25 lines, plus a small
`.gitignore` tweak. No behavior change.

## v0.4.0 — 2026-05-28

Qwen3-TTS voice cloning + custom voices.

- **New TTS slug `qwen3-tts-0.6b`** (CUDA-only), a second TTS engine alongside
  Kokoro, via `faster-qwen3-tts` 0.2.6 (bfloat16 + SDPA). Drop `.wav` samples
  into a `/data/custom-voices/` user-mount to clone voices.
- Renamed the local host cache dir `~/.talkies-models` → `~/.talkies-data`.

## v0.3.0 — 2026-05-28

Kokoro TTS.

- **New endpoint `POST /v1/audio/speech`** (OpenAI-compatible) with
  mp3 / opus / aac / flac / wav / pcm output, plus `GET /v1/audio/voices`
  discovery. `kokoro-82m` ships in both CPU and CUDA images.
- Backend protocol split into `BackendBase` / `ASRBackend` / `TTSBackend`;
  ASR and TTS share one VRAM pool with cross-modality eviction + idle-TTL
  sweeping.

## v0.2.1 — 2026-05-28

Agent skill scaffolding + credit. Docs only — adds `.agents/` skill files and
a Credits section. No runtime / API / wire-format change.

## v0.2.0 — 2026-05-28

MCP server, bearer auth, URL fetching, file staging.

- **New endpoint `/v1/mcp`** — MCP Streamable HTTP server (six tools: model
  discovery, transcription, file management).
- Optional bearer-token gating on every route via `TALKIES_AUTH_TOKEN`.
- `file_path` accepts `http(s)` URLs (size-capped, optional SSRF guard).
- **New `/v1/files`** staging API, shared with the MCP file tools.

## v0.1.0 — 2026-05-28

Initial release.

- OpenAI-compatible `POST /v1/audio/transcriptions` with seven backends
  (faster-whisper ×3, Parakeet-TDT, Canary multitask ×2, Canary-Qwen SALM),
  five response formats (json / text / verbose_json / srt / vtt), VAD-driven
  long-form chunking, stereo diarization, and Ollama/LiteLLM-compatible
  management endpoints. Ships as CPU and CUDA Docker images.

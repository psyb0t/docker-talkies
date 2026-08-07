# Changelog

All notable changes per release. Versions follow [semver](https://semver.org)
pre-1.0 conventions: minor bumps may include breaking changes (called out
explicitly with **Breaking.**), patch bumps are docs / build / fixes only.

## v0.15.2 — 2026-08-07

Fixes Chatterbox synthesis failing at model load in the CUDA image.

### Fixed

- **`chatterbox-turbo` raised `TypeError: 'NoneType' object is not callable`
  on its first synthesis request.** `resemble-perth` 1.0.1 imports
  `pkg_resources` at module load, and setuptools removed it in 81. Nothing
  pinned setuptools — it resolved transitively through torch, NeMo, spaCy,
  TensorBoard and CTranslate2 to 82.0.1, so `pkg_resources` was absent.
  Upstream catches that `ImportError` and sets `PerthImplicitWatermarker` to
  `None`; `chatterbox-tts` then calls that name unconditionally when
  constructing the model. So the install succeeded, every import succeeded,
  and only the first `POST /v1/audio/speech` failed. No `resemble-perth`
  release past 1.0.1 exists, so `scripts/heavy-deps-cuda.in` now constrains
  `setuptools<81`; the recompiled lock resolves 80.10.2 and moves no other
  package. Other CUDA models were unaffected.
- `Dockerfile.cuda` now asserts the Chatterbox runtime wiring at build time —
  it imports `perth`, imports `ChatterboxTurboTTS`, and constructs
  `PerthImplicitWatermarker()`. A dependency set that leaves the watermarker
  `None` fails the image build instead of a user request. The check runs in
  the builder stage, so it adds nothing to the runtime image.

## v0.15.1 — 2026-08-07

Fixes a startup failure in the v0.15.0 CUDA image.

### Fixed

- **The CUDA image could not start.** `models.json` declares
  `"executor": "chatterbox"` for `chatterbox-turbo`, but `VALID_EXECUTORS` in
  `src/talkies/config.py` never gained the matching entry, so `load_registry()`
  rejected the file. Because `src/talkies/server.py` calls `load_registry()` at
  module scope, the `ValueError` fired during import and the process exited
  before binding a port — taking all twenty models down, not just the new one.
  The CPU image was unaffected: its registry does not list `chatterbox-turbo`,
  so validation never saw the unknown executor.
- Added a shipped-registry contract test over both `models.json` and
  `models-cpu.json` asserting that every declared executor exists in
  `VALID_EXECUTORS` and that each registry loads. The other tests in
  `tests/test_config.py` build synthetic fixtures, so nothing exercised the
  registries that actually ship in the images — which is why a green test
  suite and a successful image build both passed over a server that could not
  boot.

## v0.15.0 — 2026-08-07

Added Chatterbox Turbo, an expressive English TTS model with inline
paralinguistic tags and transcript-free voice cloning, to the CUDA registry.

- Added the `chatterbox` executor and the `chatterbox-turbo` slug
  (`ResembleAI/chatterbox-turbo`, MIT weights, ungated). It emits 24 kHz mono
  audio through `POST /v1/audio/speech` and is CUDA-only.
- Emotion and non-verbal sounds are written inline in `input` as bracketed
  tags. The model's tokenizer defines exactly 19 of them, including `[sigh]`,
  `[whispering]`, `[sarcastic]`, `[dramatic]`, `[laugh]` and `[gasp]`; the full
  list is in `docs/models.md`.
- Voice selection is either `builtin`, the speaker shipped inside the
  checkpoint, or a `.wav` under `/data/custom-voices`. Unlike the Qwen3
  backend no reference transcript is required, but the clip must be longer
  than five seconds — shorter clips are rejected with a 400 rather than
  reaching the upstream assertion.
- Registry-owned `download_patterns` fetch only the files the backend loads,
  skipping a 1 GB tensor file the model never reads.
- `chatterbox-tts` and `s3tokenizer` install from a separate hash-pinned
  `requirements-chatterbox.txt` with `--no-deps`. Their declared dependency
  metadata is either unsatisfiable against this image's pinned torch and
  transformers, or pulls development tooling that would otherwise ship in the
  runtime image; both wheels are pure Python and their real imports are
  already satisfied.
- Note: every waveform this model produces carries a neural watermark applied
  unconditionally by the upstream package, which exposes no option to disable
  it. See `THIRD_PARTY.md`.
- Synced README, model, and third-party documentation; added unit coverage for
  the voice catalog, the reference-clip bounds, and the traversal guard.

## v0.14.1 — 2026-08-06

Agent integrations now describe and install the released Talkies surfaces
without overstating MCP capabilities or omitting CUDA behavior.

### Fixed

- Added copy-pasteable Claude Code, Codex, and OpenClaw installation commands
  to the root README.
- Corrected Claude Code and Codex plugin descriptions to distinguish HTTP TTS
  from the six ASR/file-staging tools exposed through MCP.
- Corrected the Talkies skill permission schema and documented Nemotron's
  parakeet.cpp CUDA path alongside CPU inference.
- Made the OpenClaw bridge entry point executable and normalized the bundled
  bulk-transcription script to the repository's shell format.

## v0.14.0 — 2026-08-05

Model-aware concurrency and native CUDA lifecycle controls make parallel speech
requests predictable across every inference surface.

### Added

- Per-model concurrency now covers HTTP transcription, MCP transcription,
  WebSocket ASR, buffered TTS, and streaming TTS through one admission
  controller. `TALKIES_MODEL_MAX_CONCURRENCY` sets the fallback limit,
  `TALKIES_MODEL_CONCURRENCY` applies per-slug overrides, and registry entries
  can define `max_concurrency`; malformed, duplicate, disabled, unknown, and
  out-of-range values fail at startup.
- `GET /v1/models` reports `max_concurrency`; `GET /api/ps` reports both
  `active_requests` and `max_concurrency`. Capacity exhaustion returns 429,
  while attempts to switch models during active inference return 409.
- The bundled Nemotron model admits two concurrent requests in both CPU and
  CUDA registries. Real WebSocket coverage verifies two streams are admitted
  together and finish independently.

### Changed

- The CUDA image now installs the upstream parakeet.cpp v0.5.0 CUDA 12 binary
  bundle by pinned SHA-256 instead of compiling a CPU-only library in a CUDA
  image. Its bundled CUDA libraries remain isolated from the Python ML stack.
- Every model-unload path now releases native model contexts, collects Python
  references, and clears already-initialized Torch CUDA allocator and IPC
  caches without creating a CUDA context solely for cleanup.

## v0.13.3 — 2026-08-01

### Fixed

- **Sherpa-ONNX returned no word timestamps at all.** `OnlineRecognizer.get_result()`
  returns `result.text.strip()` — a plain `str` — so the word extraction in
  `src/talkies/models/sherpa.py` read `tokens` and `timestamps` off a string and
  always came back empty. Every Sherpa model silently produced `"words": []` on
  `POST /v1/audio/transcriptions` and over the streaming WebSocket, whatever the
  caller requested. The adapter now reads the full `OnlineRecognizerResult` via
  `get_result_all()`, falling back to the string form on wrapper builds without it.
- **Sherpa word entries were subword fragments, not words.** Transducer tokens are
  BPE pieces — `"QUICK"` arrives as `("QUI", "CK")` — and each token was emitted as
  its own word. Tokens are now grouped back into words on the leading-space marker
  that denotes a word start. Vocabularies without that marker (char-level and
  word-level) are detected and left one-token-per-word, since joining on an absent
  boundary would collapse an utterance into a single word.
- **Sherpa now reports per-word `confidence`**, derived from the model's per-token
  acoustic log-probabilities (`ys_probs`) and averaged over each word's tokens.
  Same field name and same 0–1 range the Vosk backend already emits, so both
  backends return the same word shape.
- **File transcription through a Sherpa model duplicated text.** The batch file
  route opens its stream with `interim_results=False`, which the Vosk backend
  honours and the Sherpa backend ignored. `get_result` is cumulative within an
  utterance, so every partial repeated the whole prefix and the batch adapter
  concatenated each revision — a nine-word clip came back as `"THE QUICK THE QUICK
  BROWN FOX … THE QUICK BROWN FOX JUMPS OVER THE LAZY DO"`. Sherpa now respects the
  flag. Live streaming is unaffected: cumulative partials are the point there, and
  callers asking for interim results still receive them.
- Sherpa transcript events now use the shared `EVENT_PARTIAL` / `EVENT_ENDPOINT` /
  `EVENT_FINAL` constants rather than bare string literals, matching the Vosk backend.

### Changed

- CI: the repository is push-mirrored to GitLab and Codeberg on every branch and
  tag, and archived to the Wayback Machine (through the authenticated Save Page Now
  API) and Software Heritage. Mirror and archive live in
  `.github/workflows/mirror-and-archive.yml` beside the pipeline rather than inside
  it, because the pipeline is tag-only while the mirror needs every push.
- CI: `.github/workflows/issue-pull.yml` pulls issues opened on the Codeberg and
  GitLab mirrors back into GitHub. Only the scheduled run is jittered; a manual
  dispatch runs immediately.

## v0.13.2 — 2026-07-31

### Fixed

- Every caller now references the shared reusable workflows at `@master` instead
  of a commit SHA. The pin held this repo on a revision from months earlier, and
  two fixes it needed were already sitting on master with no way to reach it: the
  badge job racing itself between the branch and tag runs of the same release
  (`cannot lock ref 'refs/heads/badges'`), and — more quietly — `release-multi`
  being skipped outright, so tags from `v0.11.0` onward built and pushed images,
  passed their scans, went green, and never created a GitHub Release. Releases
  stop at `v0.10.0` while tags run to `v0.13.1` for exactly that reason.
- Third-party actions are unaffected and still pin by full commit SHA; the
  force-push threat that justifies pinning does not apply to a repo we own.

## v0.13.1 — 2026-07-31

Documentation accuracy and install guidance corrections. No service behavior
changed.

- Corrected CPU/CUDA image defaults, model-registry paths, and the CUDA
  no-GPU deployment guidance in the agent setup reference.
- Repaired configuration-table rendering, clarified the staged-file path
  boundary, and corrected the Qwen3-TTS model-mode matrix.
- Removed an unsupported bulk-transcription helper reference and aligned the
  documented load-timeout behavior with the current server.

## v0.13.0 — 2026-07-30

Bundled selectable Sherpa-ONNX and Vosk ASR models with OpenAI-compatible file transcription.

- Added four English Sherpa-ONNX Zipformer variants and Vosk small English to
  both bundled registries. Each supports native live ASR and
  `POST /v1/audio/transcriptions`; the file route feeds normalized audio through
  a short-lived native stream.
- Added registry-owned `download_patterns`, so selecting a Sherpa variant
  downloads only its matching tokens, encoder, decoder, and joiner artifacts.
- Updated the CUDA image to install a hash-verified upstream Sherpa CUDA wheel,
  enabling its native CUDA execution provider rather than a CPU fallback.
- Added real-audio CPU and CUDA end-to-end coverage for native WebSocket and
  OpenAI-compatible HTTP transcription, plus unit and route-level coverage for
  the new file adapter and bundled registry entries.
- Synced model, agent, third-party, OpenAPI-version, and local versioned-image
  documentation and metadata.

## v0.12.1 — 2026-07-30

Documentation hierarchy refresh. No service behavior change.

- Replaced the long root README with a short start page and detailed guides
  under `docs/` for startup, architecture, models, API, streaming,
  configuration, operations, and development.
- Moved live streaming documentation to `docs/streaming.md` and updated linked
  agent and third-party references.
- Corrected the configuration reference to state that `TALKIES_LOAD_TIMEOUT`
  is parsed but not currently enforced by the server.

## v0.12.0 — 2026-07-29

Live streaming ASR, new optional streaming runtimes, and CUDA dependency compatibility.

- Added live ASR at `WS /v1/audio/transcriptions/stream`: strict JSON start and
  control messages with a 4096-byte cap and duplicate-key rejection, bounded
  binary PCM16LE input and one-message transport queue, revisioned
  `partial`/`endpoint`/`final` transcripts, terminal stats, explicit cancel,
  idle/duration/connection limits, and WebSocket-aware bearer-token rejection.
- Added native streaming sessions for parakeet.cpp ABI v4, Sherpa-ONNX, and
  Vosk. Every `whisper` executor also exposes bounded rolling-window
  pseudo-streaming while preserving its existing file-transcription behavior.
- Active WebSockets pin their model: sibling model switches and explicit unload
  return conflicts, and the idle sweeper skips active stream models. Multiple
  streams may share the pinned model up to `TALKIES_STREAM_MAX_CONNECTIONS`.
- Added `sherpa` and `vosk` registry executors plus pinned
  `sherpa-onnx==1.13.4`, `sherpa-onnx-core==1.13.4`, and `vosk==0.3.45`
  runtimes. The native Sherpa companion is explicit because the Python wheel
  metadata does not install it. The bundled registries do not enable model
  slugs for these executors; custom registries supply compatible snapshots.
- Added streaming protocol, client, configuration, backend, cancellation, and
  custom-registry documentation in `README.md` and `streaming.md`.
- Fixed the CUDA dependency lock to use matching `onnxruntime` and
  `onnxruntime-gpu` 1.21.0 distributions.
- Pinned all reusable GitHub Actions workflow callers to an immutable commit.

## v0.11.10 — 2026-07-27

README fix. Documentation only, no behavior change.

- The `## Agent integrations` section's Codex subsection listed `codex plugin marketplace add psyb0t/agents` but never the actual install command — added `codex plugin add talkies@psyb0t` right after it.
- Clarified that the invocation form depends on how the skill got there: installed via the marketplace it's invoked as `$talkies:talkies`, while Codex's automatic pickup from a repo's own `.agents/skills/` (no install required) invokes it as plain `$talkies`.

## v0.11.9 — 2026-07-27

Agent-integration manifests. Documentation only, no behavior change.

- Added `.agents/.claude-plugin/plugin.json` and `.agents/.codex-plugin/plugin.json` so the existing `.agents/skills/talkies` skill installs natively as a plugin in Claude Code and Codex, pointed at the shared `psyb0t/agents` marketplace.
- Added a `## Agent integrations` section to the README with copy-pasteable install commands for Claude Code, Codex, the OpenClaw skill, and the OpenClaw MCP-bridge plugin.

## v0.11.8 — 2026-07-27

- Added a GitHub Actions CI status badge to the README.

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

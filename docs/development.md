---
title: "Development"
description: "Work on Talkies: Make targets in the dev container, unit and integration tests, image builds, locked dependencies and the docs build."
nav_order: 10
---

# Development

The [Makefile](https://github.com/psyb0t/docker-talkies/blob/main/Makefile)
runs normal developer workflows in containers. The development image
([`Dockerfile.dev`](https://github.com/psyb0t/docker-talkies/blob/main/Dockerfile.dev))
contains formatting, type-checking, and test tooling but not heavyweight ML
runtime dependencies, so unit tests stub the ML backends.

| Command | Purpose |
|---|---|
| `make help` | List targets |
| `make dev-image` | Build the development image; the dev targets depend on it |
| `make shell` | Open a shell in the development container |
| `make check` | Lint and unit tests in the development image |
| `make lint` | Run flake8 and mypy on `src` |
| `make test-unit` | Run the offline unit tests (`make test` is the same) |
| `make format` | Run isort and Black on `src` |
| `make build` / `make build-cuda` | Build CPU or CUDA production image |
| `make build-all` | Build both images |
| `make run` / `make run-cuda` | Build and run an image on `PORT` (default 8000) with `~/.talkies-data` as `/data` |
| `make test-streaming` | Real CPU native WebSocket ASR E2E |
| `make test-streaming-custom` | Real CPU Sherpa-ONNX/Vosk WebSocket and HTTP E2E |
| `make test-streaming-custom-cuda` | Real CUDA Sherpa WebSocket and HTTP E2E |
| `make test-integration` | CUDA integration suite on a GPU-capable host |
| `make compile-heavy` | Regenerate the hash-locked ML requirements |
| `make pkg-add PKG=...`, `pkg-remove`, `pkg-update`, `pkg-upgrade`, `pkg-lock` | Change light dependencies through uv |
| `make version` | Print the release tag from `pyproject.toml` |
| `make docs` | Build this documentation site into `.testing/docs-site` |
| `make clean` | Remove local images and Python build and cache files |

`make run` and `make run-cuda` publish `PORT` on every host interface and do
not set `TALKIES_ENABLED_MODELS`, so the first run downloads the whole bundled
registry. Use the `docker run` commands in [Getting started](getting-started.md)
for a loopback-only, restricted start.

The two images get parakeet.cpp differently. The CPU image compiles it from a
pinned upstream commit in a build stage of `Dockerfile`. The CUDA image
installs the upstream parakeet.cpp v0.5.0 precompiled CUDA 12 shared-library
bundle instead: `Dockerfile.cuda` pins both the release URL and its published
SHA-256 digest, verifies the archive before extraction, and keeps the bundle's
CUDA 12.9 runtime libraries isolated under `/opt/parakeet`, so it has no
parakeet.cpp compiler stage.

The streaming and integration tests run host-side because they start
production images and connect to their HTTP/WebSocket port. The custom suite
uses the pinned registry in
[`tests/integration/`](https://github.com/psyb0t/docker-talkies/tree/main/tests/integration).

Lightweight dependencies are locked in `uv.lock`. CPU and CUDA ML dependencies
are hash-locked in `requirements-heavy-cpu.txt` and
`requirements-heavy-cuda.txt`; use `make compile-heavy` to regenerate them.
The `pkg-*` targets that change dependencies first move the uv
`exclude-newer` cutoff to UTC midnight seven days ago.

## Documentation site

Keep the root README short and place detailed user documentation in `docs/`.
GitHub Pages builds `docs/` from the `main` branch with Jekyll and the Just the
Docs theme. Every page needs front matter with a `title`, a `description` of
50 to 170 characters, and a `nav_order`. Link pages to each other with
relative `.md` paths, and link any file outside `docs/` with its full
`https://github.com/psyb0t/docker-talkies/blob/main/<path>` URL, because
relative links out of `docs/` break on the site.

`make docs` builds the site with the same Jekyll image GitHub Pages uses and
writes it to `.testing/docs-site`, which Git ignores.

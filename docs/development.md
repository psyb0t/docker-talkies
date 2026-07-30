# Development

The Makefile runs normal developer workflows in containers. The development
image contains formatting, type-checking, and test tooling but not heavyweight
ML runtime dependencies, so unit tests stub the ML backends.

| Command | Purpose |
|---|---|
| `make help` | List targets |
| `make check` | Lint and unit tests in the development image |
| `make format` | Run isort and Black on `src` |
| `make build` / `make build-cuda` | Build CPU or CUDA production image |
| `make build-all` | Build both images |
| `make test-streaming` | Real CPU native WebSocket ASR E2E |
| `make test-streaming-custom` | Real Sherpa-ONNX and Vosk WebSocket E2E |
| `make test-integration` | CUDA integration suite on a GPU-capable host |

The two streaming tests run host-side because they start production images and
connect to their HTTP/WebSocket port. The custom suite uses the pinned registry
in `tests/integration/`.

Lightweight dependencies are locked in `uv.lock`. CPU and CUDA ML dependencies
are hash-locked in `requirements-heavy-cpu.txt` and
`requirements-heavy-cuda.txt`; use `make compile-heavy` to regenerate them.
Keep the root README short and place detailed user documentation in `docs/`.

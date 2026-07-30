# Third-party components

`talkies` itself is WTFPL (see [LICENSE](LICENSE)). This file lists third-party
components that end up **inside the published Docker image** — not dev-only
tooling, not stuff you download yourself.

| Component | Kind | SPDX license | Source | Where it lives | Note |
|---|---|---|---|---|---|
| espeak-ng | vendored-source (apt package) | GPL-3.0-or-later | https://github.com/espeak-ng/espeak-ng | `apt-get install espeak-ng` in `Dockerfile` / `Dockerfile.cuda`, loaded at runtime by `kokoro` → `misaki` → `phonemizer-fork` | Transitive runtime dep of the Kokoro TTS backend in the published image (G2P for `kokoro-82m` / `kokoro-82m-nvidia`). GPL-3.0-or-later text: [LICENSES/GPL-3.0.txt](LICENSES/GPL-3.0.txt). |
| sherpa-onnx 1.13.4 + sherpa-onnx-core 1.13.4 | pinned Python package + native companion | Apache-2.0 | https://github.com/k2-fsa/sherpa-onnx | both installed from `uv.lock` into both images by `uv sync --frozen --no-dev` | Runtime for custom model-registry entries with `executor: "sherpa"`. `sherpa-onnx-core` is pinned explicitly because the `sherpa-onnx` wheel metadata does not pull in its required native companion. The bundled registries do not enable a Sherpa model. Package metadata and the upstream `LICENSE` identify both packages as Apache-2.0. |
| vosk 0.3.45 | pinned Python/native runtime | Apache-2.0 | https://github.com/alphacep/vosk-api | installed from `uv.lock` into both images by `uv sync --frozen --no-dev` | Runtime for custom model-registry entries with `executor: "vosk"`. The bundled registries do not enable a Vosk model. License verified against upstream `COPYING` and Python package metadata. Model snapshots may have separate licenses. |
| `voices/qwen3/{alloy,echo,fable}.wav` | tracked reference audio | MIT | https://github.com/andimarafioti/faster-qwen3-tts | tracked in git; `COPY voices/qwen3/ /opt/talkies/qwen3-voices/` in `Dockerfile.cuda` (baked into the CUDA image) | The three builtin Qwen3-TTS reference voice samples. **Byte-identical copies** of faster-qwen3-tts's `ref_audio.wav` / `ref_audio_2.wav` / `ref_audio_3.wav` (verified by sha256), renamed to the OpenAI-compat slugs. MIT text: [LICENSES/faster-qwen3-tts-MIT.txt](LICENSES/faster-qwen3-tts-MIT.txt). |

The rest of the stack pulled into the image is permissive:

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) and the `kokoro` / `misaki` PyPI packages — Apache-2.0.
- [faster-qwen3-tts](https://pypi.org/project/faster-qwen3-tts/) (Qwen3-TTS backend) — MIT.
- [NeMo](https://github.com/NVIDIA/NeMo) (`nemo_toolkit[asr]`, ASR backends) — Apache-2.0.

Model weight licenses (CC-BY-4.0, Apache-2.0, etc.) are documented per-model in the
[model registry documentation](docs/models.md) — those are
downloaded at runtime into `/data/models/`, not distributed inside the image
itself.

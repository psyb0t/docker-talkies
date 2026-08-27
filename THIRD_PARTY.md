# Third-party components

`talkies` itself is WTFPL (see [LICENSE](LICENSE)). This file lists third-party
components that end up **inside the published Docker image** — not dev-only
tooling, not stuff you download yourself.

| Component | Kind | SPDX license | Source | Where it lives | Note |
|---|---|---|---|---|---|
| espeak-ng | vendored-source (apt package) | GPL-3.0-or-later | https://github.com/espeak-ng/espeak-ng | `apt-get install espeak-ng` in `Dockerfile` / `Dockerfile.cuda`, loaded at runtime by `kokoro` → `misaki` → `phonemizer-fork` | Transitive runtime dep of the Kokoro TTS backend in the published image (G2P for `kokoro-82m` / `kokoro-82m-nvidia`). GPL-3.0-or-later text: [LICENSES/GPL-3.0.txt](LICENSES/GPL-3.0.txt). |
| parakeet.cpp 0.5.0 CUDA 12 shared library | pinned precompiled native runtime | MIT | https://github.com/mudler/parakeet.cpp | hash-verified release bundle copied to `/opt/parakeet` by `Dockerfile.cuda` | Runtime for the CUDA image's `parakeet_cpp` executor, including Nemotron-3.5-ASR. The upstream archive includes its license, C API header, and matching CUDA 12.9 runtime libraries. |
| sherpa-onnx 1.13.4 + sherpa-onnx-core 1.13.4 | pinned Python package + native companion | Apache-2.0 | https://github.com/k2-fsa/sherpa-onnx | CPU image installs both from `uv.lock`; CUDA image replaces `sherpa-onnx` with the hash-verified upstream CUDA wheel in `Dockerfile.cuda` | Runtime for the four bundled Sherpa Zipformer ASR slugs and custom `executor: "sherpa"` entries, and the offline `executor: "sherpa_offline_ctc"` phoneme slug (`zipa-ipa`). `sherpa-onnx-core` is explicit because wheel metadata does not install the required native companion. Package metadata and upstream `LICENSE` identify both packages as Apache-2.0. Model snapshots are downloaded at runtime under their own terms. |
| vosk 0.3.45 | pinned Python/native runtime | Apache-2.0 | https://github.com/alphacep/vosk-api | installed from `uv.lock` into both images by `uv sync --frozen --no-dev` | Runtime for the bundled `vosk-small-en-us-0.15` ASR slug and custom `executor: "vosk"` entries. License verified against upstream `COPYING` and Python package metadata. Model snapshots are downloaded at runtime under their own terms. |
| `voices/qwen3/{alloy,echo,fable}.wav` | tracked reference audio | MIT | https://github.com/andimarafioti/faster-qwen3-tts | tracked in git; `COPY voices/qwen3/ /opt/talkies/qwen3-voices/` in `Dockerfile.cuda` (baked into the CUDA image) | The three builtin Qwen3-TTS reference voice samples. **Byte-identical copies** of faster-qwen3-tts's `ref_audio.wav` / `ref_audio_2.wav` / `ref_audio_3.wav` (verified by sha256), renamed to the OpenAI-compat slugs. MIT text: [LICENSES/faster-qwen3-tts-MIT.txt](LICENSES/faster-qwen3-tts-MIT.txt). |
| chatterbox-tts 0.1.7 | pinned Python package | MIT | https://github.com/resemble-ai/chatterbox | CUDA image only; hash-pinned in `requirements-chatterbox.txt`, installed `--no-deps` in `Dockerfile.cuda` | Runtime for the `chatterbox-turbo` TTS slug. Kept out of `requirements-heavy-cuda.txt` because its declared pins (`torch==2.6.0`, `transformers==5.2.0`) are unsatisfiable against this image and it pulls Gradio. **Applies Resemble AI's PerTh neural watermark to every waveform it generates.** Upstream offers no switch, so the backend swaps in a passthrough watermarker when `TALKIES_CHATTERBOX_WATERMARK=false`. It defaults to true. License verified against the upstream `LICENSE` and the PyPI wheel metadata. |
| s3tokenizer 0.3.0 | pinned Python package | Apache-2.0 | https://github.com/xingchensong/S3Tokenizer | CUDA image only; hash-pinned alongside chatterbox-tts | Speech tokenizer required by chatterbox. Kept out of the shared resolver because it declares `pre-commit` as a runtime dependency, which would drag virtualenv/distlib/nodeenv into the image. |

The rest of the stack pulled into the image is permissive:

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) and the `kokoro` / `misaki` PyPI packages — Apache-2.0.
- [faster-qwen3-tts](https://pypi.org/project/faster-qwen3-tts/) (Qwen3-TTS backend) — MIT.
- [NeMo](https://github.com/NVIDIA/NeMo) (`nemo_toolkit[asr]`, ASR backends) — Apache-2.0.
- [diffusers](https://github.com/huggingface/diffusers), [einops](https://github.com/arogozhnikov/einops),
  [conformer](https://github.com/lucidrains/conformer) and
  [resemble-perth](https://github.com/resemble-ai/Perth) (Chatterbox runtime, CUDA image) — Apache-2.0 / MIT.
- [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm) (Chatterbox loudness normalisation) — MIT.

Model weight licenses (CC-BY-4.0, Apache-2.0, etc.) are documented per-model in the
[model registry documentation](docs/models.md) — those are
downloaded at runtime into `/data/models/`, not distributed inside the image
itself.

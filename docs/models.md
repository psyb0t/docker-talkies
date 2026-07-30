# Models and registries

The CPU image copies `models-cpu.json` to `/app/models.json`; the CUDA image
copies `models.json`. `TALKIES_ENABLED_MODELS` is a comma-separated allowlist
that limits both boot-time downloads and the model surface exposed by the API.

## Bundled ASR models

| Slug | Registry repository | Executor | CPU | CUDA | Live ASR |
|---|---|---|---:|---:|---|
| `whisper-large-v3` | `Systran/faster-whisper-large-v3` | `whisper` | yes | yes | bounded rolling decoder |
| `whisper-large-v3-turbo` | `deepdml/faster-whisper-large-v3-turbo-ct2` | `whisper` | yes | yes | bounded rolling decoder |
| `canary-180m-flash` | `nvidia/canary-180m-flash` | `canary_multitask` | yes | yes | no |
| `nemotron-3.5-asr-0.6b` | `mudler/parakeet-cpp-gguf` | `parakeet_cpp` | yes | yes | native |
| `parakeet-tdt-0.6b-v3` | `nvidia/parakeet-tdt-0.6b-v3` | `parakeet` | no | yes | no |
| `canary-1b-flash` | `nvidia/canary-1b-flash` | `canary_multitask` | no | yes | no |
| `canary-qwen-2.5b` | `nvidia/canary-qwen-2.5b` | `canary_salm` | no | yes | no |

All ASR slugs use `POST /v1/audio/transcriptions`. A registry may provide the
default source language, target language, and task; request `language` wins
over the source-language default. The bundled Nemotron entry uses the
`nemotron-3.5-asr-streaming-0.6b-q8_0.gguf` file and defaults its source
language to `auto`.

The registry declares English for Canary-180M and Parakeet-TDT, English/German/
French/Spanish for Canary-1B, English for Canary-Qwen, and `auto` plus 23 named
language codes for Nemotron. Whisper entries do not pin a registry language.
`canary-qwen-2.5b` also declares `Qwen/Qwen3-1.7B` as a dependent snapshot.

## Bundled TTS models

| Slug | Registry repository | Mode | CPU | CUDA | Default voice |
|---|---|---|---:|---:|---|
| `kokoro-82m` | `hexgrad/Kokoro-82M` | `kokoro` | yes | yes | `af_heart` |
| `kokoro-82m-nvidia` | `nvidia/kokoro-82M-onnx-opt` | `kokoro_nvidia` | yes | yes | `af_heart` |
| `qwen3-tts-0.6b` | `Qwen/Qwen3-TTS-12Hz-0.6B-Base` | `base` | no | yes | `alloy` |
| `qwen3-tts-1.7b` | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | `base` | no | yes | `alloy` |
| `qwen3-tts-0.6b-custom` | `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` | `custom_voice` | no | yes | `Vivian` |
| `qwen3-tts-1.7b-custom` | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` | no | yes | `Vivian` |
| `qwen3-tts-1.7b-design` | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` | `voice_design` | no | yes | `design` |

`GET /v1/audio/voices` is the source of truth for a model's valid voices.
Qwen3 `base` voices are `.wav` files below `/opt/talkies/qwen3-voices` or
`/data/custom-voices`, named without the extension. A sibling `.txt` is its
reference transcript and a sibling `.lang` changes its language label. Custom
voices shadow built-ins of the same name.

For `custom_voice`, `voice` is a built-in speaker name. The 1.7B model accepts
`instructions` as an emotion/style prompt; the 0.6B checkpoint ignores it.
For `voice_design`, the valid voice is `design` and `instructions` must be a
non-empty voice description. Qwen3 produces 24 kHz mono PCM and can stream it
with `response_format="pcm"`; see [Speech](api.md#speech).

## Use a custom registry

```bash
docker run --rm -it \
  -p 127.0.0.1:8000:8000 \
  -v "$PWD/talkies-data:/data" \
  -v "$PWD/my-models.json:/app/my-models.json:ro" \
  -e TALKIES_MODELS_FILE=/app/my-models.json \
  -e TALKIES_ENABLED_MODELS=my-asr \
  psyb0t/talkies:latest
```

Every entry needs a `repo`. `executor` defaults to `whisper` and must be one
of `whisper`, `parakeet`, `parakeet_cpp`, `canary_multitask`, `canary_salm`,
`kokoro`, `kokoro_nvidia`, `qwen3_tts`, `sherpa`, or `vosk`. The entrypoint
downloads a selected repository into `/data/models/<slug>`. `revision`, when
set, is supplied to Hugging Face snapshot download; pin it to an immutable
commit for reproducible contents. See [Streaming](streaming.md#custom-sherpa-onnx-and-vosk-registries)
for the required native-streaming fields.

The authoritative bundled entries are
[`models-cpu.json`](../models-cpu.json) and [`models.json`](../models.json).

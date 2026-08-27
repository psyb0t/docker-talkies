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
| `sherpa-zipformer-en-left-64` | `csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26` | `sherpa` | yes | yes | native |
| `sherpa-zipformer-en-left-128` | `csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26` | `sherpa` | yes | yes | native |
| `sherpa-zipformer-en-int8-left-64` | `csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26` | `sherpa` | yes | yes | native |
| `sherpa-zipformer-en-int8-left-128` | `csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26` | `sherpa` | yes | yes | native |
| `vosk-small-en-us-0.15` | `aglaia-models/vosk-model-small-en-us-0.15` | `vosk` | yes | yes | native (CPU decoder) |
| `wav2vec2-xlsr-53-espeak` | `facebook/wav2vec2-xlsr-53-espeak-cv-ft` | `wav2vec2_phoneme` | yes | yes | no |
| `zipa-ipa` | `anyspeech/zipa-small-crctc-500k` | `sherpa_offline_ctc` | yes | yes | no |

All ASR slugs use `POST /v1/audio/transcriptions`. A registry may provide the
default source language, target language, and task; request `language` wins
over the source-language default. The bundled Nemotron entry uses the
`nemotron-3.5-asr-streaming-0.6b-q8_0.gguf` file and defaults its source
language to `auto`. Its bundled `max_concurrency` is two in both registries.
The CPU image executes its parakeet.cpp backend on CPU. The CUDA image installs
the SHA-256-pinned upstream v0.5.0 CUDA 12 binary bundle for GPU offload and
keeps the bundle's CUDA libraries isolated from the Python ML stack.

The registry declares English for Canary-180M and Parakeet-TDT, English/German/
French/Spanish for Canary-1B, English for Canary-Qwen, and `auto` plus 23 named
language codes for Nemotron. Whisper entries do not pin a registry language.
`canary-qwen-2.5b` also declares `Qwen/Qwen3-1.7B` as a dependent snapshot.

### Sherpa-ONNX and Vosk choices

The four Sherpa slugs are English Zipformer transducer variants. `left-64` and
`left-128` select the model's left-context configuration; the `int8` choices
use quantized weights. Select exactly the trade-off you want with
`TALKIES_ENABLED_MODELS`; the entrypoint downloads only that slug's tokens and
matching encoder, decoder, and joiner files, not all four variants. The FP32
model files are substantially larger than their INT8 counterparts.

Sherpa uses CPU in the CPU image and its native CUDA provider in the CUDA image
when Talkies runs with `--gpus all`. `vosk-small-en-us-0.15` is English-only
and always decodes on CPU, including in the CUDA image. All five models support
the live WebSocket API and the OpenAI-compatible file-transcription API. See
[Streaming](streaming.md#sherpa-onnx-and-vosk) for protocol and file-route
details.

### Phoneme recognition

`wav2vec2-xlsr-53-espeak` and `zipa-ipa` recognize phones rather than words.
They run no language model and no lexicon, so the output is the phones the
acoustic model heard, not the nearest dictionary word: a mispronunciation stays
visible instead of being corrected away. Both use `POST /v1/audio/transcriptions`
like every other ASR slug, and neither does live streaming.

The `text` field is a space-separated IPA phone stream, with one segment per
file. A `verbose_json`, `srt`, or `vtt` request (or `timestamp_granularities[]`)
turns each phone into a `words` entry with `start` and `end` in seconds.
`language` is accepted and echoed but does not steer decoding; both models are
language-neutral. Translation and task modes do not apply.

`wav2vec2-xlsr-53-espeak` is the wav2vec2 XLSR-53 checkpoint fine-tuned on
CommonVoice phonemes. It emits eSpeak-style IPA over a 392-symbol vocabulary,
the same alphabet the Kokoro G2P path uses. Code and weights are Apache-2.0,
the repository is ungated, and the download is 1.3 GB. It needs no extra image
dependency because `Wav2Vec2ForCTC` already ships with the bundled transformers.
Audio longer than `TALKIES_VAD_CHUNK_THRESHOLD` is VAD-chunked before decoding,
since the model uses full self-attention.

`zipa-ipa` is the ZIPA (Zipformer IPA) small CTC checkpoint, served through the
sherpa-onnx runtime already in the images via the `sherpa_offline_ctc` executor.
The download is a 71 MB int8 model plus its token table, and it decodes a whole
file in one pass at tens of times realtime on CPU. The weights repository
carries no license tag; the training code and checkpoint lineage are permissive
(MIT and Apache-2.0), and the weights download at runtime rather than shipping
in the image.

## Bundled TTS models

| Slug | Registry repository | Mode | CPU | CUDA | Default voice |
|---|---|---|---:|---:|---|
| `kokoro-82m` | `hexgrad/Kokoro-82M` | `kokoro` | yes | yes | `af_heart` |
| `kokoro-82m-nvidia` | `nvidia/kokoro-82M-onnx-opt` | `kokoro_nvidia` | yes | yes | `af_heart` |
| `qwen3-tts-0.6b` | `Qwen/Qwen3-TTS-12Hz-0.6B-Base` | `base` | no | yes | `alloy` |
| `qwen3-tts-1.7b` | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | `base` | no | yes | `alloy` |
| `qwen3-tts-0.6b-custom` | `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` | `custom_voice` | no | yes | `Vivian` |
| `qwen3-tts-1.7b-custom` | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` | `custom_voice` | no | yes | `Vivian` |
| `qwen3-tts-1.7b-design` | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` | `voice_design` | no | yes | `design` |
| `chatterbox-turbo` | `ResembleAI/chatterbox-turbo` | `chatterbox` | no | yes | `builtin` |

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

### Chatterbox Turbo

English-only, 24 kHz mono, buffered (no PCM streaming). `voice` is either
`builtin` — the speaker shipped inside the checkpoint — or the name of a `.wav`
below `/data/custom-voices`, extension stripped, nested paths preserved. Unlike
Qwen3 no reference transcript is needed, but the clip must be **longer than 5
seconds**; shorter clips are rejected with a 400. `speed` has no effect and is
ignored.

Emotion and non-verbal sounds are written inline in `input` as bracketed tags.
They are real tokens in the model's tokenizer, so only these 19 work:

`[angry]` `[fear]` `[surprised]` `[whispering]` `[advertisement]` `[dramatic]`
`[narration]` `[crying]` `[happy]` `[sarcastic]` `[clear throat]` `[sigh]`
`[shush]` `[cough]` `[groan]` `[sniff]` `[gasp]` `[chuckle]` `[laugh]`

```json
{
  "model": "chatterbox-turbo",
  "voice": "builtin",
  "input": "Oh, that's hilarious. [chuckle] Anyway [sigh] back to work."
}
```

Every waveform this model produces carries Resemble AI's PerTh neural
watermark. The upstream package applies it unconditionally, so Talkies
substitutes a passthrough when `TALKIES_CHATTERBOX_WATERMARK` is false. It
defaults to true, and the backend logs an info line when it is off. See
[Configuration](configuration.md).

The checkpoint is MIT-licensed and ungated, so the entrypoint downloads it
without a Hugging Face token. Only the files the model actually loads are
fetched — the repository also ships a 1 GB `s3gen.safetensors` that this
backend never reads.

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

Every entry needs a `repo`. Optional `max_concurrency` is an integer from 1 to
1024 and limits active inference requests across every API surface. `executor`
defaults to `whisper` and must be one
of `whisper`, `parakeet`, `parakeet_cpp`, `canary_multitask`, `canary_salm`,
`kokoro`, `kokoro_nvidia`, `qwen3_tts`, `sherpa`, `sherpa_offline_ctc`, `vosk`,
`chatterbox`, or `wav2vec2_phoneme`. The entrypoint
downloads a selected repository into `/data/models/<slug>`. `revision`, when
set, is supplied to Hugging Face snapshot download; pin it to an immutable
commit for reproducible contents. An optional `download_patterns` array limits
the snapshot to static registry-owned paths, which is useful when one repository
contains several model variants. See [Streaming](streaming.md#sherpa-onnx-and-vosk)
for the required native-streaming fields.

Concurrency is an admission limit, not a promise that a backend executes all
admitted requests simultaneously. A backend may serialize access to a shared
native context; parakeet.cpp currently does this for feed and finalize calls.

The authoritative bundled entries are
[`models-cpu.json`](../models-cpu.json) and [`models.json`](../models.json).

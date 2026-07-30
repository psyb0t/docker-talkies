# Getting started

Talkies is an HTTP server in a Docker container. On first boot it downloads
the selected Hugging Face snapshots; mounting `/data` makes them reusable.

## Choose an image

| Image | Use it for |
|---|---|
| `psyb0t/talkies:latest` | CPU-compatible ASR and Kokoro TTS |
| `psyb0t/talkies:latest-cuda` | Every bundled model, including Qwen3 TTS; requires NVIDIA GPU support for `--gpus all` |

Both images listen on port 8000 and create `models/`, `files/`, and
`custom-voices/` under `/data`. See the complete availability matrix in
[Models and registries](models.md).

## Run the CPU image

```bash
docker run --rm -it --name talkies \
  -p 127.0.0.1:8000:8000 \
  -v "$PWD/talkies-data:/data" \
  -e TALKIES_ENABLED_MODELS=whisper-large-v3-turbo,kokoro-82m \
  psyb0t/talkies:latest
```

An empty `TALKIES_ENABLED_MODELS` downloads every registry entry. Subsequent
starts reuse a model snapshot when its directory is non-empty.

```bash
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/v1/models
```

`/healthz` reports the configured device and selected slugs. `/v1/models`
labels each selected slug as `asr` or `tts`.

## Run the CUDA image

```bash
docker run --rm -it --name talkies \
  --gpus all \
  -p 127.0.0.1:8000:8000 \
  -v "$PWD/talkies-data:/data" \
  -e TALKIES_ENABLED_MODELS=nemotron-3.5-asr-0.6b,qwen3-tts-0.6b \
  psyb0t/talkies:latest-cuda
```

The CPU image defaults `TALKIES_DEVICE=cpu` and CUDA image defaults it to
`cuda`. Accepted values are `auto`, `cpu`, `cuda`, and `cuda:N`. A Qwen3
backend refuses to load unless its selected device begins with `cuda`.

## Make a first request

```bash
curl -s http://127.0.0.1:8000/v1/audio/transcriptions \
  -F "file=@/path/to/clip.wav" \
  -F "model=whisper-large-v3-turbo"

curl -s http://127.0.0.1:8000/v1/audio/voices
curl -s http://127.0.0.1:8000/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"kokoro-82m","input":"Hello from Talkies.","voice":"af_heart"}' \
  --output hello.mp3
```

Voice names are model-specific; list them before synthesizing. [HTTP API](api.md)
documents every field and output format.

## Authenticate before exposing the port

`TALKIES_AUTH_TOKEN` is empty by default. When set, every HTTP and WebSocket
route except `/healthz` requires `Authorization: Bearer <token>`.

```bash
docker run --rm -it --name talkies \
  -p 127.0.0.1:8000:8000 \
  -v "$PWD/talkies-data:/data" \
  -e TALKIES_AUTH_TOKEN='replace-with-a-secret-kept-outside-git' \
  -e TALKIES_ENABLED_MODELS=whisper-large-v3-turbo \
  psyb0t/talkies:latest
```

Keep the secret in your deployment's secret store or an ignored environment
file. [Operations and security](operations.md) covers network exposure and
remote-file controls.

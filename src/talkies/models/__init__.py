"""Backend factory — build backends keyed by model_id from the registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import config
from .chatterbox import ChatterboxBackend
from .kokoro import KokoroBackend
from .kokoro_nvidia import KokoroNvidiaBackend
from .multitask import MultitaskBackend
from .parakeet import ParakeetBackend
from .parakeet_cpp import ParakeetCppBackend
from .qwen3_tts import Qwen3TTSBackend
from .salm import SalmBackend
from .sherpa import SherpaBackend
from .sherpa_offline_ctc import SherpaOfflineCtcBackend
from .vosk import VoskBackend
from .wav2vec2_phoneme import Wav2Vec2PhonemeBackend
from .whisper import WhisperBackend
from .whisper_stream import WhisperStreamingAdapter

_SHERPA_PATH_FIELDS = frozenset(
    {
        "tokens",
        "encoder",
        "decoder",
        "joiner",
        "model",
        "lm_model",
        "hotwords_file",
    }
)


def _sherpa_config(
    entry: dict[str, Any],
    model_path: Path,
) -> dict[str, Any]:
    raw = entry.get("sherpa_config")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("sherpa executor requires a non-empty sherpa_config object")
    resolved = dict(raw)
    for field in _SHERPA_PATH_FIELDS:
        value = resolved.get(field)
        if isinstance(value, str) and value and not value.startswith("/"):
            resolved[field] = str(model_path / value)
    return resolved


def build_backends(registry: dict[str, dict], device: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for model_id, entry in registry.items():
        executor = entry.get("executor", "whisper")
        repo = entry["repo"]
        model_path = config.MODELS_DIR / model_id
        if executor == "whisper":
            whisper_backend = WhisperBackend(
                model_id=model_id,
                repo=repo,
                model_path=model_path,
                device=device,
            )
            out[model_id] = WhisperStreamingAdapter(
                whisper_backend,
                max_buffer_seconds=config.STREAM_MAX_BUFFER_SECONDS,
                max_frame_bytes=config.STREAM_MAX_FRAME_BYTES,
            )
            continue
        if executor == "parakeet":
            out[model_id] = ParakeetBackend(
                model_id=model_id,
                repo=repo,
                model_path=model_path,
                device=device,
            )
            continue
        if executor == "parakeet_cpp":
            gguf_file = entry.get("gguf_file")
            default_lang = entry.get("default_source_lang", "auto")
            out[model_id] = ParakeetCppBackend(
                model_id=model_id,
                repo=repo,
                model_path=model_path,
                device=device,
                gguf_file=gguf_file,
                default_lang=default_lang,
            )
            continue
        if executor == "sherpa":
            out[model_id] = SherpaBackend(
                model_id=model_id,
                repo=repo,
                recognizer_config=_sherpa_config(entry, model_path),
                device=device,
                recognizer_factory=entry.get(
                    "recognizer_factory",
                    "from_transducer",
                ),
            )
            continue
        if executor == "sherpa_offline_ctc":
            out[model_id] = SherpaOfflineCtcBackend(
                model_id=model_id,
                repo=repo,
                recognizer_config=_sherpa_config(entry, model_path),
                device=device,
            )
            continue
        if executor == "wav2vec2_phoneme":
            out[model_id] = Wav2Vec2PhonemeBackend(
                model_id=model_id,
                repo=repo,
                model_path=model_path,
                device=device,
            )
            continue
        if executor == "vosk":
            out[model_id] = VoskBackend(
                model_id=model_id,
                repo=repo,
                model_path=model_path,
                device=device,
                max_frame_bytes=config.STREAM_MAX_FRAME_BYTES,
            )
            continue
        if executor == "canary_salm":
            out[model_id] = SalmBackend(
                model_id=model_id,
                repo=repo,
                model_path=model_path,
                device=device,
            )
            continue
        if executor == "kokoro":
            out[model_id] = KokoroBackend(
                model_id=model_id,
                repo=repo,
                model_path=model_path,
                device=device,
            )
            continue
        if executor == "kokoro_nvidia":
            out[model_id] = KokoroNvidiaBackend(
                model_id=model_id,
                repo=repo,
                model_path=model_path,
                device=device,
            )
            continue
        if executor == "chatterbox":
            out[model_id] = ChatterboxBackend(
                model_id=model_id,
                repo=repo,
                model_path=model_path,
                device=device,
            )
            continue
        if executor == "qwen3_tts":
            qwen3_mode = entry.get("qwen3_mode", "base")
            default_language = entry.get("default_language", "English")
            out[model_id] = Qwen3TTSBackend(
                model_id=model_id,
                repo=repo,
                model_path=model_path,
                device=device,
                mode=qwen3_mode,
                default_language=default_language,
            )
            continue
        out[model_id] = MultitaskBackend(
            model_id=model_id,
            repo=repo,
            model_path=model_path,
            device=device,
        )
    return out


def is_tts_backend(backend: Any) -> bool:
    """Backends are duck-typed on the route layer — TTS backends have
    ``synthesize`` and ``voices``, ASR backends have ``transcribe``.
    """
    return hasattr(backend, "synthesize") and hasattr(backend, "voices")


def is_asr_backend(backend: Any) -> bool:
    return hasattr(backend, "transcribe")


def is_streaming_asr_backend(backend: Any) -> bool:
    return hasattr(backend, "start_stream")

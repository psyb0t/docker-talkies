"""Kokoro-82M ONNX backend (nvidia/kokoro-82M-onnx-opt, Apache-2.0).

NVIDIA's TensorRT-friendly ONNX export of hexgrad/Kokoro-82M served via
ONNXRuntime. Same StyleTTS2 + ISTFTNet weights as ``kokoro-82m``, but a
graph-optimized ONNX execution path: CUDA EP on the CUDA image, CPU EP
on the CPU image. No PyTorch on the hot path.

The snapshot ships ``kokoro-82m-v1.0.onnx`` (single fused graph) +
``voices.bin`` (raw packed f32, 53 voices × 510 × 256) + ``voices.txt``
(index→name) + ``tokens.txt`` (IPA phoneme → token id, identical to the
original Kokoro vocab).

Inference is implemented directly against ORT — we deliberately don't
take a dep on the ``kokoro-onnx`` PyPI lib because its install pulls
``numpy>=2.0.2``, which conflicts with the NeMo-pinned ``numpy==1.26.4``
the rest of the image runs on. Phonemization uses ``phonemizer`` with
the espeak-ng backend (already installed system-wide for the existing
Kokoro path).

Voice prefix → espeak lang code follows hexgrad/Kokoro-82M's VOICES.md:
the first letter of the voice name is the misaki lang_code. zh/ja
voices ship in voices.bin but high-quality G2P needs ``misaki[zh]`` /
``misaki[ja]`` extras, so they're filtered out — point those workloads
at the ``kokoro-82m`` slug, which already wires misaki via KPipeline.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import re
import time
from pathlib import Path
from typing import Any

from .base import SynthesisResult

SAMPLE_RATE = 24000

VOICE_DIM = 256
VOICE_TOKEN_LEN = 510
# Reserve two slots for the pad tokens [0, ..., 0] wrapped around the
# phoneme sequence on every forward.
MAX_PHONEME_LENGTH = VOICE_TOKEN_LEN - 2

# Voice name prefix → espeak-ng lang code. Mirrors hexgrad/Kokoro-82M's
# VOICES.md mapping. zh/ja are intentionally absent — see module docstring.
_PREFIX_TO_LANG: dict[str, str] = {
    "af_": "en-us",
    "am_": "en-us",
    "bf_": "en-gb",
    "bm_": "en-gb",
    "ef_": "es",
    "em_": "es",
    "ff_": "fr-fr",
    "fm_": "fr-fr",
    "hf_": "hi",
    "hm_": "hi",
    "if_": "it",
    "im_": "it",
    "pf_": "pt-br",
    "pm_": "pt-br",
}

DEFAULT_VOICE = "af_heart"

ONNX_FILENAME = "kokoro-82m-v1.0.onnx"
VOICES_BIN = "voices.bin"
VOICES_TXT = "voices.txt"
TOKENS_TXT = "tokens.txt"

# Split phoneme strings at these characters when they exceed
# MAX_PHONEME_LENGTH — same heuristic as kokoro-onnx upstream.
_PUNCT_SPLIT = re.compile(r"([.,!?;])")


class KokoroNvidiaBackend:
    def __init__(self, model_id: str, repo: str, model_path: Path, device: str) -> None:
        self.model_id = model_id
        self.repo = repo
        self.model_path = model_path
        self._device = device
        self._lock = asyncio.Lock()
        self._session: Any = None
        self._vocab: dict[str, int] | None = None
        self._voices_array: Any = None
        self._voice_index: dict[str, int] | None = None
        self._last_used: float | None = None
        self._voices_cache: list[str] | None = None
        self._log = logging.getLogger(f"talkies.kokoro_nvidia.{model_id}")

    def loaded(self) -> bool:
        return self._session is not None

    def last_used_secs_ago(self) -> float | None:
        if self._last_used is None:
            return None
        return time.monotonic() - self._last_used

    def voices(self) -> list[str]:
        if self._voices_cache is None:
            self._voices_cache = self._scan_voices() or None
        return list(self._voices_cache or [])

    def default_voice(self) -> str:
        catalog = self.voices()
        if DEFAULT_VOICE in catalog:
            return DEFAULT_VOICE
        if not catalog:
            raise RuntimeError(
                f"no voices found in {self.model_path / VOICES_TXT} — "
                "snapshot may not have been prefetched"
            )
        return catalog[0]

    def _scan_voices(self) -> list[str]:
        voices_txt = self.model_path / VOICES_TXT
        if not voices_txt.is_file():
            return []
        out: list[str] = []
        for line in voices_txt.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            _, _, name = line.partition("=")
            name = name.strip()
            if not name:
                continue
            if not any(name.startswith(p) for p in _PREFIX_TO_LANG):
                continue
            out.append(name)
        return out

    async def get_model(self) -> Any:
        if self._session is not None:
            return self._session
        async with self._lock:
            if self._session is not None:
                return self._session
            self._log.info("loading %s onto %s", self.repo, self._device)
            await asyncio.to_thread(self._load_sync)
            self._log.info("loaded %s", self.repo)
            return self._session

    def _load_sync(self) -> None:
        import numpy as np
        import onnxruntime as ort  # type: ignore[import-not-found]

        onnx_path = self.model_path / ONNX_FILENAME
        tokens_path = self.model_path / TOKENS_TXT
        voices_bin = self.model_path / VOICES_BIN
        voices_txt = self.model_path / VOICES_TXT
        for p in (onnx_path, tokens_path, voices_bin, voices_txt):
            if not p.is_file():
                raise FileNotFoundError(
                    f"kokoro-nvidia asset missing at {p} — snapshot may "
                    "not have been prefetched"
                )

        self._vocab = _parse_tokens(tokens_path)
        self._voice_index, self._voices_array = _load_voices(voices_bin, voices_txt)
        del np  # noqa: F841 — numpy imported above for early failure surface

        providers = _select_providers(self._device, ort)
        sess_opts = ort.SessionOptions()
        self._session = ort.InferenceSession(
            str(onnx_path), sess_options=sess_opts, providers=providers
        )
        self._log.info(
            "session providers=%s, voices=%d",
            self._session.get_providers(),
            len(self._voice_index),
        )

    async def synthesize(
        self,
        text: str,
        *,
        voice: str,
        speed: float,
        instructions: str | None = None,
        language: str | None = None,
        sampling: dict | None = None,
    ) -> SynthesisResult:
        # Kokoro takes no instruction-prompt input — accepted for protocol
        # parity with the TTSBackend interface and silently ignored, same
        # convention as the kokoro-82m (PyTorch) backend.
        del instructions
        if not text.strip():
            raise ValueError("input text is empty")
        if voice not in self.voices():
            raise ValueError(
                f"unknown voice {voice!r} for model {self.model_id!r}; "
                f"{len(self.voices())} voice(s) available — call "
                "GET /v1/audio/voices to list them"
            )
        await self.get_model()
        async with self._lock:
            result = await asyncio.to_thread(self._synthesize_sync, text, voice, speed)
            self._last_used = time.monotonic()
            return result

    def _synthesize_sync(self, text: str, voice: str, speed: float) -> SynthesisResult:
        import numpy as np

        assert self._voice_index is not None
        assert self._voices_array is not None
        assert self._vocab is not None
        assert self._session is not None

        lang = _PREFIX_TO_LANG[voice[:3]]
        voice_idx = self._voice_index[voice]
        phonemes = _phonemize(text, lang, self._vocab)
        if not phonemes:
            return SynthesisResult(pcm_int16=b"", sample_rate=SAMPLE_RATE)

        chunks: list[Any] = []
        for batch in _split_phonemes(phonemes, MAX_PHONEME_LENGTH):
            token_ids = [self._vocab[ch] for ch in batch if ch in self._vocab]
            if not token_ids:
                continue
            token_ids = token_ids[:MAX_PHONEME_LENGTH]
            n_tokens = len(token_ids)
            # Kokoro's style vector is indexed by the (pre-pad) token
            # count — same convention as kokoro-onnx upstream.
            style = self._voices_array[voice_idx, n_tokens]
            padded = np.asarray([[0, *token_ids, 0]], dtype=np.int64)
            style_in = np.asarray(style, dtype=np.float32).reshape(1, VOICE_DIM)
            speed_in = np.asarray([speed], dtype=np.float32)
            audio = self._session.run(
                None,
                {"tokens": padded, "style": style_in, "speed": speed_in},
            )[0]
            audio = np.asarray(audio, dtype=np.float32).reshape(-1)
            if audio.size:
                chunks.append(audio)

        if not chunks:
            return SynthesisResult(pcm_int16=b"", sample_rate=SAMPLE_RATE)
        full = np.concatenate(chunks)
        np.clip(full, -1.0, 1.0, out=full)
        int16 = (full * 32767.0).astype(np.int16)
        return SynthesisResult(pcm_int16=int16.tobytes(), sample_rate=SAMPLE_RATE)

    async def unload(self) -> None:
        async with self._lock:
            if self._session is None:
                return
            self._log.info("unloading %s", self.repo)
            self._session = None
            self._voices_array = None
            self._voice_index = None
            self._vocab = None
            self._last_used = None
        gc.collect()
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except ImportError:
            pass
        self._log.info("unloaded %s", self.repo)


def _select_providers(device: str, ort: Any) -> list[str]:
    """Pick ORT execution providers — CUDA EP first when device==cuda and
    the gpu wheel is installed, then CPU as a safety fallback. TensorRT EP
    auto-selected by ORT if available, but we don't list it explicitly:
    on first-call JIT compile times are punishing and the CUDA EP already
    captures most of the win.
    """
    available = ort.get_available_providers()
    wants_cuda = device == "cuda" or device.startswith("cuda:")
    out: list[str] = []
    if wants_cuda and "CUDAExecutionProvider" in available:
        out.append("CUDAExecutionProvider")
    out.append("CPUExecutionProvider")
    return out


def _parse_tokens(path: Path) -> dict[str, int]:
    """Parse the 'phoneme<sp>id' tokens.txt that NVIDIA ships."""
    vocab: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        # Format is "<char><space><int>"; the first char might itself be a
        # literal space (the IPA space token), so split from the right.
        head, _, tail = line.rpartition(" ")
        head = head if head != "" else line[:1]
        try:
            vocab[head] = int(tail)
        except ValueError as exc:
            raise ValueError(f"malformed tokens.txt line {line!r}") from exc
    if not vocab:
        raise ValueError(f"no tokens parsed from {path}")
    return vocab


def _load_voices(voices_bin: Path, voices_txt: Path) -> tuple[dict[str, int], Any]:
    """Read raw f32 voices.bin + index → (name→row, ndarray[N,510,256])."""
    import numpy as np

    index: dict[str, int] = {}
    for line in voices_txt.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        idx_s, _, name = line.partition("=")
        try:
            idx = int(idx_s)
        except ValueError as exc:
            raise ValueError(f"malformed voices.txt line {line!r}") from exc
        name = name.strip()
        if name:
            index[name] = idx
    if not index:
        raise ValueError(f"no voice names parsed from {voices_txt}")
    n_voices = max(index.values()) + 1
    raw = np.fromfile(str(voices_bin), dtype=np.float32)
    expected = n_voices * VOICE_TOKEN_LEN * VOICE_DIM
    if raw.size != expected:
        raise ValueError(
            f"{voices_bin}: got {raw.size} f32 elements, expected "
            f"{expected} ({n_voices} voices × {VOICE_TOKEN_LEN} × {VOICE_DIM})"
        )
    arr = raw.reshape(n_voices, VOICE_TOKEN_LEN, VOICE_DIM)
    return index, arr


def _phonemize(text: str, lang: str, vocab: dict[str, int]) -> str:
    """espeak-ng → IPA, then drop any chars outside the model's vocab."""
    import phonemizer  # type: ignore[import-not-found]

    raw = phonemizer.phonemize(
        text.strip(),
        lang,
        preserve_punctuation=True,
        with_stress=True,
    )
    return "".join(ch for ch in raw if ch in vocab).strip()


def _split_phonemes(phonemes: str, max_len: int) -> list[str]:
    """Split a long phoneme string at punctuation boundaries to keep each
    chunk under ``max_len``. Mirrors kokoro-onnx's batching heuristic.
    """
    if len(phonemes) <= max_len:
        return [phonemes]
    parts = _PUNCT_SPLIT.split(phonemes)
    batches: list[str] = []
    current = ""
    for raw in parts:
        part = raw.strip()
        if not part:
            continue
        if len(current) + len(part) + 1 >= max_len:
            if current:
                batches.append(current.strip())
            current = part
            continue
        if part in ".,!?;":
            current += part
            continue
        if current:
            current += " "
        current += part
    if current:
        batches.append(current.strip())
    return batches

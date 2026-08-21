"""Chatterbox Turbo TTS backend (ResembleAI/chatterbox-turbo, MIT).

Expressive English TTS with inline paralinguistic tags and transcript-free
zero-shot voice cloning.

Tags are real vocabulary tokens in the checkpoint's tokenizer, written inline
in the input text: ``Oh no [sigh] not again.`` Upstream ``punc_norm`` only
capitalises the first letter and collapses whitespace, so bracketed tags reach
the tokenizer untouched.

Cloning needs a reference ``.wav`` only — no sibling transcript, unlike the
Qwen3 backend. Voices come from ``CUSTOM_VOICES_DIR`` plus ``builtin``, the
speaker baked into the checkpoint (``conds.pt``), so the model works before any
reference clip exists. ``BUILTIN_VOICES_DIR`` is deliberately NOT scanned: it
holds Qwen3's own reference set, some of which is shorter than
``MIN_REFERENCE_SECONDS`` and would surface as voices that always fail.

Turbo has no ``exaggeration`` / ``cfg_weight`` control — ``from_local`` builds
the checkpoint with ``emotion_adv`` disabled, and upstream logs a warning and
ignores those values when non-zero. We pass the disabled values explicitly to
keep that warning out of every request.

Every waveform carries Resemble's PerTh neural watermark: upstream applies it
unconditionally with no kwarg to disable. MIT does not require keeping it, but
stripping it is a deliberate call, so we ship upstream behaviour.

PyPI ``chatterbox-tts`` 0.1.7 is Turbo-only. Nano (the CPU-viable 110M variant)
exists on GitHub master but in no published wheel, so it cannot be hash-pinned
into the image yet.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
import wave
from pathlib import Path
from typing import Any

from .. import config
from .base import SynthesisResult

SAMPLE_RATE = 24000

# Selects the speaker baked into the checkpoint's ``conds.pt``. Not derived
# from a scanned path, so it cannot collide with a real voice name.
BUILTIN_VOICE = "builtin"

# ``prepare_conditionals`` asserts on this bound. Checked up front so a short
# clip surfaces as a 400-shaped ValueError, not an upstream AssertionError.
MIN_REFERENCE_SECONDS = 5.0

_INT16_PEAK = 32767.0

_CONDS_FILE = "conds.pt"
_REQUIRED_CHECKPOINT_FILES = (
    "ve.safetensors",
    "t3_turbo_v1.safetensors",
    "s3gen_meanflow.safetensors",
)

_DISABLED_EXAGGERATION = 0.0
_DISABLED_CFG_WEIGHT = 0.0
_DISABLED_MIN_P = 0.0

_SAMPLING_KEYS = (
    "temperature",
    "top_p",
    "top_k",
    "repetition_penalty",
)

_ORIGIN_BUILTIN = "builtin"
_ORIGIN_CUSTOM = "custom"

_SUBMODULES = ("t3", "s3gen", "ve")


class ChatterboxBackend:
    def __init__(
        self,
        model_id: str,
        repo: str,
        model_path: Path,
        device: str,
    ) -> None:
        self.model_id = model_id
        self.repo = repo
        self.model_path = model_path
        self._device = device
        self._lock = asyncio.Lock()
        self._model: Any = None
        self._last_used: float | None = None
        self._log = logging.getLogger(f"talkies.chatterbox.{model_id}")

    def loaded(self) -> bool:
        return self._model is not None

    def last_used_secs_ago(self) -> float | None:
        if self._last_used is None:
            return None
        return time.monotonic() - self._last_used

    def voices(self) -> list[str]:
        catalog = sorted(self._scan_voices().keys())
        if self._has_builtin_voice():
            return [BUILTIN_VOICE, *catalog]
        return catalog

    def voice_origins(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self._has_builtin_voice():
            out[BUILTIN_VOICE] = _ORIGIN_BUILTIN
        for name in self._scan_voices():
            out[name] = _ORIGIN_CUSTOM
        return out

    def default_voice(self) -> str:
        if self._has_builtin_voice():
            return BUILTIN_VOICE
        catalog = sorted(self._scan_voices().keys())
        if not catalog:
            raise RuntimeError(
                f"no chatterbox voices for {self.model_id!r}: "
                f"{self.model_path / _CONDS_FILE} is missing and no .wav "
                f"exists under {config.CUSTOM_VOICES_DIR}; drop a clip "
                f"longer than {MIN_REFERENCE_SECONDS:g}s into "
                f"{config.CUSTOM_VOICES_DIR}/"
            )
        return catalog[0]

    def _has_builtin_voice(self) -> bool:
        return (self.model_path / _CONDS_FILE).is_file()

    def _scan_voices(self) -> dict[str, Path]:
        out: dict[str, Path] = {}
        base = config.CUSTOM_VOICES_DIR
        base_resolved = _resolve_dir(base)
        if base_resolved is None:
            return out
        for wav in sorted(base.rglob("*.wav")):
            try:
                resolved = wav.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not resolved.is_relative_to(base_resolved):
                self._log.warning(
                    "skipping voice wav %s, reason=escapes_base base=%s",
                    wav,
                    base,
                )
                continue
            name = wav.relative_to(base).with_suffix("").as_posix()
            if not name or name.startswith("."):
                continue
            if name == BUILTIN_VOICE:
                self._log.warning(
                    "skipping voice wav %s, reason=reserved_name name=%s",
                    wav,
                    BUILTIN_VOICE,
                )
                continue
            out[name] = wav
        return out

    def _resolve_reference_wav(self, voice: str) -> Path | None:
        if voice == BUILTIN_VOICE and self._has_builtin_voice():
            return None
        wav_path = self._scan_voices().get(voice)
        if wav_path is None:
            raise ValueError(
                f"unknown voice {voice!r} for model {self.model_id!r}; "
                f"{len(self.voices())} voice(s) available — call "
                "GET /v1/audio/voices to list them"
            )
        duration = _reference_duration_seconds(wav_path)
        if duration <= MIN_REFERENCE_SECONDS:
            raise ValueError(
                f"reference clip for voice {voice!r} is {duration:.2f}s; "
                f"chatterbox needs more than {MIN_REFERENCE_SECONDS:g}s"
            )
        return wav_path

    async def get_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is not None:
                return self._model
            self._log.info("loading %s onto %s", self.repo, self._device)
            self._model = await asyncio.to_thread(self._load_sync)
            self._log.info("loaded %s", self.repo)
            return self._model

    def _load_sync(self) -> Any:
        # Checkpoint check precedes the import so a missing snapshot fails fast
        # with a useful path instead of paying a multi-second torch import.
        for filename in _REQUIRED_CHECKPOINT_FILES:
            path = self.model_path / filename
            if not path.is_file():
                raise FileNotFoundError(
                    f"chatterbox checkpoint file missing at {path} — "
                    "snapshot may not have been prefetched"
                )

        from chatterbox.tts_turbo import ChatterboxTurboTTS

        device = "cuda" if self._device.startswith("cuda") else "cpu"
        model = ChatterboxTurboTTS.from_local(self.model_path, device)
        if not config.CHATTERBOX_WATERMARK:
            # generate() calls self.watermarker.apply_watermark unconditionally,
            # and watermarker is a plain instance attribute, so swapping it here
            # disables the mark without patching or vendoring upstream.
            model.watermarker = _PassthroughWatermarker()
            self._log.info(
                "watermarking disabled, reason=%s",
                "TALKIES_CHATTERBOX_WATERMARK",
            )
        return model

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
        if not text.strip():
            raise ValueError("input text is empty")
        if speed != 1.0:
            self._log.debug(
                "ignoring speed=%.2f, reason=no_speed_control", speed
            )
        reference_wav = self._resolve_reference_wav(voice)
        sampling_kwargs = _sampling_kwargs(sampling)
        model = await self.get_model()
        # prepare_conditionals mutates model.conds, so the lock has to span
        # generation — two concurrent voices would otherwise cross-talk.
        async with self._lock:
            result = await asyncio.to_thread(
                self._synthesize_sync,
                model,
                text,
                reference_wav,
                sampling_kwargs,
            )
            self._last_used = time.monotonic()
            return result

    def _synthesize_sync(
        self,
        model: Any,
        text: str,
        reference_wav: Path | None,
        sampling_kwargs: dict[str, Any],
    ) -> SynthesisResult:
        import numpy as np

        wav = model.generate(
            text,
            audio_prompt_path=str(reference_wav) if reference_wav else None,
            exaggeration=_DISABLED_EXAGGERATION,
            cfg_weight=_DISABLED_CFG_WEIGHT,
            min_p=_DISABLED_MIN_P,
            **sampling_kwargs,
        )
        audio = wav.squeeze(0).detach().cpu().numpy()
        audio = audio.astype(np.float32, copy=False)
        if audio.size == 0:
            self._log.warning("empty waveform, reason=no_audio_generated")
            return SynthesisResult(pcm_int16=b"", sample_rate=SAMPLE_RATE)
        # The watermarker can nudge samples past unity; clamp before int16.
        np.clip(audio, -1.0, 1.0, out=audio)
        int16 = (audio * _INT16_PEAK).astype(np.int16)
        return SynthesisResult(pcm_int16=int16.tobytes(), sample_rate=SAMPLE_RATE)

    async def unload(self) -> None:
        async with self._lock:
            if self._model is None:
                return
            self._log.info("unloading %s", self.repo)
            model = self._model
            self._model = None
            self._last_used = None
        # Chatterbox holds three separate nn.Modules rather than one root
        # module, so each has to be walked off the GPU individually.
        for name in _SUBMODULES:
            submodule = getattr(model, name, None)
            if submodule is None:
                continue
            try:
                submodule.cpu()
            except Exception:  # noqa: BLE001 — eviction must not fail a request
                self._log.exception("%s.cpu() failed for %s", name, self.repo)
        del model
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


class _PassthroughWatermarker:
    """Stand-in for perth.PerthImplicitWatermarker that returns audio unchanged.

    Matches the upstream signature ``(signal, sample_rate, **_)`` so it drops
    into ``model.watermarker`` without generate() knowing the difference.
    """

    def apply_watermark(self, signal: Any, sample_rate: int, **_: Any) -> Any:
        return signal


def _resolve_dir(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def _sampling_kwargs(sampling: dict | None) -> dict[str, Any]:
    if not sampling:
        return {}
    return {key: sampling[key] for key in _SAMPLING_KEYS if key in sampling}


def _reference_duration_seconds(path: Path) -> float:
    """Seconds of audio in ``path``, header-only — never decodes the samples.

    stdlib ``wave`` first so the check works anywhere (it is also the only
    reader available in the dev image). It rejects non-PCM containers such as
    IEEE-float WAV, which are legitimate reference clips, so soundfile — which
    ships in the prod images — backstops those.
    """
    try:
        with wave.open(str(path), "rb") as handle:
            frame_rate = handle.getframerate()
            frames = handle.getnframes()
    except (wave.Error, EOFError, OSError):
        return _reference_duration_seconds_via_soundfile(path)
    if not frame_rate:
        raise ValueError(f"reference clip at {path} declares no sample rate")
    return float(frames) / float(frame_rate)


def _reference_duration_seconds_via_soundfile(path: Path) -> float:
    try:
        import soundfile as sf
    except ImportError as err:
        raise ValueError(f"unreadable reference clip at {path}") from err
    try:
        info = sf.info(str(path))
    except (RuntimeError, OSError) as err:
        raise ValueError(f"unreadable reference clip at {path}") from err
    if not info.samplerate:
        raise ValueError(f"reference clip at {path} declares no sample rate")
    return float(info.frames) / float(info.samplerate)

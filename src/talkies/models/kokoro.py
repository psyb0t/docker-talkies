"""Kokoro-82M TTS backend (hexgrad/Kokoro-82M, Apache-2.0).

Lazy load of ``KModel`` + per-lang-code ``KPipeline`` cache. All voice
``.pt`` files are loaded directly off the flat snapshot dir at
``$TALKIES_DATA_DIR/models/<slug>/voices/`` and pre-populated into each
pipeline's voice cache, so the server never calls ``hf_hub_download`` at
synth time (we run with ``HF_HUB_OFFLINE=1``).

Voices are filtered to lang codes we can run without misaki extras:
American + British English (misaki[en], shipped), and the espeak-ng-driven
languages (es/fr/hi/it/pt). Japanese (misaki[ja]) and Mandarin
(misaki[zh]) are skipped — they need heavy extras (pyopenjtalk / pypinyin
etc.) and aren't pulled in by the base image.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from pathlib import Path
from typing import Any

from .base import SynthesisResult

SAMPLE_RATE = 24000

# Voice name prefixes we expose. Each voice file in the snapshot's
# ``voices/`` dir is keyed by ``<lang><gender>_<name>`` — the first char
# is the misaki lang_code, the second is f/m. We allowlist lang codes that
# need no extras beyond the base image's misaki[en] + espeak-ng install.
SUPPORTED_VOICE_PREFIXES = (
    "af_",
    "am_",  # a — American English (misaki[en])
    "bf_",
    "bm_",  # b — British English  (misaki[en])
    "ef_",
    "em_",  # e — Spanish          (espeak-ng)
    "ff_",
    "fm_",  # f — French           (espeak-ng)
    "hf_",
    "hm_",  # h — Hindi            (espeak-ng)
    "if_",
    "im_",  # i — Italian          (espeak-ng)
    "pf_",
    "pm_",  # p — Portuguese       (espeak-ng)
)

DEFAULT_VOICE = "af_heart"


class KokoroBackend:
    def __init__(self, model_id: str, repo: str, model_path: Path, device: str) -> None:
        self.model_id = model_id
        self.repo = repo
        self.model_path = model_path
        self._device = device
        self._lock = asyncio.Lock()
        self._model: Any = None
        self._pipelines: dict[str, Any] = {}
        self._last_used: float | None = None
        self._voices_cache: list[str] | None = None
        self._log = logging.getLogger(f"talkies.kokoro.{model_id}")

    def loaded(self) -> bool:
        return self._model is not None

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
                f"no voices found under {self.model_path / 'voices'} — "
                "snapshot may not have been prefetched"
            )
        return catalog[0]

    def _scan_voices(self) -> list[str]:
        voices_dir = self.model_path / "voices"
        if not voices_dir.is_dir():
            return []
        out: list[str] = []
        for f in sorted(voices_dir.glob("*.pt")):
            name = f.stem
            if not name.startswith(SUPPORTED_VOICE_PREFIXES):
                continue
            out.append(name)
        return out

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
        from kokoro import KModel

        config_path = self.model_path / "config.json"
        weights_path = self.model_path / "kokoro-v1_0.pth"
        if not config_path.is_file():
            raise FileNotFoundError(f"kokoro config.json missing at {config_path}")
        if not weights_path.is_file():
            raise FileNotFoundError(f"kokoro weights missing at {weights_path}")
        device = "cuda" if self._device.startswith("cuda") else "cpu"
        model = (
            KModel(
                repo_id=self.repo,
                config=str(config_path),
                model=str(weights_path),
            )
            .to(device)
            .eval()
        )
        return model

    def _get_or_create_pipeline(self, lang_code: str, model: Any) -> Any:
        cached = self._pipelines.get(lang_code)
        if cached is not None:
            return cached
        from kokoro import KPipeline

        pipeline = KPipeline(
            lang_code=lang_code,
            repo_id=self.repo,
            model=model,
        )
        self._pipelines[lang_code] = pipeline
        return pipeline

    def _preload_voice(self, pipeline: Any, voice: str) -> None:
        """Stash the voice tensor in ``pipeline.voices`` from local disk.

        ``KPipeline.load_single_voice`` checks ``self.voices`` first, so a
        pre-populated entry skips the ``hf_hub_download`` call entirely
        (we run ``HF_HUB_OFFLINE=1`` in prod).
        """
        if voice in pipeline.voices:
            return
        import torch

        voice_path = self.model_path / "voices" / f"{voice}.pt"
        if not voice_path.is_file():
            raise FileNotFoundError(f"voice pack missing at {voice_path}")
        pack = torch.load(str(voice_path), weights_only=True)
        pipeline.voices[voice] = pack

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
        if voice not in self.voices():
            raise ValueError(
                f"unknown voice {voice!r} for model {self.model_id!r}; "
                f"{len(self.voices())} voice(s) available — call "
                "GET /v1/audio/voices to list them"
            )
        model = await self.get_model()
        async with self._lock:
            result = await asyncio.to_thread(
                self._synthesize_sync, model, text, voice, speed
            )
            self._last_used = time.monotonic()
            return result

    def _synthesize_sync(
        self, model: Any, text: str, voice: str, speed: float
    ) -> SynthesisResult:
        import numpy as np

        lang_code = voice[0]
        pipeline = self._get_or_create_pipeline(lang_code, model)
        self._preload_voice(pipeline, voice)

        chunks: list[Any] = []
        for result in pipeline(text, voice=voice, speed=speed):
            audio = result.audio
            if audio is None:
                continue
            chunks.append(audio.detach().cpu().numpy())

        if not chunks:
            return SynthesisResult(pcm_int16=b"", sample_rate=SAMPLE_RATE)

        full = np.concatenate(chunks).astype(np.float32, copy=False)
        # Kokoro emits float audio in [-1, 1]; clamp defensively before int16.
        np.clip(full, -1.0, 1.0, out=full)
        int16 = (full * 32767.0).astype(np.int16)
        return SynthesisResult(pcm_int16=int16.tobytes(), sample_rate=SAMPLE_RATE)

    async def unload(self) -> None:
        async with self._lock:
            if self._model is None:
                return
            self._log.info("unloading %s", self.repo)
            model = self._model
            self._model = None
            self._pipelines.clear()
            self._last_used = None
        try:
            model.cpu()
        except Exception:  # noqa: BLE001
            self._log.exception("model.cpu() failed for %s", self.repo)
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

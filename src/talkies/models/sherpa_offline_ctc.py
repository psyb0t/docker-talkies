"""Offline sherpa-onnx CTC backend — audio → phone stream, no transcript.

Currently drives ZIPA (Zipformer IPA): a zipformer2 CTC model exported for
sherpa-onnx that emits IPA phones directly from audio. Like the wav2vec2
phoneme backend this is phoneme recognition, not word transcription — no
language model and no lexicon, so the phones returned are the phones heard, and
a mispronunciation is not corrected toward the nearest real word.

Distinct from the streaming ``sherpa`` executor, which builds an
``OnlineRecognizer`` for live ASR. This one builds an ``OfflineRecognizer`` and
decodes a whole normalized file in one pass; the zipformer CTC decode is linear
in audio length, so no VAD chunking is needed.

Output is a space-separated phone stream. sherpa surfaces the model's
word-boundary token as a whitespace entry in ``result.tokens``; those are
dropped so only phones remain. ``with_timestamps`` fills per-phone ``words``
from ``result.timestamps`` (seconds, one per token).
"""

from __future__ import annotations

import asyncio
import gc
import importlib
import logging
import time
from collections.abc import Mapping
from typing import Any

from .base import TranscribeResult

_SAMPLE_RATE = 16000
_RECOGNIZER_FACTORY = "from_zipformer_ctc"
# Modest default: the int8 model already runs at tens of times realtime on CPU,
# and a self-hosted host shares cores across every resident model.
_DEFAULT_NUM_THREADS = 2


class SherpaOfflineCtcBackend:
    def __init__(
        self,
        model_id: str,
        repo: str,
        recognizer_config: Mapping[str, Any],
        device: str,
    ) -> None:
        self.model_id = model_id
        self.repo = repo
        self._recognizer_config = dict(recognizer_config)
        self._device = device
        self._lock = asyncio.Lock()
        self._recognizer: Any = None
        self._last_used: float | None = None
        self._log = logging.getLogger(f"talkies.sherpa_offline_ctc.{model_id}")

    def loaded(self) -> bool:
        return self._recognizer is not None

    def last_used_secs_ago(self) -> float | None:
        if self._last_used is None:
            return None
        return time.monotonic() - self._last_used

    async def get_model(self) -> Any:
        if self._recognizer is not None:
            return self._recognizer
        async with self._lock:
            if self._recognizer is not None:
                return self._recognizer
            self._log.info("loading %s onto %s", self.repo, self._device)
            self._recognizer = await asyncio.to_thread(self._load_recognizer)
            self._log.info("loaded %s", self.repo)
            return self._recognizer

    def _load_recognizer(self) -> Any:
        try:
            sherpa_onnx = importlib.import_module("sherpa_onnx")
        except ModuleNotFoundError as err:
            raise RuntimeError(
                "sherpa offline CTC requires the sherpa-onnx package"
            ) from err
        factory = getattr(sherpa_onnx.OfflineRecognizer, _RECOGNIZER_FACTORY, None)
        if not callable(factory):
            raise RuntimeError(
                "installed sherpa-onnx does not provide "
                f"OfflineRecognizer.{_RECOGNIZER_FACTORY}"
            )
        config = dict(self._recognizer_config)
        config.setdefault("provider", self._provider())
        config.setdefault("num_threads", _DEFAULT_NUM_THREADS)
        return factory(**config)

    def _provider(self) -> str:
        if self._device.lower().startswith("cuda"):
            return "cuda"
        return "cpu"

    async def transcribe(
        self,
        audio_path: str,
        *,
        source_lang: str | None,
        target_lang: str | None,
        task: str,
        with_timestamps: bool = False,
    ) -> TranscribeResult:
        del target_lang, task  # phoneme recognition: no translation, no task modes
        recognizer = await self.get_model()
        async with self._lock:
            result = await asyncio.to_thread(
                self._recognize_sync,
                recognizer,
                audio_path,
                source_lang,
                with_timestamps,
            )
            self._last_used = time.monotonic()
            return result

    def _recognize_sync(
        self,
        recognizer: Any,
        audio_path: str,
        source_lang: str | None,
        with_timestamps: bool,
    ) -> TranscribeResult:
        from .. import vad as vad_mod

        audio = vad_mod.load_wav_16k_mono(audio_path)
        duration = audio.shape[0] / _SAMPLE_RATE

        stream = recognizer.create_stream()
        stream.accept_waveform(_SAMPLE_RATE, audio)
        recognizer.decode_stream(stream)
        result = stream.result

        phones, words = self._phones_and_words(result, duration, with_timestamps)
        text = " ".join(phones)
        segments: list[dict] = []
        if text:
            segments.append({"id": 0, "start": 0.0, "end": duration, "text": text})
        return TranscribeResult(
            text=text,
            segments=segments,
            words=words,
            language=source_lang,
            duration=duration,
            supports_timestamps=True,
        )

    def _phones_and_words(
        self,
        result: Any,
        duration: float,
        with_timestamps: bool,
    ) -> tuple[list[str], list[dict]]:
        tokens = list(getattr(result, "tokens", ()) or ())
        timestamps = list(getattr(result, "timestamps", ()) or ())
        usable = min(len(tokens), len(timestamps)) if with_timestamps else len(tokens)

        phones: list[str] = []
        words: list[dict] = []
        for index in range(usable):
            token = tokens[index]
            # sherpa renders the word-boundary token as whitespace; keep phones.
            if not isinstance(token, str) or not token.strip():
                continue
            phones.append(token)
            if not with_timestamps:
                continue
            start = float(timestamps[index])
            end = float(timestamps[index + 1]) if index + 1 < usable else duration
            words.append({"word": token, "start": start, "end": max(start, end)})
        return phones, words

    async def unload(self) -> None:
        async with self._lock:
            if self._recognizer is None:
                return
            self._log.info("unloading %s", self.repo)
            self._recognizer = None
            self._last_used = None
        await asyncio.to_thread(gc.collect)
        self._log.info("unloaded %s", self.repo)

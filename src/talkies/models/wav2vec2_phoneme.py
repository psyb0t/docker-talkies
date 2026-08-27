"""Wav2Vec2 phoneme-CTC backend — audio → IPA phone stream, no transcript.

``facebook/wav2vec2-xlsr-53-espeak-cv-ft`` and its siblings. Unlike the
word-level ASR backends this emits the phones the acoustic model actually
heard, with no language-model rescoring and no lexicon, so a mispronunciation
surfaces as the phones that were spoken rather than being auto-corrected to the
nearest real word. That makes it a pronunciation gate, not a transcriber.

Output is space-separated eSpeak-style IPA (the checkpoint's 392-token
vocabulary) — the same phone alphabet the Kokoro G2P path emits — so a caller
can align heard phones against expected phones with plain edit distance.

The processor MUST be built with ``do_phonemize=False``. Left at its default it
runs eSpeak at tokenizer-load time (text→phone G2P we never use for decoding)
and raises a misleading "requires the protobuf library" error, because the
image ships ``phonemizer-fork``, which transformers does not recognise as the
phonemizer distribution. CTC argmax decoding needs neither eSpeak nor protobuf.

Long audio is VAD-chunked like the other backends: full self-attention makes a
single multi-minute forward pass expensive, so anything past
``VAD_CHUNK_THRESHOLD_SECONDS`` is split into speech regions and decoded per
chunk, with per-phone offsets shifted onto the absolute timeline.

The standard transcription parameters flow through unchanged. ``with_timestamps``
turns on per-phone ``words`` (each phone is one token with a start/end derived
from the CTC frame offsets) and per-region ``segments``, so ``verbose_json``,
``srt``/``vtt`` and ``timestamp_granularities`` all work exactly as they do for
the other ASR models. Translation and task modes do not apply — the phone
stream is language-neutral — so ``target_lang`` and ``task`` are ignored.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import config
from .base import TranscribeResult

# CTC emits one logit frame every ``inputs_to_logits_ratio`` input samples; at
# 16 kHz that is 320/16000 = 20 ms per frame. The value is read from the loaded
# model config — this constant is only the fallback should that field vanish.
_DEFAULT_LOGITS_RATIO = 320

# Enough of the snapshot to fail fast with a useful path before paying the
# multi-second torch import when a prefetch was incomplete. The weight file is
# checked separately because its name varies (.safetensors vs .bin).
_REQUIRED_CHECKPOINT_FILES = (
    "config.json",
    "vocab.json",
    "preprocessor_config.json",
)
_WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")


@dataclass
class _Loaded:
    """The processor and CTC model are always used together, so one handle
    carries both through the lazy-load / idle-unload lifecycle."""

    processor: Any
    model: Any


class Wav2Vec2PhonemeBackend:
    def __init__(self, model_id: str, repo: str, model_path: Path, device: str) -> None:
        self.model_id = model_id
        self.repo = repo
        self.model_path = model_path
        self._device = device
        self._lock = asyncio.Lock()
        self._model: _Loaded | None = None
        self._last_used: float | None = None
        self._log = logging.getLogger(f"talkies.wav2vec2_phoneme.{model_id}")

    def loaded(self) -> bool:
        return self._model is not None

    def last_used_secs_ago(self) -> float | None:
        if self._last_used is None:
            return None
        return time.monotonic() - self._last_used

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

    def _load_sync(self) -> _Loaded:
        for filename in _REQUIRED_CHECKPOINT_FILES:
            path = self.model_path / filename
            if not path.is_file():
                raise FileNotFoundError(
                    f"wav2vec2 phoneme checkpoint file missing at {path} — "
                    "snapshot may not have been prefetched"
                )
        if not any((self.model_path / w).is_file() for w in _WEIGHT_FILES):
            raise FileNotFoundError(
                f"wav2vec2 phoneme weights missing under {self.model_path} — "
                f"expected one of {_WEIGHT_FILES}"
            )

        from transformers import AutoProcessor, Wav2Vec2ForCTC

        # do_phonemize=False is mandatory; see the module docstring.
        processor = AutoProcessor.from_pretrained(
            str(self.model_path), do_phonemize=False
        )
        model = Wav2Vec2ForCTC.from_pretrained(str(self.model_path))
        model = model.to(self._device).eval()
        return _Loaded(processor=processor, model=model)

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
        loaded = await self.get_model()
        async with self._lock:
            result = await asyncio.to_thread(
                self._recognize_sync,
                loaded,
                audio_path,
                source_lang,
                with_timestamps,
            )
            self._last_used = time.monotonic()
            return result

    def _recognize_sync(
        self,
        loaded: _Loaded,
        audio_path: str,
        source_lang: str | None,
        with_timestamps: bool,
    ) -> TranscribeResult:
        from .. import vad as vad_mod

        audio = vad_mod.load_wav_16k_mono(audio_path)
        sample_rate = vad_mod.SAMPLE_RATE
        duration = audio.shape[0] / sample_rate

        regions = self._regions(audio, duration)
        if not regions:
            return TranscribeResult(
                text="",
                language=source_lang,
                duration=duration,
                supports_timestamps=True,
            )

        phone_parts: list[str] = []
        segments: list[dict] = []
        words: list[dict] = []
        for start_sample, end_sample in regions:
            offset_seconds = start_sample / sample_rate
            chunk = audio[start_sample:end_sample]
            phones, chunk_words = self._decode_region(
                loaded, chunk, offset_seconds, with_timestamps
            )
            if not phones:
                continue
            phone_parts.append(phones)
            segments.append(
                {
                    "id": len(segments),
                    "start": offset_seconds,
                    "end": end_sample / sample_rate,
                    "text": phones,
                }
            )
            words.extend(chunk_words)

        return TranscribeResult(
            text=" ".join(phone_parts).strip(),
            segments=segments,
            words=words,
            language=source_lang,
            duration=duration,
            supports_timestamps=True,
        )

    def _regions(self, audio: Any, duration: float) -> list[tuple[int, int]]:
        """Sample-index [start, end) spans to decode.

        Short audio is one span over the whole clip. Longer audio is VAD-chunked
        into speech regions so a single long self-attention pass never runs.
        """
        from .. import vad as vad_mod

        if duration <= config.VAD_CHUNK_THRESHOLD_SECONDS:
            return [(0, int(audio.shape[0]))]
        raw_regions = vad_mod.detect_speech_regions(
            audio,
            threshold=config.VAD_THRESHOLD,
            min_silence_ms=config.VAD_MIN_SILENCE_MS,
            speech_pad_ms=config.VAD_SPEECH_PAD_MS,
        )
        chunks = vad_mod.merge_speech_regions(
            raw_regions, max_speech_seconds=config.VAD_MAX_SPEECH_SECONDS
        )
        if chunks:
            self._log.info(
                "vad chunked %.1fs into %d region(s) for %s",
                duration,
                len(chunks),
                self.model_id,
            )
        return [(r.start, r.end) for r in chunks]

    def _decode_region(
        self,
        loaded: _Loaded,
        chunk: Any,
        offset_seconds: float,
        with_timestamps: bool,
    ) -> tuple[str, list[dict]]:
        import torch

        from .. import vad as vad_mod

        processor, model = loaded.processor, loaded.model
        inputs = processor(
            chunk, sampling_rate=vad_mod.SAMPLE_RATE, return_tensors="pt"
        )
        input_values = inputs.input_values.to(model.device)
        with torch.no_grad():
            logits = model(input_values).logits
        predicted_ids = torch.argmax(logits, dim=-1)

        if not with_timestamps:
            phones = processor.batch_decode(predicted_ids)[0].strip()
            return phones, []

        decoded = processor.batch_decode(predicted_ids, output_char_offsets=True)
        phones = decoded["text"][0].strip()
        ratio = (
            getattr(model.config, "inputs_to_logits_ratio", _DEFAULT_LOGITS_RATIO)
            or _DEFAULT_LOGITS_RATIO
        )
        seconds_per_frame = ratio / vad_mod.SAMPLE_RATE
        words: list[dict] = []
        for offset in decoded["char_offsets"][0]:
            phone = str(offset.get("char", "")).strip()
            if not phone:
                continue
            start = offset_seconds + offset["start_offset"] * seconds_per_frame
            end = offset_seconds + offset["end_offset"] * seconds_per_frame
            words.append({"word": phone, "start": start, "end": end})
        return phones, words

    async def unload(self) -> None:
        async with self._lock:
            if self._model is None:
                return
            self._log.info("unloading %s", self.repo)
            loaded = self._model
            self._model = None
            self._last_used = None
        try:
            loaded.model.cpu()
        except Exception:  # noqa: BLE001 — eviction must not fail a request
            self._log.exception("model.cpu() failed for %s", self.repo)
        del loaded
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

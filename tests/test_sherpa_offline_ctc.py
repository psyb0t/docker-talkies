"""Tests for the offline sherpa-onnx CTC (ZIPA) phoneme backend.

Layered by dependency:

* Lifecycle and the ``_phones_and_words`` mapping are pure python and run in the
  dev image like the other backend suites.
* ``_recognize_sync`` needs numpy (via ``talkies.vad``); it ``importorskip``s it
  and stubs the recognizer at a narrow boundary.
* ``test_real_model_*`` load the actual ZIPA checkpoint through sherpa-onnx and
  run a real speech clip end to end; they skip when sherpa-onnx or the cached
  snapshot are absent and execute wherever both exist.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

# The dev image excludes the ML dependencies imported by the eager backend
# factory; stub the package so importing one backend module does not drag them.
_MODELS_PACKAGE = types.ModuleType("talkies.models")
_MODELS_PACKAGE.__path__ = [
    str(Path(__file__).resolve().parents[1] / "src" / "talkies" / "models")
]
sys.modules["talkies.models"] = _MODELS_PACKAGE

from talkies.models.sherpa_offline_ctc import (  # noqa: E402
    SherpaOfflineCtcBackend,
)

_REPO = "anyspeech/zipa-small-crctc-500k"
_REVISION = "a97a19eab1e5b2263ade7922ba97bf737dd418db"
_MODEL_ID = "zipa-ipa"
_MODEL_FILE = "model.int8.onnx"
_TOKENS_FILE = "tokens.txt"
_VOICE_CLIP = Path(__file__).resolve().parents[1] / "voices" / "qwen3" / "alloy.wav"

_SR = 16000
_MIN_REAL_PHONES = 20


def _make_backend() -> SherpaOfflineCtcBackend:
    return SherpaOfflineCtcBackend(
        model_id=_MODEL_ID,
        repo=_REPO,
        recognizer_config={"model": _MODEL_FILE, "tokens": _TOKENS_FILE},
        device="cpu",
    )


# --- lifecycle (stdlib only) ---------------------------------------------


def test_lifecycle_surface_before_load():
    backend = _make_backend()
    assert backend.loaded() is False
    assert backend.last_used_secs_ago() is None


# --- phone/word mapping (stdlib only) ------------------------------------


def test_phones_and_words_drops_boundary_and_offsets():
    backend = _make_backend()
    # sherpa renders the word boundary as a whitespace token.
    result = SimpleNamespace(
        tokens=[" ", "m", "ɪ", " ", "k"],
        timestamps=[0.0, 0.1, 0.2, 0.3, 0.4],
    )
    phones, words = backend._phones_and_words(
        result, duration=1.0, with_timestamps=True
    )
    assert phones == ["m", "ɪ", "k"]
    assert words == [
        {"word": "m", "start": 0.1, "end": 0.2},
        {"word": "ɪ", "start": 0.2, "end": 0.3},
        {"word": "k", "start": 0.4, "end": 1.0},  # last phone ends at duration
    ]


def test_phones_and_words_without_timestamps_skips_words():
    backend = _make_backend()
    result = SimpleNamespace(tokens=[" ", "m", "ɪ"], timestamps=[0.0, 0.1, 0.2])
    phones, words = backend._phones_and_words(
        result, duration=1.0, with_timestamps=False
    )
    assert phones == ["m", "ɪ"]
    assert words == []


def test_phones_and_words_ignores_non_string_and_extra_tokens():
    backend = _make_backend()
    # More tokens than timestamps, plus a non-string entry.
    result = SimpleNamespace(tokens=["m", 5, "k", "t"], timestamps=[0.0, 0.1, 0.2])
    phones, words = backend._phones_and_words(
        result, duration=1.0, with_timestamps=True
    )
    # usable is bounded by the shorter timestamps list (3), and the int is dropped.
    assert phones == ["m", "k"]
    assert [w["word"] for w in words] == ["m", "k"]


def test_phones_and_words_empty_result():
    backend = _make_backend()
    result = SimpleNamespace(tokens=[], timestamps=[])
    assert backend._phones_and_words(result, duration=1.0, with_timestamps=True) == (
        [],
        [],
    )


# --- recognize orchestration (numpy; recognizer stubbed) -----------------


class _FakeStream:
    def __init__(self, result):
        self.result = result
        self.accepted = None

    def accept_waveform(self, sample_rate, audio):
        self.accepted = (sample_rate, len(audio))


class _FakeRecognizer:
    def __init__(self, result):
        self._result = result
        self.decoded_stream = None

    def create_stream(self):
        return _FakeStream(self._result)

    def decode_stream(self, stream):
        self.decoded_stream = stream


def test_recognize_sync_builds_transcribe_result(monkeypatch):
    np = pytest.importorskip("numpy")
    vad_mod = pytest.importorskip("talkies.vad")
    monkeypatch.setattr(
        vad_mod, "load_wav_16k_mono", lambda _p: np.zeros(_SR, dtype=np.float32)
    )
    backend = _make_backend()
    result = SimpleNamespace(tokens=[" ", "m", "ɪ"], timestamps=[0.0, 0.5, 0.8])
    recognizer = _FakeRecognizer(result)

    out = backend._recognize_sync(recognizer, "x.wav", "en", with_timestamps=True)

    assert out.text == "m ɪ"
    assert out.segments == [{"id": 0, "start": 0.0, "end": 1.0, "text": "m ɪ"}]
    assert [w["word"] for w in out.words] == ["m", "ɪ"]
    assert out.language == "en"
    assert out.duration == pytest.approx(1.0)
    assert out.supports_timestamps is True
    # The whole normalized clip was fed at 16 kHz in one pass.
    assert recognizer.decoded_stream.accepted == (_SR, _SR)


def test_recognize_sync_empty_decode_yields_empty(monkeypatch):
    np = pytest.importorskip("numpy")
    vad_mod = pytest.importorskip("talkies.vad")
    monkeypatch.setattr(
        vad_mod, "load_wav_16k_mono", lambda _p: np.zeros(_SR, dtype=np.float32)
    )
    backend = _make_backend()
    recognizer = _FakeRecognizer(SimpleNamespace(tokens=[], timestamps=[]))

    out = backend._recognize_sync(recognizer, "x.wav", None, with_timestamps=True)
    assert out.text == ""
    assert out.segments == []
    assert out.words == []


# --- real model, end to end ----------------------------------------------


def _cached_model() -> tuple[str, str]:
    pytest.importorskip("sherpa_onnx")
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import LocalEntryNotFoundError

    try:
        snapshot = snapshot_download(
            _REPO,
            revision=_REVISION,
            allow_patterns=[_MODEL_FILE, _TOKENS_FILE],
            local_files_only=True,
        )
    except (LocalEntryNotFoundError, FileNotFoundError, OSError):
        pytest.skip(f"{_REPO} not cached; fetch it once online to run this test")
    root = Path(snapshot)
    model, tokens = root / _MODEL_FILE, root / _TOKENS_FILE
    if not (model.is_file() and tokens.is_file()):
        pytest.skip(f"{_REPO} snapshot missing {_MODEL_FILE}/{_TOKENS_FILE}")
    if not _VOICE_CLIP.is_file():
        pytest.skip(f"reference clip missing at {_VOICE_CLIP}")
    return str(model), str(tokens)


def _clip_as_16k_mono_wav() -> str:
    from talkies.audio import to_wav_16k_mono

    return to_wav_16k_mono(_VOICE_CLIP.read_bytes(), _VOICE_CLIP.name)


def _real_backend(model: str, tokens: str) -> SherpaOfflineCtcBackend:
    return SherpaOfflineCtcBackend(
        model_id=_MODEL_ID,
        repo=_REPO,
        recognizer_config={"model": model, "tokens": tokens},
        device="cpu",
    )


def test_real_model_emits_ipa_phone_stream():
    model, tokens = _cached_model()
    wav = _clip_as_16k_mono_wav()
    backend = _real_backend(model, tokens)
    try:
        result = asyncio.run(
            backend.transcribe(
                wav,
                source_lang=None,
                target_lang=None,
                task="asr",
                with_timestamps=False,
            )
        )
    finally:
        asyncio.run(backend.unload())
    phones = result.text.split()
    assert len(phones) > _MIN_REAL_PHONES
    assert any(any(ord(ch) > 127 for ch in phone) for phone in phones)
    assert result.words == []
    assert backend.loaded() is False


def test_real_model_per_phone_timestamps_are_ordered():
    model, tokens = _cached_model()
    wav = _clip_as_16k_mono_wav()
    backend = _real_backend(model, tokens)
    try:
        plain = asyncio.run(
            backend.transcribe(
                wav, source_lang=None, target_lang=None, task="asr"
            )
        )
        timed = asyncio.run(
            backend.transcribe(
                wav,
                source_lang="en",
                target_lang=None,
                task="asr",
                with_timestamps=True,
            )
        )
    finally:
        asyncio.run(backend.unload())
    assert timed.text == plain.text
    assert len(timed.words) > _MIN_REAL_PHONES
    assert all(set(w) == {"word", "start", "end"} for w in timed.words)
    assert all(w["end"] >= w["start"] for w in timed.words)
    assert all(w["word"].strip() for w in timed.words)  # boundary token filtered
    starts = [w["start"] for w in timed.words]
    assert starts == sorted(starts)

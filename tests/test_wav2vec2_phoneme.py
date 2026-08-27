"""Tests for the wav2vec2 phoneme-CTC backend.

Layered by dependency so each runs wherever its stack exists:

* Lifecycle and fail-fast checkpoint validation need only stdlib, so they run
  in the dev image like the other backend suites.
* The region planner and result-assembly tests need numpy (via ``talkies.vad``);
  they ``importorskip`` it and stub the torch decode at a narrow boundary.
* ``test_real_model_*`` load the actual checkpoint and run a real speech clip
  through ``transcribe`` end to end; they skip when torch/transformers or the
  cached snapshot are absent and execute wherever the ML stack and model exist.
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

from talkies.models.wav2vec2_phoneme import (  # noqa: E402
    _REQUIRED_CHECKPOINT_FILES,
    _WEIGHT_FILES,
    Wav2Vec2PhonemeBackend,
)

_REPO = "facebook/wav2vec2-xlsr-53-espeak-cv-ft"
_REVISION = "2c733782da5604684829819a5eb744c193fe9398"
_MODEL_ID = "wav2vec2-xlsr-53-espeak"
_VOICE_CLIP = Path(__file__).resolve().parents[1] / "voices" / "qwen3" / "alloy.wav"

# 16 kHz mono is what the audio pipeline normalises to (talkies.vad.SAMPLE_RATE).
_SR = 16000
_HALF_SECOND = _SR // 2
_MIN_REAL_PHONES = 20


@pytest.fixture
def make_backend(tmp_path):
    def _make() -> Wav2Vec2PhonemeBackend:
        model_path = tmp_path / "model"
        model_path.mkdir(parents=True, exist_ok=True)
        return Wav2Vec2PhonemeBackend(
            model_id=_MODEL_ID,
            repo=_REPO,
            model_path=model_path,
            device="cpu",
        )

    return _make


@pytest.fixture
def np_vad():
    """numpy plus the vad module the backend lazy-imports. Skips where numpy is
    absent (the dev image), so the numpy-dependent cases do not fail collection."""
    numpy = pytest.importorskip("numpy")
    vad_mod = pytest.importorskip("talkies.vad")
    return numpy, vad_mod


# --- lifecycle (stdlib only) ---------------------------------------------


def test_lifecycle_surface_before_load(make_backend):
    backend = make_backend()
    assert backend.loaded() is False
    assert backend.last_used_secs_ago() is None


# --- fail-fast checkpoint validation (stdlib only) ------------------------


@pytest.mark.parametrize("missing", _REQUIRED_CHECKPOINT_FILES)
def test_load_reports_missing_checkpoint_file(make_backend, missing):
    backend = make_backend()
    for name in (*_REQUIRED_CHECKPOINT_FILES, _WEIGHT_FILES[0]):
        (backend.model_path / name).write_bytes(b"stub")
    (backend.model_path / missing).unlink()
    with pytest.raises(FileNotFoundError, match=missing):
        backend._load_sync()


def test_load_reports_missing_weights(make_backend):
    backend = make_backend()
    for name in _REQUIRED_CHECKPOINT_FILES:
        (backend.model_path / name).write_bytes(b"stub")
    # No weight file written under either accepted name.
    with pytest.raises(FileNotFoundError, match="weights missing"):
        backend._load_sync()


# --- region planner (numpy) ----------------------------------------------


def test_regions_short_audio_is_single_span_without_vad(
    make_backend, np_vad, monkeypatch
):
    np, vad_mod = np_vad
    backend = make_backend()

    def _fail(*_a, **_k):
        raise AssertionError("VAD must not run on short audio")

    monkeypatch.setattr(vad_mod, "detect_speech_regions", _fail)
    audio = np.zeros(_SR, dtype=np.float32)  # 1 s
    assert backend._regions(audio, duration=1.0) == [(0, _SR)]


def test_regions_long_audio_maps_vad_chunks_to_sample_spans(
    make_backend, np_vad, monkeypatch
):
    from talkies import config

    np, vad_mod = np_vad
    backend = make_backend()
    monkeypatch.setattr(vad_mod, "detect_speech_regions", lambda *a, **k: ["raw"])
    monkeypatch.setattr(
        vad_mod,
        "merge_speech_regions",
        lambda *a, **k: [
            SimpleNamespace(start=_HALF_SECOND, end=_SR),
            SimpleNamespace(start=_SR, end=2 * _SR),
        ],
    )
    audio = np.zeros(_SR, dtype=np.float32)
    over_threshold = config.VAD_CHUNK_THRESHOLD_SECONDS + 10.0
    assert backend._regions(audio, duration=over_threshold) == [
        (_HALF_SECOND, _SR),
        (_SR, 2 * _SR),
    ]


def test_regions_long_audio_all_silence_is_empty(make_backend, np_vad, monkeypatch):
    np, vad_mod = np_vad
    backend = make_backend()
    monkeypatch.setattr(vad_mod, "detect_speech_regions", lambda *a, **k: [])
    monkeypatch.setattr(vad_mod, "merge_speech_regions", lambda *a, **k: [])
    audio = np.zeros(_SR, dtype=np.float32)
    assert backend._regions(audio, duration=999.0) == []


# --- result assembly (numpy; torch decode stubbed) -----------------------


def _stub_audio(np, vad_mod, monkeypatch, samples: int) -> None:
    monkeypatch.setattr(
        vad_mod,
        "load_wav_16k_mono",
        lambda _p: np.zeros(samples, dtype=np.float32),
    )


def test_recognize_joins_regions_and_offsets_words(
    make_backend, np_vad, monkeypatch
):
    np, vad_mod = np_vad
    backend = make_backend()
    _stub_audio(np, vad_mod, monkeypatch, _SR)
    monkeypatch.setattr(
        backend, "_regions", lambda a, d: [(0, _HALF_SECOND), (_HALF_SECOND, _SR)]
    )

    calls: list[tuple] = []

    def _fake_decode(loaded, chunk, offset_seconds, with_timestamps):
        calls.append((offset_seconds, with_timestamps))
        if offset_seconds == 0.0:
            return "p a", [{"word": "p", "start": 0.0, "end": 0.1}]
        return "t iː", [{"word": "t", "start": 0.5, "end": 0.6}]

    monkeypatch.setattr(backend, "_decode_region", _fake_decode)

    result = backend._recognize_sync(None, "x.wav", "en", with_timestamps=True)
    assert result.text == "p a t iː"
    assert result.segments[0] == {"id": 0, "start": 0.0, "end": 0.5, "text": "p a"}
    assert result.segments[1] == {"id": 1, "start": 0.5, "end": 1.0, "text": "t iː"}
    assert [w["word"] for w in result.words] == ["p", "t"]
    assert result.language == "en"
    assert result.supports_timestamps is True
    assert result.duration == pytest.approx(1.0)
    # The second region's decode offset is its start in seconds.
    assert calls[1][0] == pytest.approx(0.5)


def test_recognize_skips_empty_regions(make_backend, np_vad, monkeypatch):
    np, vad_mod = np_vad
    backend = make_backend()
    _stub_audio(np, vad_mod, monkeypatch, _SR)
    monkeypatch.setattr(
        backend, "_regions", lambda a, d: [(0, _HALF_SECOND), (_HALF_SECOND, _SR)]
    )

    def _fake_decode(loaded, chunk, offset_seconds, with_timestamps):
        if offset_seconds == 0.0:
            return "", []  # silence: no phones in this region
        return "k", [{"word": "k", "start": 0.5, "end": 0.6}]

    monkeypatch.setattr(backend, "_decode_region", _fake_decode)
    result = backend._recognize_sync(None, "x.wav", None, with_timestamps=True)
    assert result.text == "k"
    assert [s["id"] for s in result.segments] == [0]
    assert result.segments[0]["start"] == pytest.approx(0.5)
    assert [w["word"] for w in result.words] == ["k"]


def test_recognize_no_speech_returns_empty(make_backend, np_vad, monkeypatch):
    np, vad_mod = np_vad
    backend = make_backend()
    _stub_audio(np, vad_mod, monkeypatch, _SR)
    monkeypatch.setattr(backend, "_regions", lambda a, d: [])
    result = backend._recognize_sync(None, "x.wav", "de", with_timestamps=False)
    assert result.text == ""
    assert result.segments == []
    assert result.words == []
    assert result.language == "de"
    assert result.duration == pytest.approx(1.0)


# --- real model, end to end ----------------------------------------------


def _cached_snapshot() -> Path:
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import LocalEntryNotFoundError

    try:
        path = snapshot_download(_REPO, revision=_REVISION, local_files_only=True)
    except (LocalEntryNotFoundError, FileNotFoundError, OSError):
        pytest.skip(f"{_REPO} not cached; fetch it once online to run this test")
    if not _VOICE_CLIP.is_file():
        pytest.skip(f"reference clip missing at {_VOICE_CLIP}")
    return Path(path)


def _clip_as_16k_mono_wav() -> str:
    from talkies.audio import to_wav_16k_mono

    return to_wav_16k_mono(_VOICE_CLIP.read_bytes(), _VOICE_CLIP.name)


def _real_backend(snapshot: Path) -> Wav2Vec2PhonemeBackend:
    return Wav2Vec2PhonemeBackend(
        model_id=_MODEL_ID,
        repo=_REPO,
        model_path=snapshot,
        device="cpu",
    )


def test_real_model_emits_ipa_phone_stream():
    snapshot = _cached_snapshot()
    wav = _clip_as_16k_mono_wav()
    backend = _real_backend(snapshot)
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
    # eSpeak IPA, not ASCII words: some phones carry non-ASCII symbols.
    assert any(any(ord(ch) > 127 for ch in phone) for phone in phones)
    assert result.words == []
    assert backend.loaded() is False


def test_real_model_per_phone_timestamps_are_ordered():
    snapshot = _cached_snapshot()
    wav = _clip_as_16k_mono_wav()
    backend = _real_backend(snapshot)
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
    assert timed.text == plain.text  # decode is identical with or without offsets
    assert len(timed.words) > _MIN_REAL_PHONES
    assert all(set(w) == {"word", "start", "end"} for w in timed.words)
    assert all(w["end"] >= w["start"] for w in timed.words)
    starts = [w["start"] for w in timed.words]
    assert starts == sorted(starts)  # non-decreasing along the timeline
    assert timed.segments and timed.segments[0]["start"] == pytest.approx(0.0)

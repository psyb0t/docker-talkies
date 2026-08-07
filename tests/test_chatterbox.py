"""Pure-python tests for the chatterbox-turbo backend.

Exercises the voice catalog, the builtin-speaker sentinel, the traversal guard
and the reference-clip bounds without importing ``chatterbox`` or loading a
checkpoint. Real synthesis needs CUDA and a 3.9 GB snapshot, so it belongs in
the integration suite.
"""

from __future__ import annotations

import asyncio
import sys
import types
import wave
from pathlib import Path

import pytest

from talkies import config

# The dev image excludes ML dependencies imported by the eager backend factory.
_MODELS_PACKAGE = types.ModuleType("talkies.models")
_MODELS_PACKAGE.__path__ = [
    str(Path(__file__).resolve().parents[1] / "src" / "talkies" / "models")
]
sys.modules["talkies.models"] = _MODELS_PACKAGE

from talkies.models.chatterbox import (  # noqa: E402
    BUILTIN_VOICE,
    MIN_REFERENCE_SECONDS,
    ChatterboxBackend,
)

_SAMPLE_RATE = 24000
_CHANNELS = 1
_SAMPLE_WIDTH_BYTES = 2
_LONG_ENOUGH_SECONDS = MIN_REFERENCE_SECONDS + 1.0


def _write_wav(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(_SAMPLE_RATE * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(_CHANNELS)
        handle.setsampwidth(_SAMPLE_WIDTH_BYTES)
        handle.setframerate(_SAMPLE_RATE)
        handle.writeframes(b"\x00" * frames * _SAMPLE_WIDTH_BYTES)
    return path


@pytest.fixture
def voices_dir(tmp_path, monkeypatch):
    voices = tmp_path / "custom-voices"
    voices.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "CUSTOM_VOICES_DIR", voices)
    return voices


@pytest.fixture
def make_backend(tmp_path):
    def _make(*, with_conds: bool = True) -> ChatterboxBackend:
        model_path = tmp_path / "model"
        model_path.mkdir(parents=True, exist_ok=True)
        if with_conds:
            (model_path / "conds.pt").write_bytes(b"stub")
        return ChatterboxBackend(
            model_id="chatterbox-turbo",
            repo="ResembleAI/chatterbox-turbo",
            model_path=model_path,
            device="cpu",
        )

    return _make


def test_builtin_voice_exposed_when_conds_present(voices_dir, make_backend):
    backend = make_backend()
    assert backend.voices() == [BUILTIN_VOICE]
    assert backend.default_voice() == BUILTIN_VOICE
    assert backend.voice_origins() == {BUILTIN_VOICE: "builtin"}


def test_catalog_empty_and_default_raises_without_conds_or_wavs(
    voices_dir, make_backend
):
    backend = make_backend(with_conds=False)
    assert backend.voices() == []
    with pytest.raises(RuntimeError, match="no chatterbox voices"):
        backend.default_voice()


def test_missing_voices_dir_is_not_fatal(tmp_path, monkeypatch, make_backend):
    monkeypatch.setattr(config, "CUSTOM_VOICES_DIR", tmp_path / "absent")
    backend = make_backend()
    assert backend.voices() == [BUILTIN_VOICE]


def test_custom_voices_scanned_with_nested_names_preserved(
    voices_dir, make_backend
):
    _write_wav(voices_dir / "me.wav", _LONG_ENOUGH_SECONDS)
    _write_wav(voices_dir / "cast" / "villain.wav", _LONG_ENOUGH_SECONDS)
    backend = make_backend()
    assert backend.voices() == [BUILTIN_VOICE, "cast/villain", "me"]
    assert backend.voice_origins()["cast/villain"] == "custom"


def test_default_falls_back_to_first_custom_without_conds(
    voices_dir, make_backend
):
    _write_wav(voices_dir / "zeta.wav", _LONG_ENOUGH_SECONDS)
    _write_wav(voices_dir / "alpha.wav", _LONG_ENOUGH_SECONDS)
    backend = make_backend(with_conds=False)
    assert backend.default_voice() == "alpha"


def test_wav_named_builtin_cannot_shadow_the_checkpoint_speaker(
    voices_dir, make_backend
):
    _write_wav(voices_dir / f"{BUILTIN_VOICE}.wav", _LONG_ENOUGH_SECONDS)
    backend = make_backend()
    assert backend.voices() == [BUILTIN_VOICE]
    assert backend._resolve_reference_wav(BUILTIN_VOICE) is None


def test_dotfile_wavs_are_skipped(voices_dir, make_backend):
    _write_wav(voices_dir / ".hidden.wav", _LONG_ENOUGH_SECONDS)
    backend = make_backend()
    assert backend.voices() == [BUILTIN_VOICE]


def test_symlink_escaping_voices_dir_is_skipped(
    tmp_path, voices_dir, make_backend
):
    outside = _write_wav(
        tmp_path / "outside" / "leak.wav", _LONG_ENOUGH_SECONDS
    )
    (voices_dir / "leak.wav").symlink_to(outside)
    backend = make_backend()
    assert backend.voices() == [BUILTIN_VOICE]


def test_unknown_voice_rejected(voices_dir, make_backend):
    backend = make_backend()
    with pytest.raises(ValueError, match="unknown voice"):
        backend._resolve_reference_wav("nope")


@pytest.mark.parametrize(
    ("seconds", "accepted"),
    [
        (MIN_REFERENCE_SECONDS - 1.0, False),
        (MIN_REFERENCE_SECONDS, False),
        (MIN_REFERENCE_SECONDS + 0.5, True),
    ],
)
def test_reference_duration_bound_is_exclusive(
    voices_dir, make_backend, seconds, accepted
):
    wav = _write_wav(voices_dir / "ref.wav", seconds)
    backend = make_backend()
    if not accepted:
        with pytest.raises(ValueError, match="needs more than"):
            backend._resolve_reference_wav("ref")
        return
    assert backend._resolve_reference_wav("ref") == wav


def test_unreadable_reference_clip_rejected(voices_dir, make_backend):
    (voices_dir / "broken.wav").write_bytes(b"not a wav at all")
    backend = make_backend()
    with pytest.raises(ValueError, match="unreadable reference clip"):
        backend._resolve_reference_wav("broken")


def test_empty_text_rejected_before_model_load(voices_dir, make_backend):
    backend = make_backend()
    with pytest.raises(ValueError, match="input text is empty"):
        asyncio.run(
            backend.synthesize("   ", voice=BUILTIN_VOICE, speed=1.0)
        )
    assert backend.loaded() is False


def test_lifecycle_surface_before_load(voices_dir, make_backend):
    backend = make_backend()
    assert backend.loaded() is False
    assert backend.last_used_secs_ago() is None


def test_load_reports_missing_checkpoint_file(voices_dir, make_backend):
    backend = make_backend()
    with pytest.raises(FileNotFoundError, match="ve.safetensors"):
        backend._load_sync()

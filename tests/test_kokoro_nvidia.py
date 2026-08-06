"""Pure-python tests for the kokoro-nvidia backend.

Exercises file-format parsing and the voice-prefix → lang mapping without
loading the ONNX session or hitting espeak-ng. The ORT inference path is
covered by the integration suite (tests/integration/test_speech.sh).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")

from talkies.models.kokoro_nvidia import (
    _PREFIX_TO_LANG,  # noqa: E402
    VOICE_DIM,
    VOICE_TOKEN_LEN,
    KokoroNvidiaBackend,
    _load_voices,
    _parse_tokens,
    _split_phonemes,
)


def _write_voices_pair(tmp_path: Path, names: list[str]) -> tuple[Path, Path]:
    """Create a tiny voices.bin + voices.txt fixture pair matching the
    NVIDIA layout (raw f32 packed by voice, index→name txt)."""
    import numpy as np

    arr = np.arange(len(names) * VOICE_TOKEN_LEN * VOICE_DIM, dtype=np.float32).reshape(
        len(names), VOICE_TOKEN_LEN, VOICE_DIM
    )
    bin_path = tmp_path / "voices.bin"
    arr.tofile(str(bin_path))
    txt_path = tmp_path / "voices.txt"
    txt_path.write_text(
        "\n".join(f"{i}={name}" for i, name in enumerate(names)) + "\n",
        encoding="utf-8",
    )
    return bin_path, txt_path


def test_parse_tokens_handles_space_phoneme(tmp_path: Path) -> None:
    # The space phoneme is itself a literal space — the line is "  16".
    # rpartition(" ", 1) returns ("", " ", "16"), so the parser has to fall
    # back to the first char as the key.
    p = tmp_path / "tokens.txt"
    p.write_text("a 43\n  16\nɹ 123\n", encoding="utf-8")
    vocab = _parse_tokens(p)
    assert vocab["a"] == 43
    assert vocab[" "] == 16
    assert vocab["ɹ"] == 123


def test_parse_tokens_rejects_garbage(tmp_path: Path) -> None:
    p = tmp_path / "tokens.txt"
    p.write_text("a notanumber\n", encoding="utf-8")
    with pytest.raises(ValueError):
        _parse_tokens(p)


def test_load_voices_round_trip(tmp_path: Path) -> None:
    names = ["af_heart", "bm_george", "zf_xiaoxiao"]
    bin_path, txt_path = _write_voices_pair(tmp_path, names)
    index, arr = _load_voices(bin_path, txt_path)
    assert index == {"af_heart": 0, "bm_george": 1, "zf_xiaoxiao": 2}
    assert arr.shape == (3, VOICE_TOKEN_LEN, VOICE_DIM)
    # First voice starts at 0.0, second voice starts at VOICE_TOKEN_LEN*VOICE_DIM.
    assert arr[0, 0, 0] == 0.0
    assert arr[1, 0, 0] == float(VOICE_TOKEN_LEN * VOICE_DIM)


def test_load_voices_size_mismatch(tmp_path: Path) -> None:
    import numpy as np

    bin_path = tmp_path / "voices.bin"
    # Write half the expected bytes — should fail-loud.
    arr = np.zeros(VOICE_TOKEN_LEN * VOICE_DIM // 2, dtype=np.float32)
    arr.tofile(str(bin_path))
    txt_path = tmp_path / "voices.txt"
    txt_path.write_text("0=af_heart\n", encoding="utf-8")
    with pytest.raises(ValueError, match="size mismatch|expected"):
        _load_voices(bin_path, txt_path)


def test_scan_voices_filters_zh_ja(tmp_path: Path) -> None:
    # voices.txt with a mix of supported + unsupported prefixes — the
    # backend should only expose the ones it has espeak lang codes for.
    txt = tmp_path / "voices.txt"
    txt.write_text(
        "0=af_heart\n1=zm_yunjian\n2=jf_alpha\n3=pm_alex\n4=hm_omega\n",
        encoding="utf-8",
    )
    backend = KokoroNvidiaBackend(
        model_id="kokoro-82m-nvidia",
        repo="nvidia/kokoro-82M-onnx-opt",
        model_path=tmp_path,
        device="cpu",
    )
    voices = backend.voices()
    assert voices == ["af_heart", "pm_alex", "hm_omega"]


def test_default_voice_falls_back_when_default_missing(
    tmp_path: Path,
) -> None:
    txt = tmp_path / "voices.txt"
    txt.write_text("0=am_adam\n1=bf_emma\n", encoding="utf-8")
    backend = KokoroNvidiaBackend(
        model_id="kokoro-82m-nvidia",
        repo="nvidia/kokoro-82M-onnx-opt",
        model_path=tmp_path,
        device="cpu",
    )
    # af_heart isn't in this catalog — falls back to first entry.
    assert backend.default_voice() == "am_adam"


def test_default_voice_raises_when_catalog_empty(tmp_path: Path) -> None:
    # No voices.txt at all → snapshot wasn't prefetched.
    backend = KokoroNvidiaBackend(
        model_id="kokoro-82m-nvidia",
        repo="nvidia/kokoro-82M-onnx-opt",
        model_path=tmp_path,
        device="cpu",
    )
    with pytest.raises(RuntimeError, match="no voices found"):
        backend.default_voice()


def test_prefix_to_lang_covers_all_supported_prefixes() -> None:
    # Sanity: every prefix in the table maps to a valid espeak-ng lang
    # code. We don't talk to espeak here, just verify the constants are
    # well-formed (no empty values, no typos in form).
    for prefix, lang in _PREFIX_TO_LANG.items():
        assert prefix.endswith("_") and len(prefix) == 3
        assert lang and "-" not in lang.split("-")[0]


def test_split_phonemes_short_passthrough() -> None:
    out = _split_phonemes("hello world", max_len=100)
    assert out == ["hello world"]


def test_split_phonemes_breaks_at_punctuation() -> None:
    # Build a string longer than max_len with a punctuation boundary.
    text = ("a" * 30) + "." + ("b" * 30)
    chunks = _split_phonemes(text, max_len=40)
    assert len(chunks) == 2
    # First chunk should end with the punctuation that produced the break.
    assert chunks[0].endswith(".")
    assert chunks[1] == "b" * 30


def test_split_phonemes_handles_no_punctuation() -> None:
    # A long run with no punctuation should still produce at least one
    # chunk and not loop forever.
    text = "a" * 200
    chunks = _split_phonemes(text, max_len=50)
    assert chunks
    assert all(len(c) <= 200 for c in chunks)

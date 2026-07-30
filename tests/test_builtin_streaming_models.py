"""Built-in native streaming model registry tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_REGISTRIES = ("models-cpu.json", "models.json")
_SHERPA_SLUGS = {
    "sherpa-zipformer-en-left-64": "left-64.onnx",
    "sherpa-zipformer-en-left-128": "left-128.onnx",
    "sherpa-zipformer-en-int8-left-64": "left-64.int8.onnx",
    "sherpa-zipformer-en-int8-left-128": "left-128.int8.onnx",
}
_SHERPA_REPO = "csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26"
_SHERPA_REVISION = "672fbf1b30579d6585301139bb363f42a0ad4a24"
_VOSK_SLUG = "vosk-small-en-us-0.15"
_VOSK_REPO = "aglaia-models/vosk-model-small-en-us-0.15"
_VOSK_REVISION = "0aac829e440a7c8bd30f674a17d9a6d1dbdbbf3a"


@pytest.mark.parametrize("registry_name", _REGISTRIES)
def test_builtin_sherpa_variants_have_minimal_pinned_downloads(
    registry_name: str,
) -> None:
    registry = json.loads((_ROOT / registry_name).read_text())["models"]

    for slug, variant_suffix in _SHERPA_SLUGS.items():
        entry = registry[slug]
        config = entry["sherpa_config"]

        assert entry["repo"] == _SHERPA_REPO
        assert entry["revision"] == _SHERPA_REVISION
        assert entry["executor"] == "sherpa"
        assert entry["recognizer_factory"] == "from_transducer"
        assert entry["languages"] == ["en"]
        assert config["tokens"] == "tokens.txt"
        assert config["encoder"].endswith(variant_suffix)
        assert config["decoder"].endswith(variant_suffix)
        assert config["joiner"].endswith(variant_suffix)
        assert set(entry["download_patterns"]) == {
            "tokens.txt",
            config["encoder"],
            config["decoder"],
            config["joiner"],
        }


@pytest.mark.parametrize("registry_name", _REGISTRIES)
def test_builtin_vosk_model_is_pinned_and_selectable(registry_name: str) -> None:
    registry = json.loads((_ROOT / registry_name).read_text())["models"]
    entry = registry[_VOSK_SLUG]

    assert entry == {
        "repo": _VOSK_REPO,
        "revision": _VOSK_REVISION,
        "executor": "vosk",
        "languages": ["en"],
    }

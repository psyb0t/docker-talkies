"""Unit tests for talkies.config — env parsing + load_registry() filtering.

These hit pure-python paths only; no ML deps required. They reload the
config module under different env-var setups, so each test patches the
environment and forces a fresh import.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


def _reload_config(monkeypatch, models_path: Path, **env: str):
    """Reload talkies.config with a specific MODELS_FILE + env. Returns the module."""
    monkeypatch.setenv("TALKIES_MODELS_FILE", str(models_path))
    # Reset everything that load_registry cares about so previous tests
    # can't leak filters into this one.
    for var in (
        "TALKIES_ENABLED_MODELS",
        "TALKIES_PRELOAD",
        "TALKIES_MODEL_MAX_CONCURRENCY",
        "TALKIES_MODEL_CONCURRENCY",
        "TALKIES_STREAM_MAX_CONNECTIONS",
        "TALKIES_STREAM_MAX_FRAME_BYTES",
        "TALKIES_STREAM_MAX_BUFFER_SECONDS",
        "TALKIES_STREAM_IDLE_TIMEOUT",
        "TALKIES_STREAM_MAX_DURATION",
    ):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("talkies.config", None)
    return importlib.import_module("talkies.config")


@pytest.fixture
def fake_registry(tmp_path: Path) -> Path:
    """Write a minimal but valid models.json that mirrors the real schema."""
    p = tmp_path / "models.json"
    p.write_text(
        json.dumps(
            {
                "models": {
                    "whisper-tiny": {
                        "repo": "openai/whisper-tiny",
                        "executor": "whisper",
                    },
                    "parakeet-mini": {
                        "repo": "nvidia/parakeet-mini",
                        "executor": "parakeet",
                    },
                    "canary-tiny": {
                        "repo": "nvidia/canary-tiny",
                        "executor": "canary_multitask",
                        "default_task": "asr",
                        "default_source_lang": "en",
                        "default_target_lang": "en",
                    },
                }
            }
        )
    )
    return p


# ── ENABLED_MODELS env parsing ───────────────────────────────────────────────


def test_enabled_models_empty_means_all(monkeypatch, fake_registry):
    cfg = _reload_config(monkeypatch, fake_registry)
    assert cfg.ENABLED_MODELS == []
    reg = cfg.load_registry()
    assert set(reg) == {"whisper-tiny", "parakeet-mini", "canary-tiny"}


def test_enabled_models_filters_registry(monkeypatch, fake_registry):
    cfg = _reload_config(
        monkeypatch,
        fake_registry,
        TALKIES_ENABLED_MODELS="whisper-tiny,canary-tiny",
    )
    assert cfg.ENABLED_MODELS == ["whisper-tiny", "canary-tiny"]
    reg = cfg.load_registry()
    assert set(reg) == {"whisper-tiny", "canary-tiny"}
    # Filtered-out slug must not survive the filter
    assert "parakeet-mini" not in reg


def test_enabled_models_preserves_order(monkeypatch, fake_registry):
    cfg = _reload_config(
        monkeypatch,
        fake_registry,
        TALKIES_ENABLED_MODELS="canary-tiny,whisper-tiny",
    )
    reg = cfg.load_registry()
    assert list(reg) == ["canary-tiny", "whisper-tiny"]


def test_enabled_models_trims_whitespace_and_blanks(monkeypatch, fake_registry):
    cfg = _reload_config(
        monkeypatch,
        fake_registry,
        TALKIES_ENABLED_MODELS=" whisper-tiny , , canary-tiny ,",
    )
    assert cfg.ENABLED_MODELS == ["whisper-tiny", "canary-tiny"]


def test_enabled_models_unknown_slug_fails_fast(monkeypatch, fake_registry):
    cfg = _reload_config(
        monkeypatch,
        fake_registry,
        TALKIES_ENABLED_MODELS="whisper-tiny,does-not-exist",
    )
    with pytest.raises(ValueError, match="does-not-exist"):
        cfg.load_registry()


def test_enabled_models_all_unknown_fails_fast(monkeypatch, fake_registry):
    cfg = _reload_config(
        monkeypatch,
        fake_registry,
        TALKIES_ENABLED_MODELS="nope-a,nope-b",
    )
    with pytest.raises(ValueError, match=r"nope-a.*nope-b|nope-b.*nope-a"):
        cfg.load_registry()


# ── load_registry schema validation (unchanged behavior, still covered) ──────


def test_load_registry_missing_file_raises(monkeypatch, tmp_path):
    missing = tmp_path / "no-such-file.json"
    cfg = _reload_config(monkeypatch, missing)
    with pytest.raises(FileNotFoundError):
        cfg.load_registry()


def test_load_registry_bad_top_level_raises(monkeypatch, tmp_path):
    p = tmp_path / "models.json"
    p.write_text(json.dumps(["not", "an", "object"]))
    cfg = _reload_config(monkeypatch, p)
    with pytest.raises(ValueError, match="top-level"):
        cfg.load_registry()


def test_load_registry_unknown_executor_raises(monkeypatch, tmp_path):
    p = tmp_path / "models.json"
    p.write_text(
        json.dumps({"models": {"x": {"repo": "foo/bar", "executor": "telepathy"}}})
    )
    cfg = _reload_config(monkeypatch, p)
    with pytest.raises(ValueError, match="telepathy"):
        cfg.load_registry()


def test_load_registry_missing_repo_raises(monkeypatch, tmp_path):
    p = tmp_path / "models.json"
    p.write_text(json.dumps({"models": {"x": {"executor": "whisper"}}}))
    cfg = _reload_config(monkeypatch, p)
    with pytest.raises(ValueError, match="missing 'repo'"):
        cfg.load_registry()


def test_model_concurrency_uses_fallback_registry_and_override_precedence(
    monkeypatch,
    tmp_path,
):
    registry_path = tmp_path / "models.json"
    registry_path.write_text(
        json.dumps(
            {
                "models": {
                    "fallback": {"repo": "example/fallback"},
                    "registry": {
                        "repo": "example/registry",
                        "max_concurrency": 2,
                    },
                }
            }
        )
    )
    cfg = _reload_config(
        monkeypatch,
        registry_path,
        TALKIES_MODEL_MAX_CONCURRENCY="3",
        TALKIES_MODEL_CONCURRENCY="registry=4",
    )

    registry = cfg.load_registry()

    assert registry["fallback"]["max_concurrency"] == 3
    assert registry["registry"]["max_concurrency"] == 4


@pytest.mark.parametrize("value", [True, 0, -1, 1025, 1.5, "2"])
def test_registry_rejects_invalid_model_concurrency(
    monkeypatch,
    tmp_path,
    value,
):
    registry_path = tmp_path / "models.json"
    registry_path.write_text(
        json.dumps(
            {
                "models": {
                    "bad-model": {
                        "repo": "example/bad-model",
                        "max_concurrency": value,
                    }
                }
            }
        )
    )
    cfg = _reload_config(monkeypatch, registry_path)

    with pytest.raises(ValueError, match="bad-model.*max_concurrency"):
        cfg.load_registry()


@pytest.mark.parametrize(
    "value",
    [
        "missing",
        "=2",
        "whisper-tiny=",
        "whisper-tiny=two",
        "whisper-tiny=0",
        "whisper-tiny=1025",
        "whisper-tiny=2,whisper-tiny=3",
        "missing-model=2",
        "whisper-tiny=2=3",
    ],
)
def test_model_concurrency_override_rejects_invalid_values(
    monkeypatch,
    fake_registry,
    value,
):
    cfg = _reload_config(
        monkeypatch,
        fake_registry,
        TALKIES_MODEL_CONCURRENCY=value,
    )

    with pytest.raises(ValueError, match="TALKIES_MODEL_CONCURRENCY"):
        cfg.load_registry()


def test_disabled_model_cannot_be_overridden(monkeypatch, fake_registry):
    cfg = _reload_config(
        monkeypatch,
        fake_registry,
        TALKIES_ENABLED_MODELS="whisper-tiny",
        TALKIES_MODEL_CONCURRENCY="parakeet-mini=2",
    )

    with pytest.raises(ValueError, match="unknown or disabled"):
        cfg.load_registry()


@pytest.mark.parametrize("value", ["0", "1025", "not-a-number"])
def test_model_concurrency_fallback_rejects_invalid_values(
    monkeypatch,
    fake_registry,
    value,
):
    monkeypatch.setenv("TALKIES_MODELS_FILE", str(fake_registry))
    monkeypatch.setenv("TALKIES_MODEL_MAX_CONCURRENCY", value)
    sys.modules.pop("talkies.config", None)

    with pytest.raises(ValueError, match="TALKIES_MODEL_MAX_CONCURRENCY"):
        importlib.import_module("talkies.config")


def test_model_concurrency_override_rejects_oversized_text(
    monkeypatch,
    fake_registry,
):
    monkeypatch.setenv("TALKIES_MODELS_FILE", str(fake_registry))
    monkeypatch.setenv("TALKIES_MODEL_CONCURRENCY", "x" * 65537)
    sys.modules.pop("talkies.config", None)

    with pytest.raises(ValueError, match="65536-byte"):
        importlib.import_module("talkies.config")


# ── duration parser smoke ─────────────────────────────────────────────────────


def test_duration_env_accepts_bare_seconds(monkeypatch, fake_registry):
    cfg = _reload_config(monkeypatch, fake_registry, TALKIES_MODEL_TTL="120")
    assert cfg.MODEL_IDLE_TIMEOUT_SECONDS == 120.0


def test_duration_env_accepts_go_style(monkeypatch, fake_registry):
    cfg = _reload_config(monkeypatch, fake_registry, TALKIES_MODEL_TTL="1h30m5s")
    assert cfg.MODEL_IDLE_TIMEOUT_SECONDS == 3600 + 30 * 60 + 5


def test_duration_env_rejects_garbage(monkeypatch, fake_registry):
    monkeypatch.setenv("TALKIES_MODELS_FILE", str(fake_registry))
    monkeypatch.setenv("TALKIES_MODEL_TTL", "yesterday")
    sys.modules.pop("talkies.config", None)
    with pytest.raises(ValueError, match="TALKIES_MODEL_TTL"):
        importlib.import_module("talkies.config")


def test_device_rejects_garbage(monkeypatch, fake_registry):
    monkeypatch.setenv("TALKIES_MODELS_FILE", str(fake_registry))
    monkeypatch.setenv("TALKIES_DEVICE", "potato")
    sys.modules.pop("talkies.config", None)
    with pytest.raises(ValueError, match="TALKIES_DEVICE"):
        importlib.import_module("talkies.config")


def test_device_accepts_cuda_n(monkeypatch, fake_registry):
    cfg = _reload_config(monkeypatch, fake_registry, TALKIES_DEVICE="cuda:1")
    assert cfg.DEVICE == "cuda:1"


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("TALKIES_STREAM_MAX_CONNECTIONS", "0"),
        ("TALKIES_STREAM_MAX_FRAME_BYTES", "1"),
        ("TALKIES_STREAM_MAX_BUFFER_SECONDS", "0"),
        ("TALKIES_STREAM_IDLE_TIMEOUT", "0s"),
        ("TALKIES_STREAM_MAX_DURATION", "25h"),
    ),
)
def test_stream_limits_reject_out_of_range_values(
    monkeypatch,
    fake_registry,
    name,
    value,
):
    monkeypatch.setenv("TALKIES_MODELS_FILE", str(fake_registry))
    monkeypatch.setenv(name, value)
    sys.modules.pop("talkies.config", None)
    with pytest.raises(ValueError, match=name):
        importlib.import_module("talkies.config")


def test_stream_limits_accept_documented_values(monkeypatch, fake_registry):
    cfg = _reload_config(
        monkeypatch,
        fake_registry,
        TALKIES_STREAM_MAX_CONNECTIONS="8",
        TALKIES_STREAM_MAX_FRAME_BYTES="32768",
        TALKIES_STREAM_MAX_BUFFER_SECONDS="2.5",
        TALKIES_STREAM_IDLE_TIMEOUT="45s",
        TALKIES_STREAM_MAX_DURATION="2h",
    )
    assert cfg.STREAM_MAX_CONNECTIONS == 8
    assert cfg.STREAM_MAX_FRAME_BYTES == 32768
    assert cfg.STREAM_MAX_BUFFER_SECONDS == 2.5
    assert cfg.STREAM_IDLE_TIMEOUT_SECONDS == 45.0
    assert cfg.STREAM_MAX_DURATION_SECONDS == 7200.0


def test_stream_buffer_must_hold_one_maximum_frame(monkeypatch, fake_registry):
    monkeypatch.setenv("TALKIES_MODELS_FILE", str(fake_registry))
    monkeypatch.setenv("TALKIES_STREAM_MAX_FRAME_BYTES", "65536")
    monkeypatch.setenv("TALKIES_STREAM_MAX_BUFFER_SECONDS", "0.1")
    sys.modules.pop("talkies.config", None)
    with pytest.raises(ValueError, match="must hold at least one"):
        importlib.import_module("talkies.config")


# --- shipped-registry contract -----------------------------------------------
# The unit tests above all run against synthetic fixtures, so nothing here
# loaded the registries that actually ship in the images. That gap let a model
# declare an executor the validator rejected: the slug, the backend and the
# factory branch were all correct, but VALID_EXECUTORS never got the entry, and
# load_registry() runs at server import time — so the image died on startup
# with every model, not just the new one. These two tests close that gap for
# every future model addition.

_SHIPPED_REGISTRIES = ("models.json", "models-cpu.json")


def _registry_path(name: str) -> Path:
    return Path(__file__).resolve().parent.parent / name


@pytest.mark.parametrize("registry_name", _SHIPPED_REGISTRIES)
def test_shipped_registry_executors_are_all_valid(monkeypatch, registry_name):
    path = _registry_path(registry_name)
    cfg = _reload_config(monkeypatch, path)
    declared = {
        entry.get("executor", "whisper")
        for entry in json.loads(path.read_text(encoding="utf-8"))["models"].values()
    }
    # Without this the test passes vacuously if the registry schema ever moves
    # the entries out from under the "models" key — an empty set has no unknowns.
    assert declared, f"{registry_name} declared no executors — schema changed?"
    unknown = declared - set(cfg.VALID_EXECUTORS)
    assert not unknown, (
        f"{registry_name} declares executor(s) {sorted(unknown)} missing from "
        f"config.VALID_EXECUTORS — load_registry() would reject the whole file "
        f"at server import, taking every model down with it"
    )


@pytest.mark.parametrize("registry_name", _SHIPPED_REGISTRIES)
def test_shipped_registry_loads(monkeypatch, registry_name):
    path = _registry_path(registry_name)
    cfg = _reload_config(monkeypatch, path)
    registry = cfg.load_registry()
    assert registry, f"{registry_name} loaded empty"

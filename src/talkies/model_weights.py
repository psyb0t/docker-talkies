from __future__ import annotations

import argparse
import fcntl
import logging
import tempfile
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)
_WEIGHT_SUFFIXES = frozenset(
    {".bin", ".ckpt", ".gguf", ".mdl", ".nemo", ".onnx", ".pt", ".pth", ".safetensors"}
)


def _patterns(entry: dict[str, Any]) -> list[str] | None:
    patterns = entry.get("download_patterns")
    if patterns is None:
        return None
    if (
        not isinstance(patterns, list)
        or not patterns
        or any(not isinstance(pattern, str) or not pattern for pattern in patterns)
    ):
        raise ValueError("download_patterns must be a nonempty list of paths")
    return patterns


def _nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _has_weights(target: Path, entry: dict[str, Any]) -> bool:
    patterns = _patterns(entry)
    if patterns is None and entry.get("executor") == "kokoro":
        patterns = ["config.json", "kokoro-v1_0.pth", "voices/*.pt"]
    if patterns is None and entry.get("executor") == "kokoro_nvidia":
        patterns = ["kokoro-82m-v1.0.onnx", "tokens.txt", "voices.bin", "voices.txt"]
    if patterns is not None:
        return all(
            any(_nonempty_file(path) for path in target.glob(pattern))
            for pattern in patterns
        )
    if gguf := entry.get("gguf_file"):
        return _nonempty_file(target / gguf)
    return any(
        path.suffix in _WEIGHT_SUFFIXES
        and not any(part.startswith(".") for part in path.relative_to(target).parts)
        and _nonempty_file(path)
        for path in target.rglob("*")
    )


def _dependency_cached(repo: str) -> bool:
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        snapshot_download(repo, local_files_only=True)
    except LocalEntryNotFoundError:
        return False
    return True


def weights_cached(target: Path, entry: dict[str, Any]) -> bool:
    return _has_weights(target, entry) and all(
        _dependency_cached(repo) for repo in (entry.get("dependencies") or [])
    )


def download_model(root: Path, slug: str, entry: dict[str, Any]) -> None:
    if not slug or Path(slug).name != slug or slug in (".", ".."):
        raise ValueError("invalid model slug")
    target = root / slug
    if weights_cached(target, entry):
        return
    root.mkdir(parents=True, exist_ok=True)
    with (root / f".{slug}.download.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not _has_weights(target, entry):
            if target.exists():
                _download_snapshot(target, entry)
            else:
                with tempfile.TemporaryDirectory(dir=root, prefix=".download-") as temp:
                    staged = Path(temp) / slug
                    _download_snapshot(staged, entry)
                    staged.rename(target)
        for dependency in entry.get("dependencies") or []:
            if not _dependency_cached(dependency):
                from huggingface_hub import snapshot_download

                snapshot_download(dependency)


def _download_snapshot(target: Path, entry: dict[str, Any]) -> None:
    from huggingface_hub import snapshot_download

    patterns = _patterns(entry)
    if patterns is None and (gguf := entry.get("gguf_file")):
        patterns = [gguf, "README.md", "LICENSE", "config.json"]
    log.info("downloading model weights", extra={"model": target.name})
    snapshot_download(
        entry["repo"],
        revision=entry.get("revision"),
        local_dir=target,
        allow_patterns=patterns,
    )
    if not _has_weights(target, entry):
        raise FileNotFoundError(f"model snapshot incomplete for {target.name}")


def main() -> None:
    from .logging import configure

    parser = argparse.ArgumentParser(
        description="Download weights for enabled Talkies models."
    )
    parser.add_argument("model", nargs="?", help="Download only this enabled model")
    options = parser.parse_args()
    configure()
    registry = config.load_registry()
    if options.model is not None:
        if options.model not in registry:
            parser.error("model is unavailable")
        download_model(config.MODELS_DIR, options.model, registry[options.model])
        return
    for slug, entry in registry.items():
        download_model(config.MODELS_DIR, slug, entry)


if __name__ == "__main__":
    main()

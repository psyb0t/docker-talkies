from pathlib import Path
from typing import Any

from talkies import model_weights


def test_cached_weights_skip_downloader(tmp_path: Path) -> None:
    target = tmp_path / "model"
    target.mkdir()
    (target / "model.onnx").write_bytes(b"cached")

    model_weights.download_model(tmp_path, "model", {"repo": "example/model"})

    assert (target / "model.onnx").read_bytes() == b"cached"
    assert set(tmp_path.iterdir()) == {target}


def test_prefetch_repairs_missing_companion_files(tmp_path: Path, monkeypatch: Any) -> None:
    target = tmp_path / "model"
    target.mkdir()
    (target / "model.onnx").write_bytes(b"cached")
    entry = {"repo": "example/model", "download_patterns": ["model.onnx", "tokens.txt"]}
    downloads = []

    def download_snapshot(path: Path, registry_entry: dict[str, Any]) -> None:
        downloads.append((path, registry_entry))
        (path / "tokens.txt").write_text("token", encoding="utf-8")

    monkeypatch.setattr(model_weights, "_download_snapshot", download_snapshot)
    model_weights.download_model(tmp_path, "model", entry)
    model_weights.download_model(tmp_path, "model", entry)

    assert downloads == [(target, entry)]
    assert (target / "model.onnx").read_bytes() == b"cached"
    assert model_weights.weights_cached(target, entry)

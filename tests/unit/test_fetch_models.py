from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("fetch_models", ROOT / "scripts" / "fetch_models.py")
fm = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["fetch_models"] = fm
spec.loader.exec_module(fm)  # type: ignore[union-attr]


def test_repo_lock_parses_and_has_licences() -> None:
    entries = fm.parse_lock(ROOT / "config" / "models.lock")
    names = {e.name for e in entries}
    assert {
        "face_detection_yunet_2023mar.onnx",
        "face_recognition_sface_2021dec.onnx",
        "ediffiqa_tiny_jun2024.onnx",
        "ocec_m.onnx",
        "brisque_model_live.yml",
        "brisque_range_live.yml",
    } <= names
    assert all(e.licence for e in entries) and all(e.url.startswith("https://") for e in entries)


def test_bad_lock_lines_rejected(tmp_path: Path) -> None:
    p = tmp_path / "l"
    p.write_text("a.onnx\thttps://x\tnothex\tMIT\n")
    with pytest.raises(ValueError):
        fm.parse_lock(p)
    p.write_text("../a.onnx\thttps://x\t" + "0" * 64 + "\tMIT\n")
    with pytest.raises(ValueError):
        fm.parse_lock(p)


def test_existing_good_file_is_not_downloaded_and_bad_hash_fails_check(tmp_path: Path) -> None:
    data = b"model-bytes"
    (tmp_path / "m.onnx").write_bytes(data)
    good = fm.LockEntry("m.onnx", "https://invalid.example/m.onnx", hashlib.sha256(data).hexdigest(), "MIT")
    assert fm.fetch([good], tmp_path) == 0  # no network touched: hash already matches
    bad = fm.LockEntry("m.onnx", "https://invalid.example/m.onnx", "0" * 64, "MIT")
    assert fm.fetch([bad], tmp_path, check_only=True) == 1

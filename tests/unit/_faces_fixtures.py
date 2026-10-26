"""Shared helpers for the face unit tests (synthetic (machine-generated) faces, see tests/fixtures/faces/LICENCES.md)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from keepframe.tools._face_defaults import models_available

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "faces"
OPEN = ["open_a", "open_b", "open_c"]
CLOSED = ["closed_a", "closed_b"]

needs_models = pytest.mark.skipif(not models_available(), reason="run `python scripts/fetch_models.py` first")


def load(name: str) -> np.ndarray:
    img = cv2.imread(str(FIXTURES / f"{name}.jpg"), cv2.IMREAD_COLOR)
    assert img is not None, name
    return img


def group_photo(names: list[str], tile: int = 512) -> np.ndarray:
    """Side-by-side canvas of fixtures (each resized to `tile`)."""
    return cv2.hconcat([cv2.resize(load(n), (tile, tile)) for n in names])

"""Threshold and reason-code configuration (config/thresholds.yaml, config/reason_codes.yaml).

Every number the tools and policies use lives in thresholds.yaml; nothing is hardcoded elsewhere.
`thresholds_sha()` is recorded in every run summary and trace header.

Override the file with $CULL_THRESHOLDS (path) for tuning experiments.
"""

from __future__ import annotations

import copy
import hashlib
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("CULL_CONFIG_DIR", REPO_ROOT / "config"))


def thresholds_path() -> Path:
    return Path(os.environ.get("CULL_THRESHOLDS", CONFIG_DIR / "thresholds.yaml"))


def reason_codes_path() -> Path:
    return CONFIG_DIR / "reason_codes.yaml"


@lru_cache(maxsize=4)
def _load(path: str) -> tuple[bytes, dict[str, Any]]:
    raw = Path(path).read_bytes()
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return raw, data


def thresholds() -> dict[str, Any]:
    """Return a deep copy of the thresholds mapping (callers may not mutate the cache)."""
    return copy.deepcopy(_load(str(thresholds_path()))[1])


def section(name: str) -> dict[str, Any]:
    """One section of thresholds.yaml, e.g. section('sharpness')."""
    data = _load(str(thresholds_path()))[1]
    if name not in data:
        raise KeyError(f"thresholds.yaml has no section {name!r}")
    return copy.deepcopy(data[name])


def thresholds_sha() -> str:
    """sha256 of the exact thresholds.yaml bytes in use."""
    return hashlib.sha256(_load(str(thresholds_path()))[0]).hexdigest()


def reason_codes() -> dict[str, Any]:
    return copy.deepcopy(_load(str(reason_codes_path()))[1]["codes"])


def models_dir() -> Path:
    """$CULL_MODELS_DIR, else /opt/models (container, scripts/fetch_models.py), else <repo>/models."""
    env = os.environ.get("CULL_MODELS_DIR")
    if env:
        return Path(env)
    opt = Path("/opt/models")
    if opt.is_dir() and any(opt.iterdir()):
        return opt
    return REPO_ROOT / "models"


def model_path(name: str) -> Path:
    """Path of a model file named as in config/models.lock (may not exist)."""
    return models_dir() / name


def clear_cache() -> None:
    _load.cache_clear()

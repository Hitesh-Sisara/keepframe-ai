"""Shared fixtures for core CV integration tests (synthetic images only)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("KEEPFRAME_IMPORT_LOG", "0")

TEST_SIZE = (1066, 1600)  # > 1024 so the proxy resize path runs; smaller than prod for speed


@pytest.fixture(autouse=True)
def _isolate_store(monkeypatch: pytest.MonkeyPatch):
    """Every test starts with no cached store / contexts / S3 module double, whatever ran before it."""
    from keepframe.io import proxies
    from keepframe.tools.ingest import reset_contexts

    monkeypatch.setenv("CULL_DISABLE_DDB", "1")  # tests never write DynamoDB
    proxies.reset_store()
    reset_contexts()
    yield
    proxies.reset_store()
    reset_contexts()


@pytest.fixture()
def local_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """LOCAL_MODE store under tmp_path/root, cache under tmp_path/cache."""
    from keepframe.io import proxies
    from keepframe.tools.ingest import reset_contexts

    monkeypatch.delenv("WORK_BUCKET", raising=False)
    monkeypatch.setenv("CULL_LOCAL_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("CULL_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "root").mkdir()
    proxies.reset_store()
    reset_contexts()
    yield tmp_path / "root"
    proxies.reset_store()
    reset_contexts()


@pytest.fixture(scope="session")
def _synth_trees(tmp_path_factory: pytest.TempPathFactory):
    cache: dict[str, Path] = {}

    def build(event_id: str) -> Path:
        if event_id not in cache:
            from keepframe.worker.synth import make_event

            d = tmp_path_factory.mktemp(f"synth_{event_id}")
            make_event(d, event_id, size=TEST_SIZE, webp_quality=85)
            cache[event_id] = d
        return cache[event_id]

    return build


@pytest.fixture()
def synth_event(local_env: Path, _synth_trees):
    """Copy a prebuilt synthetic event into the per-test local root: synth_event(event_id) -> root."""

    def make(event_id: str = "ev_t") -> Path:
        shutil.copytree(_synth_trees(event_id), local_env, dirs_exist_ok=True)
        return local_env

    return make

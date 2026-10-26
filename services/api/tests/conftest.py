from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

JUDGE = "judge-token-synthetic-0123456789"
TEAM = "team-token-synthetic-0123456789ab"


def _reset() -> None:
    from app import aliases, aws, backend, config, data
    from app.auth import token_cache

    config.settings.cache_clear()
    aliases.registry.clear()
    aliases.alias_key.clear()
    data.clear_meta_cache()
    aws.reset_clients()
    backend.reset_backend()
    token_cache.clear()


@pytest.fixture(scope="session")
def _built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from tests.fixtures.make_fixtures import build

    out = tmp_path_factory.mktemp("fixture") / "results"
    build(out)
    return out


@pytest.fixture()
def root(_built: Path, tmp_path: Path) -> Path:
    """A fresh copy per test, so tests that write (runs, approvals) never leak into each other."""
    out = tmp_path / "results"
    shutil.copytree(_built, out)
    return out


@pytest.fixture()
def client(root: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("LOCAL_MODE", "1")
    monkeypatch.setenv("CULL_LOCAL_ROOT", str(root))
    monkeypatch.setenv("CULL_JUDGE_TOKEN", JUDGE)
    monkeypatch.setenv("CULL_TEAM_TOKEN", TEAM)
    monkeypatch.setenv("CULL_LOCAL_PUBLIC_BASE", "http://testserver")
    for name in (
        "CULL_STATE_MACHINE_ARN",
        "CULL_AGENT_RUNTIME_ARN",
        "CULL_AGENT_URL",
        "CULL_SHOW_EVENT_NAMES",
        "CULL_ALIAS_KEY",
        "CULL_ALIAS_TEAM",
        "CULL_PUBLIC_BASE",
    ):
        monkeypatch.delenv(name, raising=False)
    _reset()
    import importlib

    import app.main

    importlib.reload(app.main)  # LOCAL_MODE gates the local object route at import
    with TestClient(app.main.app) as c:
        yield c
    _reset()
    shutil.rmtree(root, ignore_errors=True)


def judge() -> dict[str, str]:
    return {"Authorization": f"Bearer {JUDGE}"}


def team() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEAM}"}


def ph(event_id: str, photo_id: str) -> str:
    """The client-facing alias of a fixture photo (local-mode alias key)."""
    from app.aliases import photo_alias

    return photo_alias(event_id, photo_id)

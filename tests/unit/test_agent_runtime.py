"""services/agent_runtime/app.py: /ping and /invocations start -> paused -> resume -> done (stub + fake)."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

from keepframe.io import proxies
from keepframe.store import dynamodb

APP = Path(__file__).resolve().parents[2] / "services" / "agent_runtime" / "app.py"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    for k, v in {"CULL_LOCAL_ROOT": str(tmp_path), "CULL_TOOLS": "stub", "CULL_MODEL_PROVIDER": "fake",
                 "CULL_METRICS": "off", "CULL_TRACE_SINKS": "ddb,s3", "CULL_DDB": "local"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("WORK_BUCKET", raising=False)
    proxies.reset_store()
    dynamodb.reset_backend()
    spec = importlib.util.spec_from_file_location("agent_runtime_app", APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    with TestClient(mod.app) as c:
        yield c
    proxies.reset_store()
    dynamodb.reset_backend()


def test_ping_and_full_cycle(client) -> None:
    assert client.get("/ping").json()["status"] == "Healthy"
    r = client.post("/invocations", json={"mode": "start", "run_id": "run_rt", "demo": True, "wait": True}).json()
    assert r["status"] == "paused" and r["pending_approvals"][0]["items"][0]["group_id"] == "g_412"
    r = client.post("/invocations", json={
        "mode": "resume", "run_id": "run_rt", "demo": True,
        "decisions": [{"group_id": "g_412", "human_verdict": {"keep": ["p_8831"],
                                                              "reject": ["p_8830", "p_8832", "p_8833"]}}]}).json()
    assert r["status"] == "running"
    for _ in range(100):
        st = client.post("/invocations", json={"mode": "status", "run_id": "run_rt"}).json()
        if st["status"] != "running":
            break
        time.sleep(0.1)
    assert st["status"] == "done" and st["summary"]["decided"] == 5
    it = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "PHOTO#p_8831")
    assert it["decided_by"] == "scripted" and it["human_source"] == "scripted"  # demo payloads are scripted


def test_rejects_bad_input(client) -> None:
    assert client.post("/invocations", json={"mode": "start", "run_id": "../x"}).json()["status"] == "error"
    assert client.post("/invocations", json={"mode": "nope", "run_id": "r1"}).json()["status"] == "error"
    assert client.post("/invocations", json={"mode": "start", "run_id": "r1"}).json()["error"].startswith("event_id")


def test_malformed_resume_is_an_error_not_a_recursion(client) -> None:
    """Probe finding 2026-10-08: {"group_id","action"} used to end in EventLoopException (recursion depth)."""
    r = client.post("/invocations", json={"mode": "start", "event_id": "ev_demo", "run_id": "r1", "demo": True,
                                          "wait": True, "max_items": 2}).json()
    assert r["status"] == "paused"
    bad = {"mode": "resume", "run_id": "r1", "demo": True, "decisions": [{"group_id": "g_412", "action": "approve"}]}
    for wait in (False, True):
        r = client.post("/invocations", json={**bad, "wait": wait}).json()
        assert r["status"] == "error" and "human_verdict is required" in r["error"], r
    assert client.post("/invocations", json={"mode": "status", "run_id": "r1"}).json()["status"] == "paused"
    r = client.post("/invocations", json={**bad, "wait": True, "decisions": [
        {"group_id": "g_412", "human_verdict": {"keep": ["p_8832"], "reject": ["p_8833", "p_8830"],
                                                "review": ["p_8831"]}}]}).json()
    assert r["status"] in ("paused", "done"), r


def test_agentcore_session_header_marks_host(client, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setenv("CULL_AGENT_HOST", "")  # registered with monkeypatch so the app's setdefault is undone
    del os.environ["CULL_AGENT_HOST"]
    r = client.post("/invocations", headers={"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "s" * 40},
                    json={"mode": "start", "run_id": "run_ac", "demo": True, "wait": True}).json()
    assert r["status"] == "paused"
    run = dynamodb.find_run("run_ac")
    assert run["agent_host"] == "agentcore" and run["run_kind"] == "deployed"

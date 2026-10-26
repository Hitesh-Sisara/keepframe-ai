"""Unit tests for the AgentCore RunAgent loop Lambdas (no AWS: clients are stubbed)."""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("WORK_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
sys.path.insert(0, str(Path(__file__).parent))

import agent_status  # noqa: E402
import agentcore  # noqa: E402
import invoke_agent  # noqa: E402

ARN = "arn:aws:bedrock-agentcore:ap-south-1:111111111111:runtime/keepframe_agent-abc"
API_SRC = Path(__file__).resolve().parents[3] / "services" / "api" / "app" / "agent_resume.py"


class FakeAgentCore:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def invoke_agent_runtime(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        body = self.reply if isinstance(self.reply, bytes) else json.dumps(self.reply).encode()
        return {"statusCode": 200, "response": io.BytesIO(body)}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"param": ARN, "updates": [], "item": None}
    monkeypatch.setattr(agentcore, "_ssm", types.SimpleNamespace(
        get_parameter=lambda Name: {"Parameter": {"Value": state["param"]}}))  # noqa: N803
    monkeypatch.setattr(agentcore, "_ddb", types.SimpleNamespace(
        get_item=lambda **kw: {"Item": state["item"]} if state["item"] else {}))
    monkeypatch.setattr(invoke_agent, "update_run", lambda e, r, f: state["updates"].append(f))
    return state


def use(monkeypatch: pytest.MonkeyPatch, reply: Any) -> FakeAgentCore:
    fake = FakeAgentCore(reply)
    monkeypatch.setattr(agentcore, "_agentcore", fake)
    return fake


def test_session_id_matches_api_rule() -> None:
    """The workflow and the API must derive the same session id (docs/AGENT.md "AgentCore Runtime")."""
    ns: dict[str, Any] = {}
    src = API_SRC.read_text()
    start = src.index("def session_id_for")
    end = src.index("\ndef ", start + 1)
    # module-level constants the function uses (SESSION_PREFIX, SESSION_MIN_LEN, ...), then the function itself
    consts = "\n".join(re.findall(r"^SESSION_[A-Z_]+\s*(?::[^=]+)?=.*$", src, flags=re.M))
    exec("from typing import Any\n" + consts + "\n" + src[start:end], ns)  # noqa: S102 - our own source
    for run_id in ("r", "run_aws_e07_20261005T184515Z", "x" * 40):
        for run in (None, {}, {"agent_session_id": "s" * 40}, {"agent_session_id": "short"}):
            assert agentcore.session_id_for(run, run_id) == ns["session_id_for"](run, run_id)
    assert len(agentcore.session_id_for(None, "r")) >= 33
    # pinned by the API tests
    assert agentcore.session_id_for(None, "run_01J9X") == "keepframe-run_01J9X00000000000000"


def test_start_invokes_agentcore_with_contract_payload(monkeypatch: pytest.MonkeyPatch, env: dict[str, Any]) -> None:
    fake = use(monkeypatch, {"status": "running", "run_id": "run_1"})
    out = invoke_agent.handler({"event_id": "ev_1", "run_id": "run_1"}, None)
    assert out["via"] == "agentcore" and out["status"] == "running"
    call = fake.calls[0]
    assert call["agentRuntimeArn"] == ARN
    assert call["runtimeSessionId"] == agentcore.session_id_for(None, "run_1") == out["session_id"]
    assert json.loads(call["payload"]) == {"mode": "start", "event_id": "ev_1", "run_id": "run_1", "wait": False}
    assert env["updates"][0]["agent_host"] == "agentcore"
    assert env["updates"][0]["agent_session_id"] == out["session_id"]


def test_start_uses_pinned_session(monkeypatch: pytest.MonkeyPatch, env: dict[str, Any]) -> None:
    env["item"] = {"agent_session_id": {"S": "p" * 40}}
    fake = use(monkeypatch, {"status": "running"})
    invoke_agent.handler({"event_id": "ev_1", "run_id": "run_1"}, None)
    assert fake.calls[0]["runtimeSessionId"] == "p" * 40


@pytest.mark.parametrize("param", ["none", "", "arn:aws:ecs:ap-south-1:1:task-definition/cull-agent:7"])
def test_non_runtime_parameter_fails_without_invoking(monkeypatch: pytest.MonkeyPatch, env: dict[str, Any],
                                                      param: str) -> None:
    """The Fargate fallback is gone: anything but an AgentCore runtime ARN fails the run (Catch)."""
    env["param"] = param
    fake = use(monkeypatch, {"status": "running"})
    with pytest.raises(agentcore.AgentError):
        invoke_agent.handler({"event_id": "ev_1", "run_id": "run_1"}, None)
    with pytest.raises(agentcore.AgentError):
        agent_status.handler(status_event(), None)
    assert fake.calls == [] and env["updates"] == []


@pytest.mark.parametrize("reply", [{"status": "error", "error": "invalid run_id"}, {"oops": 1}, b"<html>"])
def test_start_error_raises(monkeypatch: pytest.MonkeyPatch, env: dict[str, Any], reply: Any) -> None:
    use(monkeypatch, reply)
    with pytest.raises(agentcore.AgentError):
        invoke_agent.handler({"event_id": "ev_1", "run_id": "run_1"}, None)


def test_bad_ids_rejected(env: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        invoke_agent.handler({"event_id": "../x", "run_id": "run_1"}, None)


def status_event(started: int | None = None) -> dict[str, Any]:
    return {"event_id": "ev_1", "run_id": "run_1", "session_id": agentcore.session_id_for(None, "run_1"),
            "started_epoch": started if started is not None else int(time.time())}


@pytest.mark.parametrize("raw,want", [("running", "running"), ("paused", "paused"), ("done", "done"),
                                      ("error", "error"), ("worker_done", "running"), (None, "running")])
def test_status_normalised(monkeypatch: pytest.MonkeyPatch, env: dict[str, Any], raw: Any, want: str) -> None:
    fake = use(monkeypatch, {"status": raw, "pending_approvals": [{"interrupt_id": "i1"}]})
    out = agent_status.handler(status_event(), None)
    assert out["status"] == want and out["pending_approvals"] == 1
    assert json.loads(fake.calls[0]["payload"]) == {"mode": "status", "run_id": "run_1"}
    assert fake.calls[0]["runtimeSessionId"] == status_event()["session_id"]


def test_status_budget(monkeypatch: pytest.MonkeyPatch, env: dict[str, Any]) -> None:
    use(monkeypatch, {"status": "running"})
    with pytest.raises(agent_status.AgentTimeout):
        agent_status.handler(status_event(int(time.time()) - agent_status.BUDGET_S - 5), None)
    use(monkeypatch, {"status": "paused"})  # a terminal answer past the budget still counts
    assert agent_status.handler(status_event(int(time.time()) - agent_status.BUDGET_S - 5), None)["status"] == "paused"


def test_status_refuses_short_session(env: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        agent_status.handler({**status_event(), "session_id": "short"}, None)

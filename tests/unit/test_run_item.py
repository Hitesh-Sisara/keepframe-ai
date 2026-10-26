"""RUN# item shared by the worker and the agent: timing and escalation fields never clobber each other."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from keepframe.agent import strands_agent as sa
from keepframe.agent._tool_stubs import demo_scenario
from keepframe.agent.fake_model import FakeModel
from keepframe.io import proxies
from keepframe.store import dynamodb


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORK_BUCKET", raising=False)
    for k, v in {"CULL_LOCAL_ROOT": str(tmp_path), "CULL_TOOLS": "stub", "CULL_METRICS": "off",
                 "CULL_TRACE_SINKS": "ddb,s3", "CULL_DDB": "memory"}.items():
        monkeypatch.setenv(k, v)
    proxies.reset_store()
    dynamodb.reset_backend()


def worker_pass(run_id: str) -> None:
    """What the worker writes after its pass."""
    dynamodb.update_run("ev_demo", run_id, status="running", started_at="2026-10-05T17:08:20.000Z",
                        worker_started_at="2026-10-05T17:08:20.000Z", worker_ended_at="2026-10-05T17:09:11.000Z",
                        worker_wall_s=51.0, worker_escalations=686, run_kind="deployed", photos_processed=661,
                        verdict_counts={"keep": 600, "reject": 5,
                                                                                    "review": 56})


def run_item(run_id: str) -> dict[str, Any]:
    it = dynamodb.get_run("ev_demo", run_id)
    assert it is not None
    return it


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_worker_then_agent_then_pause_then_finish() -> None:
    worker_pass("run_two")
    res = sa.run_event_agent("ev_demo", "run_two", model=FakeModel(), scenario=demo_scenario())
    assert res["status"] == "paused"
    it = run_item("run_two")
    # started_at belongs to the first writer; the agent did not move it
    assert it["started_at"] == "2026-10-05T17:08:20.000Z"
    assert it["worker_started_at"] == "2026-10-05T17:08:20.000Z" and it["worker_ended_at"] == "2026-10-05T17:09:11.000Z"
    assert it["worker_wall_s"] == 51.0 and it["worker_escalations"] == 686
    assert ts(it["agent_started_at"]) > ts(it["worker_ended_at"])
    assert it["status"] == "awaiting_approval" and ts(it["paused_at"]) >= ts(it["agent_started_at"])
    assert "agent_ended_at" not in it and "ended_at" not in it  # paused is not terminal
    assert it["agent_escalations"] == 1 and "escalation_count" not in it
    assert it["agent_wall_s"] >= 0 and it["gsi1pk"] == "RUNS"
    assert it["run_kind"] == "deployed"  # the worker created the item first; the agent did not relabel it
    assert it["agent_photos"] == 5 and it["photos_processed"] == 661  # no collision with the worker field
    first_agent_start = it["agent_started_at"]

    res = sa.resume_with_decisions("run_two", [{"group_id": "g_412", "human_verdict": {"keep": ["p_8831"],
                                   "reject": ["p_8830", "p_8832", "p_8833"]}}], model=FakeModel(),
                                   scenario=demo_scenario(), source="ui")
    assert res["status"] == "done"
    it = run_item("run_two")
    assert it["started_at"] == "2026-10-05T17:08:20.000Z" and it["agent_started_at"] == first_agent_start
    assert it["status"] == "succeeded" and "paused_at" not in it
    assert it["agent_ended_at"] == it["ended_at"] and ts(it["ended_at"]) > ts(it["started_at"])
    assert it["worker_escalations"] == 686 and it["agent_escalations"] == 1
    assert it["verdict_counts"]["review"] == 56  # worker's counts untouched; agent's are agent_verdict_counts
    assert it["agent_verdict_counts"]["keep"] == 1


def test_agent_alone_creates_started_at(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    monkeypatch.delenv("CULL_RUN_KIND", raising=False)
    sa.run_event_agent("ev_demo", "run_solo", model=FakeModel(), scenario=demo_scenario())
    it = run_item("run_solo")
    assert it["started_at"] == it["agent_started_at"] and it["run_kind"] == "local"
    assert "photos_processed" not in it and it["agent_photos"] == 5


@pytest.mark.parametrize("var", ["ECS_CONTAINER_METADATA_URI_V4", "CULL_RUN_KIND"])
def test_run_kind_deployed(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    monkeypatch.delenv("CULL_RUN_KIND", raising=False)
    monkeypatch.setenv(var, "x")
    sa.run_event_agent("ev_demo", f"run_dep_{var[:4]}", model=FakeModel(), scenario=demo_scenario())
    assert run_item(f"run_dep_{var[:4]}")["run_kind"] == "deployed"


def test_if_not_exists_semantics() -> None:
    dynamodb.update_run("ev_demo", "r1", started_at="A")
    dynamodb.update_run("ev_demo", "r1", started_at="B", status="running")
    assert run_item("r1")["started_at"] == "A" and run_item("r1")["status"] == "running"


def test_aws_update_expression_uses_if_not_exists() -> None:
    calls: list[dict[str, Any]] = []

    class T:
        def update_item(self, **kw: Any) -> None:
            calls.append(kw)

    b = dynamodb.AwsBackend.__new__(dynamodb.AwsBackend)
    b._tables = {"t": T()}
    b.update("t", "pk", "sk", {"status": "running"}, remove=("paused_at",), set_if_absent={"started_at": "A"})
    expr = calls[0]["UpdateExpression"]
    assert "if_not_exists(#o0, :o0)" in expr and expr.endswith("REMOVE #r0")
    assert calls[0]["ExpressionAttributeNames"]["#o0"] == "started_at"


@pytest.mark.parametrize("path", ["http", "store"])
def test_lead_mechanism_test_is_never_human(path: str) -> None:
    """Worker, then agent pauses, then the team answers as a mechanism test (labeller lead-mechanism-test)."""
    worker_pass("run_lead")
    res = sa.run_event_agent("ev_demo", "run_lead", model=FakeModel(), scenario=demo_scenario())
    assert res["status"] == "paused"
    answer = {"keep": ["p_8831"], "reject": ["p_8830", "p_8832", "p_8833"]}
    if path == "http":  # /invocations payload as the API forwards it: source "http" + labeller
        decs = [{"group_id": "g_412", "human_verdict": answer, "labeller": "lead-mechanism-test"}]
        res = sa.resume_with_decisions("run_lead", decs, model=FakeModel(), scenario=demo_scenario(),
                                       source="http")
    else:  # ECS --resume: the API saved human_decision with the labeller on the GROUP# item
        pend = dynamodb.pending_for_run("run_lead")[0]
        dynamodb.backend().update("keepframe-decisions", pend["pk"], pend["sk"], {"human_decision": {
            "human_verdict": answer, "note": "", "human_source": "ui", "labeller": "lead-mechanism-test"}},
            remove=("gsi3pk", "gsi3sk"))
        res = sa.resume_with_decisions("run_lead", None, model=FakeModel(), scenario=demo_scenario())
    assert res["status"] == "done"
    for pid in ("p_8830", "p_8831", "p_8832", "p_8833"):
        it = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", f"PHOTO#{pid}")
        assert it["decided_by"] == "lead_test" and it["human_source"] == "lead_test"
        assert "not a photographer" in it["reason_text"]
    run = run_item("run_lead")
    assert run["worker_escalations"] == 686 and run["started_at"] == "2026-10-05T17:08:20.000Z"


def test_lead_test_validation() -> None:
    from keepframe.tools.decision import DecisionError, validate_decisions

    ok = {"photo_id": "p1", "verdict": "keep", "reason_codes": ["KEEP_SINGLETON"], "confidence": 0.6}
    assert validate_decisions([{**ok, "decided_by": "lead_test", "human_source": "lead_test"}])
    for bad in ({**ok, "decided_by": "human", "human_source": "lead_test"},
                {**ok, "decided_by": "lead_test", "human_source": "ui"}):
        with pytest.raises(DecisionError):
            validate_decisions([bad])

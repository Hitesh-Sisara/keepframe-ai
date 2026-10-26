"""End-to-end agent loop with the FakeModel + stub tools (no network, no images, no AWS).

Asserts the three docs/AGENT.md "Trace schema" "vision result changed the next action" moments, the HITL pause/resume with
decided_by=human, and that hard caps escalate instead of looping.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from keepframe.agent import strands_agent as sa
from keepframe.agent._tool_stubs import StubTools, demo_scenario
from keepframe.agent.fake_model import FakeModel
from keepframe.io import proxies
from keepframe.store import dynamodb


@pytest.fixture(autouse=True)
def local_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("WORK_BUCKET", raising=False)
    monkeypatch.setenv("CULL_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("CULL_TOOLS", "stub")
    monkeypatch.setenv("CULL_MODEL_PROVIDER", "fake")
    monkeypatch.setenv("CULL_METRICS", "off")
    monkeypatch.setenv("CULL_TRACE_SINKS", "ddb,s3")
    monkeypatch.setenv("CULL_DDB", "local")
    proxies.reset_store()
    dynamodb.reset_backend()
    yield tmp_path
    proxies.reset_store()
    dynamodb.reset_backend()


def trace(root: Path, event_id: str = "ev_demo") -> list[dict[str, Any]]:
    return [json.loads(x) for x in (root / f"results/{event_id}/trace.jsonl").read_text().splitlines()]


def nxt_call(lines: list[dict[str, Any]], seq: int) -> dict[str, Any]:
    return next(x for x in lines if x["seq"] > seq and x["type"] == "tool_call")


HUMAN = [{"group_id": "g_412", "human_verdict": {"keep": ["p_8831"], "reject": ["p_8830", "p_8832", "p_8833"]},
          "note": "8831 has the better expression"}]


def run_demo(run_id: str = "run_t1", **kw: Any) -> dict[str, Any]:
    s = demo_scenario()
    return sa.run_event_agent("ev_demo", run_id, model=FakeModel(), scenario=s, **kw)


def test_three_vision_moments_and_interrupt(local_env: Path) -> None:
    res = run_demo()
    assert res["status"] == "paused"
    pend = res["pending_approvals"]
    assert len(pend) == 1 and pend[0]["items"][0]["group_id"] == "g_412"
    lines = trace(local_env)
    assert [x["seq"] for x in lines] == list(range(len(lines)))  # monotonic, gap-free
    assert lines[0]["type"] == "run_header" and lines[0]["opencv_version"].startswith("5.")

    # 1. borderline full-frame sharpness -> the next call re-scores on the face ROI
    r1 = next(x for x in lines if x["type"] == "tool_result" and x["tool"] == "score_sharpness"
              and any(p["photo_id"] == "p_8831" and p["label"] == "borderline" for p in x["result"]["per_photo"]))
    after = [x for x in lines if x["seq"] > r1["seq"] and x["type"] == "tool_call"]
    faces_call = next(x for x in after if x["tool"] == "score_sharpness")
    assert faces_call["args"]["roi"] == "faces" and "p_8831" in faces_call["args"]["photo_ids"]
    why = next(x for x in lines if x["type"] == "model_reasoning" and x["seq"] < faces_call["seq"]
               and "Policy 2" in x["text"])
    assert "borderline" in why["text"] and "p_8831" in why["text"]
    faces_res = next(x for x in lines if x["seq"] > faces_call["seq"] and x["type"] == "tool_result")
    assert next(p for p in faces_res["result"]["per_photo"] if p["photo_id"] == "p_8831")["label"] == "sharp"

    # 2. main subject eyes closed -> compare within the group (pick_best), not a reject
    eyes = next(x for x in lines if x["type"] == "tool_result" and x["tool"] == "score_eyes_open")
    assert any(p["any_main_subject_closed"] for p in eyes["result"]["per_photo"])
    later = [x for x in lines if x["seq"] > eyes["seq"] and x["type"] == "tool_call"]
    tools_after = [x["tool"] for x in later]
    assert "pick_best_in_cluster" in tools_after
    assert tools_after.index("pick_best_in_cluster") < (tools_after.index("write_decision")
                                                        if "write_decision" in tools_after else 99)
    assert any("compare within the group" in x["text"] for x in lines
               if x["type"] == "model_reasoning" and x["seq"] > eyes["seq"])

    # 3. small margin -> contact sheet + request_human_approval
    best = next(x for x in lines if x["type"] == "tool_result" and x["tool"] == "pick_best_in_cluster")
    assert best["result"]["margin"] < 0.05
    calls = [x["tool"] for x in lines if x["seq"] > best["seq"] and x["type"] == "tool_call"]
    assert calls[:2] == ["make_contact_sheet", "request_human_approval"]
    assert lines[-1]["type"] == "interrupt"
    # tool_result lines carry the OpenCV proof + latency
    tr = [x for x in lines if x["type"] == "tool_result"]
    assert all(x["opencv"].startswith("5.") and x["cv2_file"] and "latency_ms" in x for x in tr)

    # approval queue item (GSI3) for the API
    pend_items = dynamodb.pending_for_run("run_t1")
    assert len(pend_items) == 1 and pend_items[0]["gsi3pk"] == "PENDING"
    assert pend_items[0]["sk"] == "GROUP#g_412" and pend_items[0]["interrupt_id"] == pend[0]["interrupt_id"]
    assert pend_items[0]["contact_sheet_key"] == "sheets/ev_demo/g_412.jpg"
    # escalation never creates a verdict-less PHOTO# row (it would shadow the worker's verdict in the API)
    assert dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "PHOTO#p_8833") is None
    assert (local_env / "sessions/run_t1.json").exists()
    run = dynamodb.get_run("ev_demo", "run_t1")
    assert run["status"] == "awaiting_approval" and run["gsi1pk"] == "RUNS" and run["llm_provider"] == "custom"


def test_resume_writes_human_decisions(local_env: Path) -> None:
    run_demo()
    res = sa.resume_with_decisions("run_t1", HUMAN, model=FakeModel(), scenario=demo_scenario(), source="ui")
    assert res["status"] == "done", res
    lines = trace(local_env)
    resumed = next(x for x in lines if x["type"] == "interrupt_resumed")
    w = nxt_call(lines, resumed["seq"])
    assert w["tool"] == "write_decision"
    assert {d["photo_id"]: (d["verdict"], d["decided_by"]) for d in w["args"]["decisions"]} == {
        "p_8831": ("keep", "human"), "p_8830": ("reject", "human"), "p_8832": ("reject", "human"),
        "p_8833": ("reject", "human")}
    item = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "PHOTO#p_8831")
    assert item["decided_by"] == "human" and item["verdict"] == "keep" and item["human_source"] == "ui"
    assert item["confidence"] == 0.6  # the proposal's confidence is kept, not forced to 1.0
    assert item["agent_confidence"] == 0.6  # the agent's own proposal, recorded at request_human_approval
    agent_row = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "PHOTO#p_9002")
    assert agent_row["agent_confidence"] == agent_row["confidence"] == 0.88
    assert item["gsi1pk"] == "EVENT#ev_demo#VERDICT#keep" and item["gsi2pk"] == "GROUP#g_412"
    assert "person" in item["reason_text"] and len(item["reason_text"]) <= 140
    g = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "GROUP#g_412")
    assert "gsi3pk" not in g and g["approval_status"] == "resolved" and g["best_photo_id"] == "p_8831"
    assert [r["photo_id"] for r in g["ranking"]][0] == "p_8833"
    assert dynamodb.pending_for_run("run_t1") == []
    # the singleton (artistic intent) is decided by the agent as review, not reject
    s = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "PHOTO#p_9002")
    assert s["verdict"] == "review" and "REVIEW_ARTISTIC_INTENT" in s["reason_codes"] and s["decided_by"] == "agent"
    rows = [json.loads(x) for x in (local_env / "results/ev_demo/decisions.jsonl").read_text().splitlines()]
    assert {r["photo_id"] for r in rows} == {"p_8830", "p_8831", "p_8832", "p_8833", "p_9002"}
    assert lines[-1]["type"] == "run_end"
    traces = dynamodb.query_trace("run_t1")
    assert [t["sk"] for t in traces] == [f"{i:06d}" for i in range(len(lines))]
    assert all(t["ttl"] > 0 and t["pk"] == "RUN#run_t1" for t in traces)


def test_escalation_marks_existing_rows(local_env: Path) -> None:
    """A photo that already has a PHOTO# row gets agent_confidence when its group is escalated."""
    dynamodb.put_photo_decisions("ev_demo", [{"photo_id": "p_8832", "verdict": "reject", "reason_codes":
                                              ["BURST_NOT_BEST"], "confidence": 0.8, "decided_by": "worker"}], "t0")
    run_demo("run_esc")
    it = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "PHOTO#p_8832")
    assert it["agent_confidence"] == 0.6 and it["verdict"] == "reject" and it["decided_by"] == "worker"


def _api_save(run_id: str, answer: dict[str, Any], source: str = "ui") -> None:
    """Exactly what the merged API writes on POST /approvals/{id} (shape read from the AWS RUN# item
    run_aws_e03_20261005T155605Z): human_decision on the pending GROUP# item (gsi3 removed) and
    resume_decisions on RUN#."""
    pend = dynamodb.pending_for_run(run_id)[0]
    gid = pend["group_id"]
    human = {"decision": "override", "human_verdict": answer, "note": "from the UI", "decided_at": "t1",
             "human_source": source}
    dynamodb.backend().update("keepframe-decisions", pend["pk"], pend["sk"],
                              {"human_decision": human, "approval_status": "resolved", "resolved_via": "ecs"},
                              remove=("gsi3pk", "gsi3sk"))
    dec = {"human_source": source, "note": "from the UI", "interrupt_id": pend["interrupt_id"],
           "interrupt_name": pend["interrupt_name"], "human_verdict": answer, "group_id": gid, "id": gid}
    dynamodb.update_run("ev_demo", run_id, resume_decisions=[dec])


@pytest.mark.parametrize("where", ["group_item", "run_item_only"])
def test_ecs_resume_reads_api_payload(local_env: Path, where: str) -> None:
    """ECS path `python -m keepframe.agent.run --run-id X --resume` (no file): answers come from DynamoDB,
    human_source=ui is stamped on interrupt_resumed and on every PHOTO# row, agent_confidence preserved."""
    from keepframe.agent import run as cli

    run_demo("run_ecs")
    answer = {"keep": ["p_8831"], "reject": ["p_8830", "p_8832", "p_8833"], "review": []}
    _api_save("run_ecs", answer)
    if where == "run_item_only":  # older API: only RUN#.resume_decisions carries the answer
        g = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "GROUP#g_412")
        dynamodb.backend().update("keepframe-decisions", g["pk"], g["sk"], {}, remove=("human_decision",))
    import keepframe.agent.strands_agent as sa_mod

    real = sa_mod.resume_with_decisions

    def with_fake(*a: Any, **kw: Any) -> dict[str, Any]:
        kw.update(model=FakeModel(), scenario=demo_scenario())
        return real(*a, **kw)

    sa_mod.resume_with_decisions = with_fake  # type: ignore[assignment]
    try:
        assert cli.main(["--run-id", "run_ecs", "--resume"]) == 0
    finally:
        sa_mod.resume_with_decisions = real  # type: ignore[assignment]
    lines = [x for x in trace(local_env) if x["run_id"] == "run_ecs"]
    resumed = [x for x in lines if x["type"] == "interrupt_resumed"]
    assert len(resumed) == 1 and resumed[0]["human_source"] == "ui"
    assert lines[-1]["type"] == "run_end"
    for pid, verdict in (("p_8831", "keep"), ("p_8830", "reject"), ("p_8832", "reject"), ("p_8833", "reject")):
        it = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", f"PHOTO#{pid}")
        assert (it["verdict"], it["decided_by"], it["human_source"]) == (verdict, "human", "ui")
        assert it["agent_confidence"] == 0.6 and it["confidence"] == 0.6
    assert dynamodb.pending_for_run("run_ecs") == []


def test_ecs_resume_never_upgrades_scripted(local_env: Path) -> None:
    run_demo("run_ecs_s")
    _api_save("run_ecs_s", {"keep": ["p_8832"], "reject": ["p_8830"], "review": ["p_8831", "p_8833"]},
              source="scripted")
    res = sa.resume_with_decisions("run_ecs_s", None, model=FakeModel(), scenario=demo_scenario())
    assert res["status"] == "done"
    it = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "PHOTO#p_8832")
    assert it["decided_by"] == "scripted" and it["human_source"] == "scripted"


def test_resume_from_store_answers(local_env: Path) -> None:
    """ECS path: the API wrote human_decision on the GROUP# item; --resume reads it from DynamoDB."""
    run_demo()
    dynamodb.resolve_pending("ev_demo", "g_412", "group", {"human_verdict": HUMAN[0]["human_verdict"],
                                                           "note": "from the UI"}, "2026-10-05T00:00:00Z", "api")
    res = sa.resume_with_decisions("run_t1", None, model=FakeModel(), scenario=demo_scenario())
    assert res["status"] == "done"
    item = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "PHOTO#p_8833")
    assert item["decided_by"] == "human" and item["verdict"] == "reject"


def test_hook_interrupt_on_low_confidence_reject(local_env: Path) -> None:
    """A write_decision with decided_by=agent and a reject < 0.8 is paused by the BeforeToolCall hook itself."""
    s = demo_scenario()
    s["photos"]["p_9002"]["exposure_time_s"] = None  # no artistic-intent excuse
    s["photos"]["p_9002"]["hour"] = 12
    s["photos"]["p_9002"]["sharp"]["saliency:processed"] = {**s["photos"]["p_9002"]["sharp"]["saliency:processed"],
                                                            "blur_kind": "focus", "confidence": 0.7}
    s["groups"][0]["needs_approval"] = False
    s["decisions"] = [d for d in s["decisions"] if d["photo_id"] in ("p_9002",)]
    res = sa.run_event_agent("ev_demo", "run_hook", model=ScriptedReject(), scenario=s)
    assert res["status"] == "paused"
    assert res["pending_approvals"][0]["name"].startswith("keepframe-policy-approval")
    res = sa.resume_with_decisions("run_hook", [{"photo_id": "p_9002", "human_verdict": "keep", "note": "ok"}],
                                   model=ScriptedReject(), scenario=s, source="http")
    assert res["status"] == "done"
    item = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "PHOTO#p_9002")
    assert item["decided_by"] == "human" and item["verdict"] == "keep" and item["human_source"] == "http"
    assert item["confidence"] == 0.7  # the gated reject's confidence, preserved
    assert item["agent_confidence"] == 0.7  # P5 hook gate: the model's own per-photo confidence


@pytest.mark.parametrize("source,by", [("scripted", "scripted"), ("file", "human"), ("http", "human"),
                                       ("ui", "human")])
def test_human_source_attribution(local_env: Path, source: str, by: str) -> None:
    """Scripted approvals are never recorded as human; every path stamps human_source on decisions + trace."""
    run_demo(f"run_src_{source}")
    res = sa.resume_with_decisions(f"run_src_{source}", HUMAN, model=FakeModel(), scenario=demo_scenario(),
                                   source=source)
    assert res["status"] == "done"
    lines = trace(local_env)
    resumed = [x for x in lines if x["type"] == "interrupt_resumed" and x["run_id"] == f"run_src_{source}"]
    assert resumed and resumed[0]["human_source"] == source
    for pid in ("p_8830", "p_8831", "p_8832", "p_8833"):
        it = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", f"PHOTO#{pid}")
        assert it["decided_by"] == by and it["human_source"] == source and it["confidence"] == 0.6
        if by == "scripted":
            assert "person" not in it["reason_text"] and "no human review" in it["reason_text"]
    rows = [json.loads(x) for x in (local_env / "results/ev_demo/decisions.jsonl").read_text().splitlines()]
    assert {r["decided_by"] for r in rows if r["group_id"] == "g_412"} == {by}
    # the agent itself can never claim either attribution
    assert all(r["human_source"] is None for r in rows if r["decided_by"] == "agent")


def test_source_required_with_explicit_decisions(local_env: Path) -> None:
    run_demo("run_nosrc")
    with pytest.raises(ValueError):
        sa.resume_with_decisions("run_nosrc", HUMAN, model=FakeModel(), scenario=demo_scenario())


class ScriptedReject(FakeModel):
    """Planner that tries an agent reject at 0.7 for the singleton (to exercise the hook interrupt)."""

    async def stream(self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kw: Any):  # type: ignore[override]
        from keepframe.agent.fake_model import _results

        h = _results(messages)
        names = [n for n, *_ in h]
        if not names:
            call = ("next_work_item", {})
        elif "score_sharpness" not in names:
            call = ("score_sharpness", {"photo_ids": ["p_9002"], "roi": "saliency"})
        elif "write_decision" not in names or h[-1][0] == "next_work_item" and not h[-1][2].get("done"):
            call = ("write_decision", {"decisions": [{"photo_id": "p_9002", "verdict": "reject",
                                                      "reason_codes": ["BLUR_FOCUS"], "confidence": 0.7}]})
        elif h[-1][0] == "write_decision":
            call = ("next_work_item", {})
        else:
            call = None
        yield {"messageStart": {"role": "assistant"}}
        if call is None:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "done"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
            return
        import os

        tid = f"t_{os.urandom(4).hex()}"
        yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tid, "name": call[0]}}}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(call[1])}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


class Looper(FakeModel):
    """A misbehaving planner that keeps re-scoring the same group forever."""

    async def stream(self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kw: Any):  # type: ignore[override]
        import os

        from keepframe.agent.fake_model import _results

        h = _results(messages)
        call = ("next_work_item", {}) if not h else ("score_exposure", {"photo_ids": ["p_8830"]})
        if h and h[-1][0] == "next_work_item" and h[-1][2].get("done"):
            call = None
        yield {"messageStart": {"role": "assistant"}}
        if call is None:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "done"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
            return
        tid = f"t_{os.urandom(4).hex()}"
        yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tid, "name": call[0]}}}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(call[1])}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


def test_caps_escalate_not_loop(local_env: Path) -> None:
    res = sa.run_event_agent("ev_demo", "run_loop", model=Looper(), scenario=demo_scenario())
    assert res["status"] == "done"
    lines = trace(local_env)
    calls = [x for x in lines if x["type"] == "tool_call"]
    assert len(calls) < 60  # bounded: group cap 25, then blocked calls stop the run
    assert any(x["type"] == "escalation" for x in lines)
    assert any("cap" in (x.get("text") or "") for x in lines if x["type"] == "model_reasoning")
    assert res["summary"]["stopped"] or res["summary"]["escalated"]
    pend = dynamodb.pending_for_run("run_loop")
    assert any(p["sk"] == "GROUP#g_412" for p in pend)


def test_retry_cap_on_failing_tool(local_env: Path) -> None:
    s = demo_scenario()
    tools = StubTools(s, fail={"score_sharpness": 2})
    res = sa.run_event_agent("ev_demo", "run_retry", model=FakeModel(), scenario=s, tools=tools)
    assert res["status"] == "paused"  # transient failures retried, then the normal flow continues
    lines = trace(local_env)
    assert sum(1 for x in lines if x["type"] == "error" and "retrying" in x.get("text", "")) == 2


def test_cli_demo_roundtrip(local_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from keepframe.agent.run import main

    assert main(["--demo", "--run-id", "run_cli", "--approve-all"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "done" and out["summary"]["decided"] >= 5
    it = dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "PHOTO#p_8832")
    assert it["decided_by"] == "scripted" and it["human_source"] == "scripted"  # --approve-all is never human

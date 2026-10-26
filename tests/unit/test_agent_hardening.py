"""AgentCore hardening: secret bootstrap, decision validation, bounded loops, host stamp (docs/AGENT.md "AgentCore Runtime")."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from keepframe.agent import models
from keepframe.agent import strands_agent as sa
from keepframe.agent._tool_stubs import demo_scenario
from keepframe.agent.fake_model import FakeModel
from keepframe.io import proxies
from keepframe.store import dynamodb
from keepframe.tools.approval import validate_answers

SECRET_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CULL_ANTHROPIC_SECRET_ID")
GOOD = [{"group_id": "g_412", "human_verdict": {"keep": ["p_8831"], "reject": ["p_8830", "p_8832", "p_8833"]}}]


@pytest.fixture(autouse=True)
def local_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("WORK_BUCKET", raising=False)
    for k, v in {"CULL_LOCAL_ROOT": str(tmp_path), "CULL_TOOLS": "stub", "CULL_MODEL_PROVIDER": "fake",
                 "CULL_METRICS": "off", "CULL_TRACE_SINKS": "ddb,s3", "CULL_DDB": "local"}.items():
        monkeypatch.setenv(k, v)
    for k in ("CULL_AGENT_HOST", "CULL_INSTANCE_TYPE", "ECS_CONTAINER_METADATA_URI_V4", "CULL_RUN_KIND"):
        monkeypatch.delenv(k, raising=False)
    proxies.reset_store()
    dynamodb.reset_backend()
    yield tmp_path
    proxies.reset_store()
    dynamodb.reset_backend()


def trace(root: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (root / "results/ev_demo/trace.jsonl").read_text().splitlines()]


# --------------------------------------------------------------------------- 1. secret bootstrap


class StubSM:
    def __init__(self, doc: Any = None, exc: Exception | None = None) -> None:
        self.doc, self.exc, self.calls = doc, exc, []

    def get_secret_value(self, SecretId: str) -> dict[str, str]:  # noqa: N803 - boto3 casing
        self.calls.append(SecretId)
        if self.exc:
            raise self.exc
        return {"SecretString": json.dumps(self.doc)}


@pytest.fixture()
def anthropic_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.delenv("KEEPFRAME_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("CULL_MODEL_PROVIDER", "anthropic")
    for k in SECRET_ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_secret_bootstrap_sets_env_and_never_logs(anthropic_env: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    import os

    anthropic_env.setenv("CULL_ANTHROPIC_SECRET_ID", "keepframe/anthropic")
    sm = StubSM({"ANTHROPIC_API_KEY": "sk-test-SECRETVALUE", "ANTHROPIC_BASE_URL": "https://gw.example/ai"})
    assert models.bootstrap_anthropic_secret(sm) == "secret"
    assert sm.calls == ["keepframe/anthropic"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test-SECRETVALUE"
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://gw.example/ai"
    out = capsys.readouterr()
    assert "SECRETVALUE" not in out.out + out.err and "gw.example" not in out.out + out.err
    assert models.bootstrap_anthropic_secret(sm) == "env" and len(sm.calls) == 1  # read once


def test_secret_bootstrap_env_wins(anthropic_env: pytest.MonkeyPatch) -> None:
    anthropic_env.setenv("ANTHROPIC_API_KEY", "from-env")
    anthropic_env.setenv("CULL_ANTHROPIC_SECRET_ID", "keepframe/anthropic")
    sm = StubSM({"ANTHROPIC_API_KEY": "other"})
    assert models.bootstrap_anthropic_secret(sm) == "env" and sm.calls == []


def test_secret_bootstrap_keeps_explicit_base_url(anthropic_env: pytest.MonkeyPatch) -> None:
    import os

    anthropic_env.setenv("CULL_ANTHROPIC_SECRET_ID", "s")
    anthropic_env.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    models.bootstrap_anthropic_secret(StubSM({"ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "https://x"}))
    assert os.environ["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9"


def test_secret_bootstrap_errors(anthropic_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY or CULL_ANTHROPIC_SECRET_ID"):
        models.bootstrap_anthropic_secret(StubSM())
    anthropic_env.setenv("CULL_ANTHROPIC_SECRET_ID", "s")
    with pytest.raises(RuntimeError, match="could not read") as ei:
        models.bootstrap_anthropic_secret(StubSM(exc=PermissionError("AccessDenied sk-leak")))
    assert "sk-leak" not in str(ei.value)
    with pytest.raises(RuntimeError, match="no ANTHROPIC_API_KEY"):
        models.bootstrap_anthropic_secret(StubSM({"ANTHROPIC_BASE_URL": "https://x"}))


def test_secret_bootstrap_not_needed_for_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in SECRET_ENV:
        monkeypatch.delenv(k, raising=False)
    assert models.bootstrap_anthropic_secret(StubSM()) == "not_needed"


def test_unknown_provider_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEPFRAME_LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="bedrock\\|anthropic\\|fake"):
        models.provider()
    assert set(models.DEFAULTS) == {"bedrock", "anthropic", "fake"}


def test_cli_fails_clearly_without_key(anthropic_env: pytest.MonkeyPatch) -> None:
    from keepframe.agent.run import main

    with pytest.raises(RuntimeError, match="CULL_ANTHROPIC_SECRET_ID"):
        main(["--demo", "--run-id", "run_nokey"])


# --------------------------------------------------------------------------- 3. decision validation


@pytest.mark.parametrize("decisions, msg", [
    ([{"group_id": "g_412", "action": "approve"}], "human_verdict is required"),
    ([], "non-empty list"),
    ("keep", "non-empty list"),
    ([42], "must be an object"),
    ([{"human_verdict": "keep"}], "needs one of"),
    ([{"photo_id": "p1", "human_verdict": "approve"}], "not in"),
    ([{"group_id": "g1", "human_verdict": "keep"}], "a group needs a keep/reject/review map"),
    ([{"photo_id": "p1", "human_verdict": {"keep": ["p1"]}}], "single photo"),
    ([{"group_id": "g1", "human_verdict": {"maybe": ["p1"]}}], "keys"),
    ([{"group_id": "g1", "human_verdict": {"keep": "p1"}}], "lists of photo ids"),
    ([{"group_id": "g1", "human_verdict": {"keep": ["p1"], "reject": ["p1"]}}], "more than one verdict"),
    ([{"group_id": "g1", "human_verdict": {"reject": ["p1"]}}], "keep at least one"),
    ([{"group_id": "g1", "human_verdict": 1}], "string or a map"),
    ([{"photo_id": "p1", "human_verdict": "keep", "note": 3}], "note must be a string"),
])
def test_validate_answers_rejects(decisions: Any, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        validate_answers(decisions)


def test_validate_answers_accepts_api_shapes() -> None:
    validate_answers(GOOD)
    validate_answers([{"photo_id": "p1", "human_verdict": "reject", "note": "blink"}])
    validate_answers([{"interrupt_id": "v1:x", "id": "p1", "human_verdict": "review", "human_source": "ui"}])
    with pytest.raises(ValueError, match="outside the group"):
        validate_answers([{"group_id": "g1", "human_verdict": {"keep": ["zz"]}}], {"g1": ["p1", "p2"]})


def _paused(run_id: str) -> dict[str, Any]:
    res = sa.run_event_agent("ev_demo", run_id, model=FakeModel(), scenario=demo_scenario())
    assert res["status"] == "paused"
    return res


def test_resume_rejects_malformed_and_stays_resumable(local_env: Path) -> None:
    _paused("run_bad")
    bad = [{"group_id": "g_412", "action": "approve"}]
    res = sa.resume_with_decisions("run_bad", bad, model=FakeModel(), scenario=demo_scenario(), source="http")
    assert res["status"] == "error" and "human_verdict is required" in res["error"]
    assert dynamodb.find_run("run_bad")["status"] == "awaiting_approval"  # untouched
    res = sa.resume_with_decisions("run_bad", GOOD, model=FakeModel(), scenario=demo_scenario(), source="http")
    assert res["status"] in ("paused", "done")
    assert dynamodb.backend().get("keepframe-decisions", "EVENT#ev_demo", "PHOTO#p_8831")["verdict"] == "keep"


def test_resume_rejects_member_outside_group(local_env: Path) -> None:
    _paused("run_out")
    bad = [{"group_id": "g_412", "human_verdict": {"keep": ["p_9999"]}}]
    res = sa.resume_with_decisions("run_out", bad, model=FakeModel(), scenario=demo_scenario(), source="http")
    assert res["status"] == "error" and "outside the group" in res["error"]


def test_resume_rejects_malformed_store_answers(local_env: Path) -> None:
    _paused("run_store")
    dynamodb.update_run("ev_demo", "run_store", resume_decisions=[{"group_id": "g_412", "action": "approve"}])
    res = sa.resume_with_decisions("run_store", None, model=FakeModel(), scenario=demo_scenario())
    assert res["status"] == "error" and "human_verdict" in res["error"]


def test_cli_resume_malformed_file_exits_1(local_env: Path, tmp_path: Path,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    from keepframe.agent.run import main

    assert main(["--demo", "--run-id", "run_clibad"]) == 0
    capsys.readouterr()
    f = tmp_path / "bad.json"
    f.write_text(json.dumps({"decisions": [{"group_id": "g_412", "action": "approve"}]}))
    assert main(["--demo", "--run-id", "run_clibad", "--resume", str(f)]) == 1
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "error" and "human_verdict" in out["error"]


# --------------------------------------------------------------------------- 3b. root cause: bounded loops


class BadWriter(FakeModel):
    """Calls write_decision with the same invalid (empty) args forever: the pre-fix recursion pattern."""

    def __init__(self) -> None:
        super().__init__()
        self.n = 0

    async def stream(self, messages: Any, tool_specs: Any = None, system_prompt: str | None = None,
                     **kw: Any) -> AsyncGenerator[Any, None]:
        self.n += 1
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"tu_bad_{self.n}", "name": "write_decision"}}}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({"decisions": []})}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


def test_failing_write_loop_is_bounded(local_env: Path) -> None:
    m = BadWriter()
    res = sa.run_event_agent("ev_demo", "run_badw", model=m, scenario=demo_scenario())
    assert res["status"] == "done" and res["summary"]["stopped"]
    assert m.n <= 10  # 6 consecutive failures stop the run; before the fix this looped until RecursionError
    assert any(x["type"] == "escalation" for x in trace(local_env))


class Chatter(FakeModel):
    """Harmless tool call (get_run_budget) many times, then end_turn: a long but legitimate invocation."""

    def __init__(self, turns: int) -> None:
        super().__init__()
        self.turns, self.n = turns, 0

    async def stream(self, messages: Any, tool_specs: Any = None, system_prompt: str | None = None,
                     **kw: Any) -> AsyncGenerator[Any, None]:
        self.n += 1
        yield {"messageStart": {"role": "assistant"}}
        if self.n > self.turns:
            yield {"contentBlockDelta": {"delta": {"text": "done"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
            return
        yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"tu_c_{self.n}", "name": "get_run_budget"}}}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


def test_long_invocation_is_chunked_not_recursive(local_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setenv("CULL_TURNS_PER_CHUNK", "20")
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(400)  # one unchunked invocation of 150 turns needs >450 frames
    try:
        m = Chatter(150)
        res = sa.run_event_agent("ev_demo", "run_long", model=m, scenario=demo_scenario())
    finally:
        sys.setrecursionlimit(old)
    assert res["status"] == "done" and m.n == 151


# --------------------------------------------------------------------------- 4. host stamp


@pytest.mark.parametrize("env, host, kind", [
    ({}, "local", "local"),
    ({"ECS_CONTAINER_METADATA_URI_V4": "http://x"}, "fargate", "deployed"),
    ({"CULL_INSTANCE_TYPE": "agentcore-arm64"}, "agentcore", "deployed"),
    ({"CULL_AGENT_HOST": "agentcore"}, "agentcore", "deployed"),
])
def test_host_stamp(local_env: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], host: str,
                    kind: str) -> None:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sa.run_event_agent("ev_demo", f"run_h_{host}", model=FakeModel(), scenario=demo_scenario())
    run = dynamodb.find_run(f"run_h_{host}")
    assert run["agent_host"] == host and run["run_kind"] == kind
    hdr = next(x for x in trace(local_env) if x["type"] == "run_header")
    assert hdr["host"] == host and hdr["run_kind"] == kind


def test_resume_restamps_agent_host(local_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _paused("run_rehost")
    assert dynamodb.find_run("run_rehost")["agent_host"] == "local"
    monkeypatch.setenv("CULL_AGENT_HOST", "agentcore")  # e.g. started on Fargate fallback, resumed on AgentCore
    sa.resume_with_decisions("run_rehost", GOOD, model=FakeModel(), scenario=demo_scenario(), source="http")
    assert dynamodb.find_run("run_rehost")["agent_host"] == "agentcore"


# --------------------------------------------------------------------------- eval-metrics defects (2026-10-09)


@pytest.mark.parametrize("item, want", [
    ({"photo_id": "p1", "proposed": {"verdict": "review"}}, "review"),
    ({"photo_id": "p1", "proposed": {"reject": ["p1"]}}, "reject"),
    ({"photo_id": "p1", "proposed": "keep"}, "keep"),
    ({"photo_id": "p1", "proposed": None}, "review"),
    ({"photo_id": "p1", "proposed": {"verdict": "approve"}}, "review"),
    ({"group_id": "g1", "proposed": {"keep": ["a"], "reject": ["b"], "review": []}}, {"keep": ["a"], "reject": ["b"]}),
])
def test_approve_all_answer_shapes(item: dict[str, Any], want: Any) -> None:
    from keepframe.agent.run import _auto_answers

    ans = _auto_answers({"pending_approvals": [{"interrupt_id": "v1:x", "items": [item]}]}, "auto")
    assert ans[0]["human_verdict"] == want
    validate_answers(ans)


def test_pause_normalises_singleton_proposal() -> None:
    from keepframe.tools.approval import normalise_items

    out = normalise_items([{"photo_id": "p1", "proposed": {"verdict": "reject"}},
                           {"group_id": "g1", "proposed": {"keep": ["a"], "review": []}}], "ev")
    assert out[0]["proposed"] == "reject" and out[1]["proposed"] == {"keep": ["a"]}


def test_cli_approve_all_singleton_map_proposal(local_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    """E09 defect: a singleton approval proposed as {"verdict": v} left --approve-all paused with status error."""
    from keepframe.agent import fake_model as fm
    from keepframe.agent.run import main

    orig = fm.Planner.singleton_step

    def ask_first(self: Any, item: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
        if self.last("request_human_approval") is None:  # the E09 planner shape: a map, not the bare string
            pid = item["photos"][0]["photo_id"]
            return ("Unsure; ask.", "request_human_approval",
                    {"items": [{"photo_id": pid, "proposed": {"verdict": "review"}, "reason": "test",
                                "confidence": 0.5}]})
        return orig(self, item)

    monkeypatch.setattr(fm.Planner, "singleton_step", ask_first)
    assert main(["--demo", "--run-id", "run_aa", "--approve-all"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "done", out
    pend = [x for x in trace(local_env) if x["type"] == "interrupt"]
    singles = [it for x in pend for p in x["pending"] for it in p["items"] if it.get("photo_id")]
    assert singles and all(it["proposed"] == "review" for it in singles)
    res = [x for x in trace(local_env) if x["type"] == "tool_result" and x["tool"] == "request_human_approval"]
    assert res and res[-1]["result"]["decisions"][0]["human_verdict"] == "review"  # accepted, not an error
    resumed = [x for x in trace(local_env) if x["type"] == "interrupt_resumed"]
    assert resumed and all(x["human_source"] == "scripted" for x in resumed)


def test_tokens_recorded_and_summed_across_resumes(local_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CULL_TURNS_PER_CHUNK", "3")  # several chunks per invocation: all must be counted
    res = _paused("run_tok")
    run = dynamodb.find_run("run_tok")
    first_in, first_out = run["agent_tokens_in"], run["agent_tokens_out"]
    intr = [x for x in trace(local_env) if x["type"] == "interrupt"][-1]
    assert first_in > 0 and first_out > 0 and (intr["tokens_in"], intr["tokens_out"]) == (first_in, first_out)
    turns_1 = first_out // 10  # FakeModel: 10 output tokens per model turn
    assert turns_1 == sum(1 for x in trace(local_env) if x["type"] == "tool_call")
    res = sa.resume_with_decisions("run_tok", GOOD, model=FakeModel(), scenario=demo_scenario(), source="http")
    while res["status"] == "paused":
        from keepframe.agent.run import _auto_answers

        res = sa.resume_with_decisions("run_tok", _auto_answers(res, "auto"), model=FakeModel(),
                                       scenario=demo_scenario(), source="scripted")
    assert res["status"] == "done"
    run = dynamodb.find_run("run_tok")
    end = next(x for x in trace(local_env) if x["type"] == "run_end")
    assert run["agent_tokens_in"] > first_in and run["agent_tokens_out"] > first_out
    assert (end["summary"]["tokens_in"], end["summary"]["tokens_out"]) == (run["agent_tokens_in"],
                                                                           run["agent_tokens_out"])

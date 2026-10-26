"""Agent runtime HTTP service: AgentCore Runtime contract (/invocations, /ping) on port 8080.

The same process serves AgentCore Runtime (``agentcore launch``) and a plain Fargate ARM64 service/task
(``python services/agent_runtime/app.py``), so the API's resume path works without AgentCore.

POST /invocations  {event_id, run_id, mode: start|resume|status, decisions?, max_items?, wait?, demo?}
  -> {status: running|paused|done|error, run_id, pending_approvals?, summary?, error?}
  start/resume run in a background thread (a 600-photo event takes minutes); the response says
  "running" and later calls with mode=status (or the RUN# item in DynamoDB) report paused/done.
  ``wait: true`` blocks until the run pauses or finishes (tests, CLI, short runs).
GET /ping -> {"status": "Healthy" | "HealthyBusy"} (busy while a run is in flight, AgentCore semantics).

Startup: with the default provider (KEEPFRAME_LLM_PROVIDER=bedrock) the runtime role signs Bedrock calls and no
secret is read. With the optional anthropic provider, ``models.bootstrap_anthropic_secret()`` fills
ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL from Secrets Manager (CULL_ANTHROPIC_SECRET_ID) when they are not in the
environment (AgentCore has no secret injection); if neither is available the process exits with a RuntimeError.
Resume decisions are validated before a run thread starts (``tools.approval.validate_answers``), so a
malformed answer comes back synchronously as {"status": "error"} even with ``wait: false``.
Hosting: a request carrying the AgentCore session header marks the process CULL_AGENT_HOST=agentcore
(stamped as RUN# ``agent_host`` and trace ``host``).

SECURITY: this service has NO authentication of its own. On AgentCore it sits behind IAM SigV4
(InvokeAgentRuntime). On Fargate it must only be reachable inside the VPC (security group from the API
Lambda only, no public IP, no load balancer to the internet). The API's HTTP resume path refuses non-https,
non-loopback URLs.
"""

from __future__ import annotations

import os
import re
import threading
import traceback
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp, BedrockAgentCoreContext
from bedrock_agentcore.runtime.models import PingStatus

from keepframe.agent import models
from keepframe.tools.approval import validate_answers

models.bootstrap_anthropic_secret()  # once, before the first request; never logs values
app = BedrockAgentCoreApp()

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_runs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _check(v: Any, what: str) -> str:
    if not isinstance(v, str) or not _ID.match(v) or ".." in v:
        raise ValueError(f"invalid {what}")
    return v


def _work(run_id: str, fn: Any, *args: Any, **kw: Any) -> None:
    task = app.add_async_task(f"run:{run_id}")
    try:
        res = fn(*args, **kw)
    except Exception as e:  # noqa: BLE001 - surfaced through mode=status and the RUN# item
        res = {"status": "error", "run_id": run_id, "error": f"{type(e).__name__}: {e}"[:500]}
        traceback.print_exc()
        try:
            from keepframe.store import dynamodb
            from keepframe.store.traces import now_iso

            ev = _runs.get(run_id, {}).get("event_id")
            if ev:
                dynamodb.update_run(ev, run_id, status="failed", error=res["error"], updated_at=now_iso())
        except Exception:  # noqa: BLE001
            pass
    finally:
        app.complete_async_task(task)
    with _lock:
        _runs[run_id] = {**_runs.get(run_id, {}), **res, "finished": True}


def _launch(run_id: str, ev: str | None, fn: Any, *args: Any, wait: bool = False, **kw: Any) -> dict[str, Any]:
    event_id = ev
    with _lock:
        cur = _runs.get(run_id)
        if cur and not cur.get("finished"):
            return {"status": "running", "run_id": run_id, "note": "already in progress"}
        _runs[run_id] = {"status": "running", "run_id": run_id, "event_id": event_id, "finished": False}
    t = threading.Thread(target=_work, args=(run_id, fn, *args), kwargs=kw, daemon=True, name=f"run-{run_id}")
    t.start()
    if wait:
        t.join()
        with _lock:
            return {k: v for k, v in _runs[run_id].items() if k != "finished"}
    return {"status": "running", "run_id": run_id, "event_id": event_id}


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    from keepframe.agent import strands_agent as sa

    if BedrockAgentCoreContext.get_session_id():
        # only AgentCore Runtime sends X-Amzn-Bedrock-AgentCore-Runtime-Session-Id; the SDK sets no env var
        os.environ.setdefault("CULL_AGENT_HOST", "agentcore")
    try:
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        mode = payload.get("mode", "start")
        run_id = _check(payload.get("run_id"), "run_id")
        event_id = payload.get("event_id")
        if event_id is not None:
            _check(event_id, "event_id")
        wait = bool(payload.get("wait", False))
        scenario = None
        if payload.get("demo"):
            # synthetic stub scenario: only when the process itself runs on stub tools (never real data)
            if os.environ.get("CULL_TOOLS") != "stub":
                raise ValueError("demo runs need CULL_TOOLS=stub on the server")
            from keepframe.agent._tool_stubs import demo_scenario

            scenario = demo_scenario(event_id or "ev_demo")
            event_id = scenario["event_id"]
        if mode == "status":
            with _lock:
                st = dict(_runs.get(run_id) or {})
            if st:
                st.pop("finished", None)
                return st
            from keepframe.store import dynamodb

            run = dynamodb.find_run(run_id) or {}
            return {"status": {"awaiting_approval": "paused", "succeeded": "done", "partial": "done",
                               "failed": "error"}.get(run.get("status", ""), run.get("status", "unknown")),
                    "run_id": run_id}
        if mode == "start":
            if not event_id:
                raise ValueError("event_id is required for mode=start")
            max_items = payload.get("max_items")
            return _launch(run_id, event_id, sa.run_event_agent, event_id, run_id, wait=wait,
                           max_items=int(max_items) if max_items else None, scenario=scenario)
        if mode == "resume":
            decisions = payload.get("decisions")
            if decisions is not None:
                validate_answers(decisions)  # ValueError -> {"status": "error"}; the run stays paused
            # explicit decisions in the payload -> http; none -> read from DynamoDB, stored human_source honoured
            src = "scripted" if scenario is not None else ("http" if decisions is not None else None)
            return _launch(run_id, event_id, sa.resume_with_decisions, run_id, decisions, wait=wait,
                           event_id=event_id, scenario=scenario, source=src)
        raise ValueError("mode must be start|resume|status")
    except ValueError as e:
        return {"status": "error", "error": str(e)[:300]}


@app.ping
def ping() -> PingStatus:
    with _lock:
        busy = any(not r.get("finished") for r in _runs.values())
    return PingStatus.HEALTHY_BUSY if busy else PingStatus.HEALTHY


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8080")))

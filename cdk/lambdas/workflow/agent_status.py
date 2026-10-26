"""CullEvent AgentStatus: poll the agent on AgentCore with {"mode":"status","run_id"}.

Input {event_id, run_id, session_id, started_epoch}. Returns {status} normalised to running|paused|done|error.
Anything else the agent reports (e.g. a RUN# status read back from DynamoDB) counts as running, bounded by
the 2 h budget: past it, AgentTimeout is raised and the Catch marks the run failed.
"""

from __future__ import annotations

import os
import time
from typing import Any

from agentcore import invoke, runtime_arn
from runs import check_id, log

BUDGET_S = int(os.environ.get("CULL_AGENT_BUDGET_S", "7200"))
TERMINAL = ("paused", "done", "error")


class AgentTimeout(Exception):
    pass


def normalise(status: Any) -> str:
    return status if status in TERMINAL else "running"


def handler(event: dict[str, Any], _ctx: Any) -> dict[str, Any]:
    event_id = check_id("event_id", event.get("event_id"))
    run_id = check_id("run_id", event.get("run_id"))
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or len(session_id) < 33:
        raise ValueError("invalid session_id")
    elapsed = int(time.time()) - int(event.get("started_epoch") or 0)
    arn = runtime_arn()
    doc = invoke(arn, session_id, {"mode": "status", "run_id": run_id})
    status = normalise(doc.get("status"))
    log("agent_status", event_id=event_id, run_id=run_id, raw=doc.get("status"), status=status, elapsed_s=elapsed)
    if status == "running" and elapsed > BUDGET_S:
        raise AgentTimeout(f"agent still running after {elapsed} s (budget {BUDGET_S} s)")
    out: dict[str, Any] = {"status": status, "elapsed_s": elapsed}
    if status == "error":
        out["error"] = str(doc.get("error") or "agent reported error")[:300]
    if doc.get("pending_approvals") is not None:
        out["pending_approvals"] = doc["pending_approvals"] if isinstance(doc["pending_approvals"], int) else len(doc["pending_approvals"])
    return out

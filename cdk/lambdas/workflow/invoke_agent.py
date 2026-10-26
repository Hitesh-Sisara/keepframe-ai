"""CullEvent RunAgent: start the agent on AgentCore Runtime.

Input {event_id, run_id}. Reads the runtime ARN from SSM /keepframe/agent-runtime-arn, then
InvokeAgentRuntime {"mode":"start","event_id","run_id","wait":false} on the run's session; returns
{via:"agentcore", status, session_id, started_epoch}. Raises AgentError when the parameter is not a runtime ARN
or the runtime answers status=error, so the Catch marks the run failed.
"""

from __future__ import annotations

import time
from typing import Any

from agentcore import AgentError, invoke, run_item, runtime_arn, session_id_for
from runs import check_id, log, now_iso, update_run


def handler(event: dict[str, Any], _ctx: Any) -> dict[str, Any]:
    event_id = check_id("event_id", event.get("event_id"))
    run_id = check_id("run_id", event.get("run_id"))
    arn = runtime_arn()
    session_id = session_id_for(run_item(event_id, run_id), run_id)
    doc = invoke(arn, session_id, {"mode": "start", "event_id": event_id, "run_id": run_id, "wait": False})
    status = doc.get("status")
    log("agent_started", event_id=event_id, run_id=run_id, via="agentcore", status=status, note=doc.get("note"))
    if status == "error" or status not in ("running", "paused", "done"):
        raise AgentError(f"agent start: {str(doc.get('error') or status)[:300]}")
    update_run(event_id, run_id, {"agent_host": "agentcore", "agent_session_id": session_id, "updated_at": now_iso()})
    return {"via": "agentcore", "status": status, "session_id": session_id, "started_epoch": int(time.time())}

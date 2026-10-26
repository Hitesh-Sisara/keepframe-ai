"""MarkRunFailed (CullEvent Catch target): input is the execution state with `error` = {Error, Cause}.

Marks the RUN# item `failed` with a short, non-sensitive error string. Never raises on a malformed state
(it is the last line of defence), so the state machine always reaches its Fail state cleanly.
"""

from __future__ import annotations

import json
from typing import Any

from runs import check_id, log, now_iso, update_run


def _short(err: Any) -> str:
    if not isinstance(err, dict):
        return "unknown error"
    name = str(err.get("Error", "Error"))[:80]
    cause = err.get("Cause", "")
    try:  # Lambda/ECS causes are JSON; keep the message or stop reason only
        doc = json.loads(cause) if isinstance(cause, str) and cause.startswith("{") else {}
    except ValueError:
        doc = {}
    detail = doc.get("errorMessage") or doc.get("StoppedReason") or (cause if isinstance(cause, str) else "")
    return f"{name}: {str(detail)[:200]}"


def handler(event: dict[str, Any], _ctx: Any) -> dict[str, Any]:
    error = _short(event.get("error"))
    try:
        event_id = check_id("event_id", event.get("event_id"))
        run_id = check_id("run_id", event.get("run_id"))
    except ValueError:
        log("run_failed_unrecorded", error=error)
        return {"status": "failed", "error": error, "recorded": False}
    update_run(event_id, run_id, {"status": "failed", "error": error, "ended_at": now_iso(), "updated_at": now_iso()})
    log("run_failed", event_id=event_id, run_id=run_id, error=error)
    return {"event_id": event_id, "run_id": run_id, "status": "failed", "error": error, "recorded": True}

"""Summarise (CullEvent last step): reads results/<eventId>/<runId>/summary.json written by the worker (falling
back to the unversioned results/<eventId>/summary.json for runs written before per-run keys) and copies the
headline counts onto the RUN# item.

Status: `succeeded`, unless the agent left the run paused on a human interrupt (`paused` or
`awaiting_approval`) and exited 0; that status is kept, and the API's POST /approvals/{id} starts a
`--resume` agent task that finishes the run. A missing
summary is not an error; it is recorded as `summary_missing=true`.
"""

from __future__ import annotations

from typing import Any

from runs import check_id, first_existing, get_json, get_run_status, log, now_iso, result_keys, update_run

PAUSED = ("paused", "awaiting_approval")
KEEP = (
    "verdict_counts", "n_photos", "n_groups", "wall_s", "opencv_version", "opencv_hal", "cv2_file", "flavor",
    "instance_type", "thresholds_sha", "cost_estimate_usd", "escalation_count", "tool_call_count",
)


def handler(event: dict[str, Any], _ctx: Any) -> dict[str, Any]:
    event_id = check_id("event_id", event.get("event_id"))
    run_id = check_id("run_id", event.get("run_id"))
    found, source = first_existing(result_keys(event_id, run_id, "summary.json"), get_json)
    summary = found or {}
    fields: dict[str, Any] = {k: summary[k] for k in KEEP if k in summary}
    current = get_run_status(event_id, run_id)
    status = current if current in PAUSED else "succeeded"
    fields.update({"status": status, "stage": "summarised", "updated_at": now_iso()})
    if status == "succeeded":
        fields["ended_at"] = now_iso()
    if not summary:
        fields["summary_missing"] = True
    else:
        fields["summary_key"] = source
    update_run(event_id, run_id, fields)
    log("run_summarised", event_id=event_id, run_id=run_id, status=status, found=bool(summary), key=source)
    return {"event_id": event_id, "run_id": run_id, "status": status,
            "verdict_counts": summary.get("verdict_counts")}

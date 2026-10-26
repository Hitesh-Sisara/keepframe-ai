"""RUN# item for the API's run list (DynamoDB keepframe-decisions, by design).

The deterministic worker writes exactly one item per run, at the end, and only when WORK_BUCKET is set:
pk="EVENT#<eventId>", sk="RUN#<runId>", gsi1pk="RUNS" (sparse all-runs index the API queries).
UpdateItem (not PutItem): POST /runs already created the item with status "starting" and execution_arn,
so we set our worker_* attributes and keep theirs; a CLI-only run creates it. started_at uses if_not_exists.
PHOTO#/GROUP# items belong to the agent's store (keepframe/store). CULL_WORKER_ONLY=1 marks a run with no agent
pass (status succeeded + ended_at); CULL_DISABLE_DDB=1 skips the write (tests). Any failure is logged and
swallowed: a missing table or missing IAM permission never fails a run.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from keepframe import log_json

DEFAULT_TABLE = "keepframe-decisions"
# run ids become an S3 key segment (results/<eventId>/<runId>/): API ids are run_<ULID>, CLI/bench ids are similar.
# Same character set as the agent runtime's id check (services/agent_runtime/app.py _ID), so no accepted run is refused.
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
IMAGE_SHA_ENV = ("CULL_IMAGE_SHA", "IMAGE_SHA", "IMAGE_DIGEST")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def check_run_id(run_id: str) -> str:
    """Raise ValueError unless run_id is safe as one S3 key segment."""
    if not RUN_ID_RE.match(run_id) or ".." in run_id:
        raise ValueError(f"run_id {run_id!r} is not a safe key segment ({RUN_ID_RE.pattern})")
    return run_id


def run_results_prefix(event_id: str, run_id: str) -> str:
    """Per-run results prefix `results/<eventId>/<runId>/`: never overwritten by a later run of the event."""
    return f"results/{event_id}/{check_run_id(run_id)}/"


def result_keys(event_id: str, run_id: str, name: str) -> tuple[str, str]:
    """(versioned, latest) keys for one result file. The unversioned `results/<eventId>/<name>` copy is kept for one
    release so the API, the summarise Lambda, the agent and the bench keep reading the newest run."""
    return f"{run_results_prefix(event_id, run_id)}{name}", f"results/{event_id}/{name}"


def _ecs_container_metadata(timeout: float = 0.5) -> dict[str, Any]:
    uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4", "").strip()
    if not uri.startswith("http://"):
        return {}
    try:
        with urllib.request.urlopen(uri, timeout=timeout) as r:  # noqa: S310 (task-local link address)
            meta = json.loads(r.read())
    except (OSError, ValueError) as e:
        log_json("image_sha_metadata_failed", error=str(e)[:200])
        return {}
    return meta if isinstance(meta, dict) else {}


def image_sha() -> str | None:
    """Container image digest `sha256:<64 hex>` for summary.json / RUN#, or None outside a known container.

    Order: env CULL_IMAGE_SHA / IMAGE_SHA / IMAGE_DIGEST (infra may set it; AgentCore has no task metadata), then
    the ECS task metadata v4 container document: the digest in `Image` when the task pins `repo@sha256:...`,
    else `ImageID` (the digest ECS resolved for the pulled image). Anything that is not a sha256 digest is
    ignored rather than recorded."""
    for k in IMAGE_SHA_ENV:
        m = _DIGEST_RE.search(os.environ.get(k, ""))
        if m:
            return m.group(0)
    meta = _ecs_container_metadata()
    for field in ("Image", "ImageID"):
        m = _DIGEST_RE.search(str(meta.get(field) or ""))
        if m:
            return m.group(0)
    return None


def _ddb(value: Any) -> Any:
    """Python -> DynamoDB-safe values (floats as Decimal, drop None in maps)."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        return {str(k): _ddb(v) for k, v in value.items() if v is not None}
    if isinstance(value, list | tuple):
        return [_ddb(v) for v in value]
    return value


def run_attributes(summary: dict[str, Any], agent_follows: bool = True) -> dict[str, Any]:
    """worker_* attributes of the RUN# item (shared with the agent's store writer).

    The worker never writes the run-level `ended_at` / `wall_s` / `escalation_count`: the agent pass follows in the
    state machine and the store writer owns those. `status` is `worker_done` while the agent is still to run
    (`agent_follows`), else the terminal `succeeded` / `failed`. `started_at` is written separately with
    if_not_exists (see write_run_item).
    """
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    failed = summary["status"] == "failed"
    status = "failed" if failed else ("worker_done" if agent_follows else "succeeded")
    attrs: dict[str, Any] = {
        "gsi1pk": "RUNS",
        "run_id": summary["run_id"],
        "event_id": summary["event_id"],
        "status": status,
        "worker_status": summary["status"],
        "worker_started_at": summary["started_at"],
        "worker_ended_at": summary["ended_at"],
        "worker_wall_s": summary["wall_s"],
        "worker_escalations": summary.get("escalation_count", 0),
        "worker_images_per_s": summary.get("images_per_s"),
        "updated_at": now,
        "opencv_version": summary["opencv_version"],
        "cv2_file": summary["cv2_file"],
        "opencv_hal": summary.get("opencv_hal"),
        "flavor": summary["flavor"],
        "instance_type": summary["instance_type"],
        "image_sha": summary.get("image_sha"),
        "thresholds_sha": summary["thresholds_sha"],
        "verdict_counts": summary["verdict_counts"],
        "n_photos": summary["n_photos"],
        "photos_processed": summary["n_photos"] - len(summary.get("failures") or []),
        "n_groups": summary["n_groups"],
        "results_prefix": f"results/{summary['event_id']}/",  # latest copy (kept for one release)
        "results_run_prefix": summary.get("results_run_prefix"),  # results/<eventId>/<runId>/, immutable per run
    }
    if not agent_follows:  # worker-only run: it is the whole run, so record the terminal fields too
        attrs.update(ended_at=summary["ended_at"], decided_by="worker", llm_provider="none")
    if failed and summary.get("failures"):
        attrs["error"] = str(summary["failures"][0].get("error", ""))[:500]
    return {k: v for k, v in attrs.items() if v is not None}


def run_kind() -> str:
    """`CULL_RUN_KIND` if set (infra / Step Functions override), else `deployed` inside ECS (the task metadata
    endpoint ECS_CONTAINER_METADATA_URI_V4 is set), else `local`."""
    explicit = os.environ.get("CULL_RUN_KIND", "").strip()
    if explicit:
        return explicit
    return "deployed" if os.environ.get("ECS_CONTAINER_METADATA_URI_V4") else "local"


def agent_follows_default() -> bool:
    """True when the agent pass runs after the worker (state machine); CULL_WORKER_ONLY=1 for CLI-only runs."""
    return os.environ.get("CULL_WORKER_ONLY") != "1"


def write_run_item(summary: dict[str, Any], table: str | None = None, client: Any = None,
                   agent_follows: bool | None = None, run_note: str | None = None) -> bool:
    """Upsert the worker's part of the RUN# item. `started_at` and `run_kind` only if absent (a hand-set
    run_kind such as local_calibration is never overwritten); `run_note` when given. Never raises."""
    if client is None and (not os.environ.get("WORK_BUCKET") or os.environ.get("CULL_DISABLE_DDB") == "1"):
        return False
    table = table or os.environ.get("CULL_DDB_TABLE", DEFAULT_TABLE)
    follows = agent_follows_default() if agent_follows is None else agent_follows
    attrs = run_attributes(summary, follows)
    if run_note:
        attrs["run_note"] = run_note[:500]
    try:
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.resource(
                "dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"),
                config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 3}),
            ).Table(table)
        names = {f"#a{i}": k for i, k in enumerate(attrs)}
        values = {f":v{i}": _ddb(v) for i, v in enumerate(attrs.values())}
        sets = [f"#a{i} = :v{i}" for i in range(len(attrs))]
        names["#started"] = "started_at"
        values[":started"] = summary["started_at"]
        sets.append("#started = if_not_exists(#started, :started)")  # never overwrite the run's start
        names["#kind"] = "run_kind"
        values[":kind"] = run_kind()
        sets.append("#kind = if_not_exists(#kind, :kind)")
        client.update_item(
            Key={"pk": f"EVENT#{summary['event_id']}", "sk": f"RUN#{summary['run_id']}"},
            UpdateExpression="SET " + ", ".join(sets), ExpressionAttributeNames=names, ExpressionAttributeValues=values,
        )
        log_json("run_item_written", table=table, run_id=summary["run_id"], status=attrs["status"])
        return True
    except Exception as e:  # noqa: BLE001 - optional side effect; a run must not fail because of it
        log_json("run_item_error", table=table, run_id=summary["run_id"], error=f"{type(e).__name__}: {e}"[:300])
        return False

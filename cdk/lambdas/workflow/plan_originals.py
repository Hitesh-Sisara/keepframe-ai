"""PlanOriginals (CullEvent, after CullWorker): pick which originals the second sync pass copies (docs/ARCHITECTURE.md "Components").

Candidates = every member of a group of size >= 2 in results/<eventId>/<runId>/groups.json, plus every photo whose
line in results/<eventId>/<runId>/decisions.jsonl has needs_approval=true (each falls back to the unversioned
results/<eventId>/<name> for runs written before per-run keys). Only candidates whose manifest row has an
`original_key` are kept (many events have no originals). The ids go to
originals-requests/<eventId>/<runId>.txt, one per line, which cull-sync reads via PHOTO_IDS_S3 with
SYNC_KINDS=originals.

Returns {count, photo_ids_key}; count == 0 makes the state machine skip SyncOriginals. Missing results files
also give count 0 rather than an error, since originals are an optional quality boost.
"""

from __future__ import annotations

import json
from typing import Any

from runs import BUCKET, _s3, check_id, first_existing, log, now_iso, result_keys, update_run


def _lines(key: str) -> list[dict[str, Any]]:
    try:
        body = _s3.get_object(Bucket=BUCKET, Key=key)["Body"]
    except _s3.exceptions.NoSuchKey:
        return []
    out = []
    for raw in body.iter_lines():
        if raw.strip():
            try:
                doc = json.loads(raw)
            except ValueError:
                continue
            # a line may be one decision or a {"decisions": [...]} batch
            out.extend(doc["decisions"] if isinstance(doc, dict) and isinstance(doc.get("decisions"), list) else [doc])
    return out


def _lines_or_none(key: str) -> list[dict[str, Any]] | None:
    """`_lines`, but None when the object does not exist (an existing empty file is [])."""
    try:
        _s3.head_object(Bucket=BUCKET, Key=key)
    except Exception as exc:  # 404 surfaces as ClientError
        if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return _lines(key)


def _json(key: str) -> Any | None:
    try:
        return json.loads(_s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except _s3.exceptions.NoSuchKey:
        return None


def candidates(groups_doc: Any, decisions: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for g in (groups_doc or {}).get("groups", []) if isinstance(groups_doc, dict) else []:
        members = g.get("members") or g.get("photo_ids") or []
        size = g.get("size", len(members))
        if isinstance(size, int) and size >= 2:
            ids.update(str(m) for m in members)
    for d in decisions:
        if isinstance(d, dict) and d.get("needs_approval") is True and d.get("photo_id"):
            ids.add(str(d["photo_id"]))
    return ids


def with_originals(manifest_lines: list[dict[str, Any]], ids: set[str]) -> list[str]:
    return sorted(r["photo_id"] for r in manifest_lines if r.get("photo_id") in ids and r.get("original_key"))


def handler(event: dict[str, Any], _ctx: Any) -> dict[str, Any]:
    event_id = check_id("event_id", event.get("event_id"))
    run_id = check_id("run_id", event.get("run_id"))
    groups, groups_key = first_existing(result_keys(event_id, run_id, "groups.json"), _json)
    decisions, decisions_key = first_existing(result_keys(event_id, run_id, "decisions.jsonl"), _lines_or_none)
    ids = candidates(groups, decisions or [])
    keep = with_originals(_lines(f"manifests/{event_id}.jsonl"), ids) if ids else []
    key = f"originals-requests/{event_id}/{run_id}.txt"
    if keep:
        _s3.put_object(Bucket=BUCKET, Key=key, Body=("\n".join(keep) + "\n").encode(), ContentType="text/plain",
                       ServerSideEncryption="AES256")
    update_run(event_id, run_id, {"originals_candidates": len(ids), "originals_requested": len(keep),
                                  "stage": "originals_planned", "updated_at": now_iso()})
    log("originals_planned", event_id=event_id, run_id=run_id, candidates=len(ids), with_original=len(keep),
        groups_key=groups_key, decisions_key=decisions_key)
    return {"count": len(keep), "candidates": len(ids), "photo_ids_key": key if keep else None}

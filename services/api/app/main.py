"""KeepFrame review API. Lambda handler: `app.main.handler`. Local: `uvicorn app.main:app`.

Every route requires a bearer token. The judge token is read-only (GET only); the team token may also
start runs and answer approvals. Routes follow the design.
"""

from __future__ import annotations

import hmac
import json
import re
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from pydantic import BaseModel, Field, field_validator

from . import agent_resume, aliases, data
from .auth import Principal, require_reader, require_team
from .aws import client
from .backend import (
    COND_EXISTS,
    COND_NOT_EXISTS,
    COND_PENDING_UNLOCKED,
    COND_UNCHANGED,
    ConditionFailed,
    LocalBackend,
    backend,
    table_index,
)
from .config import settings
from .ids import check_id, decode_cursor, encode_cursor, key_cursor, new_run_id, offset_cursor
from .pivots import filter_for_group, find_pivots
from .views import (
    judge_safe_run,
    IMG_KINDS,
    image_sig,
    LEAD_TEST_LABELLER,
    event_view,
    photo_id_of,
    photo_view,
    proxy_url,
    reason_counts,
    run_view,
    sheet_url,
    sheet_url_for_key,
    trace_view,
    verdict_counts,
)

log = logging.getLogger("keepframe.api")
logging.basicConfig(level=logging.INFO, format="%(message)s")

API_VERSION = "1"
ACTIVE_RUN_STATES = {"starting", "running", "awaiting_approval"}
RESUME_LOCK_S = 60

app = FastAPI(title="KeepFrame API", version=API_VERSION, docs_url=None, redoc_url=None, openapi_url=None)
if settings().cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings().cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

Reader = Annotated[Principal, Depends(require_reader)]
Team = Annotated[Principal, Depends(require_team)]


def _aliased(request: Request) -> bool:
    """Judge responses are always aliased (S-1). Team responses keep real ids for ops and the labelling export
    unless CULL_ALIAS_TEAM=1. Unauthenticated error bodies are aliased too (they carry no ids anyway)."""
    role = getattr(request.state, "role", None)
    return role != "team" or settings().alias_team


@app.middleware("http")
async def alias_responses(request: Request, call_next: Any) -> Response:
    response: Response = await call_next(request)
    if not _aliased(request) or not response.headers.get("content-type", "").startswith("application/json"):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
    try:
        doc = json.loads(body)
    except ValueError:
        return Response(content=body, status_code=response.status_code, headers=dict(response.headers))
    try:
        clean = aliases.scrub(doc)
    except HTTPException as exc:  # alias key unavailable: fail closed, never return the raw body
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    headers = {
        k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-type")
    }
    return JSONResponse(clean, status_code=response.status_code, headers=headers)


@app.middleware("http")
async def access_log(request: Request, call_next: Any) -> Response:
    started = time.perf_counter()
    response: Response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # One JSON line per request; never the Authorization header or query strings (cursors, signatures).
    log.info(
        json.dumps(
            {
                "msg": "request",
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
    )
    return response


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _require_event(event_id: str, principal: Principal | None = None) -> tuple[dict[str, Any], int]:
    """An event by alias (ev_NN). Raw source ids are accepted only from the team token (S-1)."""
    check_id(event_id, "event_id")
    real = aliases.resolve_event(event_id, allow_raw=principal is not None and principal.role == "team")
    found = data.event_meta(real)
    if found is None:
        raise HTTPException(status_code=404, detail="Unknown event")
    return found


def _raw_ok(principal: Principal) -> bool:
    return principal.role == "team"


# ---------------------------------------------------------------- health


@app.get("/health")
def health(principal: Reader) -> dict[str, Any]:
    return {
        "ok": True,
        "api_version": API_VERSION,
        "role": principal.role,
        "local_mode": settings().local_mode,
    }


# ---------------------------------------------------------------- events


@app.get("/events")
def list_events(
    _: Reader,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    metas = data.event_metas()
    start = offset_cursor(cursor)
    page = metas[start : start + limit]
    items = []
    for i, meta in enumerate(page, start=start + 1):
        view = event_view(meta, i)
        runs = data.runs_for_event(meta["event_id"], limit=1)
        summary = data.read_result_json(meta["event_id"], "summary.json", runs[0] if runs else {})
        counts = summary.get("verdict_counts") if isinstance(summary, dict) else None
        view["verdict_counts"] = (
            counts if isinstance(counts, dict) else data.event_verdict_counts(meta["event_id"])
        )
        if runs:
            latest = run_view(runs[0])
            view["latest_run"] = {
                k: latest[k] for k in ("run_id", "status", "flavor", "started_at") if k in latest
            }
        items.append(view)
    nxt = start + limit
    return {"items": items, "next_cursor": encode_cursor({"o": nxt}) if nxt < len(metas) else None}


def _member_view(
    event_id: str,
    pid: str,
    item: dict[str, Any] | None,
    group: dict[str, Any],
    ranking: dict[str, dict[str, Any]],
    detail: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {"photo_id": pid, "proxy_url": proxy_url(event_id, pid)}
    if item:
        view = photo_view(item, group)
        keep = (
            "verdict",
            "reason_codes",
            "confidence",
            "decided_by",
            "decision_source",
            "human_source",
            "labeller",
            "agent_confidence",
            "human_note",
        )
        keep += ("reason_text", "scores", "updated_at") if detail else ()
        out.update({k: view[k] for k in keep if k in view})
    out["is_best"] = pid == group.get("best_photo_id")
    if pid in ranking:
        out["rank"] = ranking[pid]["rank"]
        out["score"] = ranking[pid].get("score")
        if detail and "breakdown" in ranking[pid]:
            out["breakdown"] = ranking[pid]["breakdown"]
    return out


GROUP_FIELDS = ("group_id", "size", "span_s", "best_photo_id", "margin", "tie", "run_id", "updated_at")


_GROUP_ORDINAL = re.compile(r"_(\d{1,6})$")


def group_label(group_id: str, event_id: str) -> str:
    """Short human label: worker ids are `g_<eventId>_<n>` → "Group n"; anything else keeps a short prefix."""
    m = _GROUP_ORDINAL.search(group_id)
    if m and (event_id in group_id or len(group_id) <= 16):
        return f"Group {int(m.group(1))}"
    return group_id if len(group_id) <= 16 else f"{group_id[:12]}…"


def _group_view(
    event_id: str, group: dict[str, Any], photos: dict[str, dict[str, Any]], detail: bool
) -> dict[str, Any]:
    members = [m for m in (group.get("members") or group.get("photo_ids") or []) if isinstance(m, str)]
    ranking = {
        str(r["photo_id"]): {**r, "rank": i}
        for i, r in enumerate(group.get("ranking") or [], start=1)
        if isinstance(r, dict) and "photo_id" in r
    }
    # Ranked members first (best → worst), then the rest in capture order.
    ordered = sorted(members, key=lambda m: (ranking.get(m, {}).get("rank", 10_000), members.index(m)))
    member_items = [photos.get(m) for m in ordered]
    out = {k: group[k] for k in GROUP_FIELDS if k in group and group[k] is not None}
    out["group_id"] = str(group.get("group_id") or str(group.get("sk", "")).removeprefix("GROUP#"))
    out["group_label"] = group_label(out["group_id"], event_id)
    out["size"] = out.get("size", len(members))
    out["members"] = [
        _member_view(event_id, m, it, group, ranking, detail)
        for m, it in zip(ordered, member_items, strict=True)
    ]
    present = [it for it in member_items if it]
    out["verdict_counts"] = verdict_counts(present)
    out["reason_counts"] = reason_counts(present)
    out["pending_approval"] = group.get("gsi3pk") == "PENDING" or group.get("approval_status") == "pending"
    out["contact_sheet_url"] = sheet_url(event_id, out["group_id"])
    return out


def _photos_for(event_id: str, groups: list[dict[str, Any]], from_s3: bool) -> dict[str, dict[str, Any]]:
    ids = [m for g in groups for m in (g.get("members") or g.get("photo_ids") or []) if isinstance(m, str)]
    photos = data.photo_items(event_id, ids)
    if from_s3 or len(photos) < len(set(ids)):
        fallback = data.decisions_from_s3(event_id)
        for pid in ids:
            if pid not in photos and pid in fallback:
                photos[pid] = fallback[pid]
    return photos


@app.get("/events/{event_id}/groups")
def list_groups(
    event_id: str,
    principal: Reader,
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
    cursor: str | None = None,
    group_id: str | None = None,
) -> dict[str, Any]:
    meta, index = _require_event(event_id, principal)
    event_id = str(meta["event_id"])
    event = event_view(meta, index)
    if group_id is not None:
        check_id(group_id, "group_id")
        group_id = aliases.resolve_group(event_id, group_id, allow_raw=_raw_ok(principal))
        group = data.group_item(event_id, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Unknown group")
        photos = _photos_for(event_id, [group], from_s3=False)
        return {
            "event": event,
            "items": [_group_view(event_id, group, photos, detail=True)],
            "next_cursor": None,
        }

    all_groups, source = data.merged_groups(event_id)
    offset = _decode_groups_cursor(cursor, event_id) if cursor is not None else 0
    groups = all_groups[offset : offset + limit]
    nxt = offset + limit
    next_cursor = encode_cursor({"src": "merged", "e": event_id, "o": nxt}) if nxt < len(all_groups) else None
    photos = _photos_for(event_id, groups, from_s3=False)
    out: dict[str, Any] = {
        "event": event,
        "source": source,
        "items": [_group_view(event_id, g, photos, detail=False) for g in groups],
        "next_cursor": next_cursor,
        "total": len(all_groups),
    }
    if cursor is None:
        summary = data.read_result_json(event_id, "summary.json")
        counts = summary.get("verdict_counts") if isinstance(summary, dict) else None
        if not isinstance(counts, dict):
            counts = data.event_verdict_counts(event_id)
            if not any(counts.values()):
                counts = verdict_counts(list(data.decisions_from_s3(event_id).values()))
        out["event_verdict_counts"] = counts
    return out


def _decode_groups_cursor(cursor: str, event_id: str) -> int:
    doc = decode_cursor(cursor)
    if (
        isinstance(doc, dict)
        and doc.get("src") == "merged"
        and doc.get("e") == event_id
        and isinstance(doc.get("o"), int)
        and doc["o"] >= 0
    ):
        return int(doc["o"])
    raise HTTPException(status_code=400, detail="Invalid cursor")


@app.get("/events/{event_id}/photos/{photo_id}")
def get_photo(event_id: str, photo_id: str, principal: Reader) -> dict[str, Any]:
    meta, index = _require_event(event_id, principal)
    event_id = str(meta["event_id"])
    check_id(photo_id, "photo_id")
    photo_id = aliases.resolve_photo(event_id, photo_id, allow_raw=_raw_ok(principal))
    item = data.photo_item(event_id, photo_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No decision for this photo yet")
    gid = item.get("group_id")
    grp = data.group_item(event_id, gid) if isinstance(gid, str) and gid else None
    out = photo_view(item, grp)
    out["event"] = event_view(meta, index)
    out["proxy_url"] = proxy_url(event_id, photo_id_of(item))
    group_id = out.get("group_id")
    if isinstance(group_id, str) and group_id:
        check_id(group_id, "group_id")
        out["contact_sheet_url"] = sheet_url(event_id, group_id)
        group = grp
        if group:
            out["group"] = {k: group[k] for k in ("best_photo_id", "margin", "tie", "size") if k in group}
    return out


# ---------------------------------------------------------------- runs


class StartRun(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    # Only the stock wheel exists (COOL removed 2026-10-07, Marketplace-billed); kept for the SFN input shape.
    flavor: Literal["stock"] = "stock"


@app.post("/runs", status_code=202)
def start_run(body: StartRun, principal: Team) -> dict[str, Any]:
    meta, index = _require_event(body.event_id, principal)
    body = body.model_copy(update={"event_id": str(meta["event_id"])})
    for existing in data.runs_for_event(body.event_id, limit=5):
        if existing.get("status") in ACTIVE_RUN_STATES:
            raise HTTPException(
                status_code=409,
                detail=f"Run {run_view(existing)['run_id']} is still {existing['status']} for this event",
            )
    s = settings()
    if not s.state_machine_arn and not s.local_mode:
        raise HTTPException(status_code=503, detail="CULL_STATE_MACHINE_ARN is not configured")
    run_id = new_run_id()
    started = now_iso()
    pk, sk = f"EVENT#{body.event_id}", f"RUN#{run_id}"
    item = backend().update_item(
        data.decisions_table(),
        pk,
        sk,
        set_values={
            "run_id": run_id,
            "event_id": body.event_id,
            "flavor": body.flavor,
            "status": "starting",
            "started_at": started,
            "updated_at": started,
            "requested_by": "team",
            "gsi1pk": data.RUNS_GSI1PK,
        },
        condition=COND_NOT_EXISTS,
    )
    if s.state_machine_arn:
        try:
            resp = client("stepfunctions").start_execution(
                stateMachineArn=s.state_machine_arn,
                name=run_id,
                input=json.dumps({"event_id": body.event_id, "flavor": body.flavor, "run_id": run_id}),
            )
        except Exception as exc:
            backend().update_item(
                data.decisions_table(),
                pk,
                sk,
                set_values={"status": "failed", "error": "StartExecution failed", "updated_at": now_iso()},
                condition=COND_EXISTS,
            )
            log.error(
                json.dumps({"msg": "start_execution failed", "run_id": run_id, "error": type(exc).__name__})
            )
            raise HTTPException(
                status_code=502, detail="Could not start the CullEvent state machine"
            ) from exc
        item = backend().update_item(
            data.decisions_table(),
            pk,
            sk,
            set_values={"status": "running", "execution_arn": resp["executionArn"], "updated_at": now_iso()},
            condition=COND_EXISTS,
        )
    view = run_view(item)
    view["event"] = event_view(meta, index)
    return view


@app.get("/runs")
def list_runs(
    principal: Reader,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    if event_id is not None:
        meta, _i = _require_event(event_id, principal)
        runs = data.runs_for_event(str(meta["event_id"]), limit=200)
    else:
        runs = data.all_runs()
    labels = {m["event_id"]: i for i, m in enumerate(data.event_metas(), start=1)}
    start = offset_cursor(cursor)
    items = []
    for r in runs[start : start + limit]:
        view = run_view(r)
        if not _raw_ok(principal):
            judge_safe_run(view)
        if view.get("event_id") in labels:
            view["event_label"] = f"Event {labels[view['event_id']]}"
        items.append(view)
    nxt = start + limit
    return {"items": items, "next_cursor": encode_cursor({"o": nxt}) if nxt < len(runs) else None}


def _real_run_id(run: dict[str, Any], fallback: str) -> str:
    return str(run.get("run_id") or str(run.get("sk", "")).removeprefix("RUN#") or fallback)


def _run_or_404(run_id: str) -> dict[str, Any]:
    check_id(run_id, "run_id")
    run = data.find_run(run_id) or data.find_run(aliases.resolve_run(run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown run")
    return run


@app.get("/runs/{run_id}")
def get_run(run_id: str, principal: Reader) -> dict[str, Any]:
    run_item = _run_or_404(run_id)
    run_id = _real_run_id(run_item, run_id)
    view = run_view(run_item)
    if not _raw_ok(principal):
        judge_safe_run(view)
    if view.get("status") == "awaiting_approval" and "paused_at" not in view:
        pending, _last = _pending(100, None)
        stamps = sorted(
            str(p.get("gsi3sk", "")).split("#", 1)[0] for p in pending if p.get("run_id") == run_id
        )
        if stamps and stamps[0]:
            view["paused_at"] = stamps[0]
    event_id = str(view.get("event_id", ""))
    summary = data.read_result_json(event_id, "summary.json", run_item) if event_id else None
    if isinstance(summary, dict) and summary.get("run_id") in (None, run_id):
        for k in (
            "opencv_version",
            "opencv_hal",
            "cv2_file",
            "flavor",
            "verdict_counts",
            "thresholds_sha",
            "instance_type",
            "llm_provider",
            "planner_model",
            "reasons_model",
        ):
            if k not in view and summary.get(k) is not None:
                view[k] = summary[k]
        for k in ("wall_s", "cost_estimate_usd", "photos_processed"):
            if summary.get(k) is not None:
                view.setdefault(k, summary[k])
    found = data.event_meta(event_id) if event_id else None
    if found:
        view["event"] = event_view(*found)
    return view


AGENT_PROBE_TTL_S = 5.0
_agent_probe_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@app.get("/runs/{run_id}/agent")
def get_run_agent(run_id: str, principal: Reader) -> dict[str, Any]:
    """Which agent path the API would resume through and, for AgentCore, the runtime's live status.

    Read-only. The live probe (`{"mode": "status"}` InvokeAgentRuntime) runs only while the run is active,
    so judges looking at old runs never start a runtime session; answers are cached for a few seconds.
    """
    run = _run_or_404(run_id)
    run_id = _real_run_id(run, run_id)
    path = agent_resume.configured_path() or "none"
    out: dict[str, Any] = {"run_id": run_id, "path": path, "run_status": run.get("status")}
    host = run.get("agent_host")
    if isinstance(host, str):
        out["agent_host"] = host
    if path != "agentcore":
        out["live"] = None
        return out
    out["session_id"] = agent_resume.session_id_for(run, run_id)
    if run.get("status") not in ACTIVE_RUN_STATES:
        out["live"] = None
        out["live_skipped"] = "run is not active"
        return out
    now = time.monotonic()
    cached = _agent_probe_cache.get(run_id)
    if cached and now - cached[0] < AGENT_PROBE_TTL_S:
        out["live"] = _live_for(cached[1], principal)
        return out
    try:
        res = agent_resume.status(run_id, run)
        live: dict[str, Any] = {"status": res.get("status", "unknown"), "checked_at": now_iso()}
        ack = res.get("ack") or {}
        for k in ("error", "note", "pending_approvals"):
            if k in ack:
                live[k] = ack[k]
    except agent_resume.ResumeUnavailable as exc:
        live = {"status": "unreachable", "error": str(exc)[:200], "checked_at": now_iso()}
    _agent_probe_cache[run_id] = (now, live)
    if len(_agent_probe_cache) > 256:
        _agent_probe_cache.pop(next(iter(_agent_probe_cache)))
    out["live"] = _live_for(live, principal)
    return out


def _live_for(live: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """The judge never sees the probe's error text (S-13)."""
    return live if _raw_ok(principal) else {k: v for k, v in live.items() if k != "error"}


def _annotate(line: dict[str, Any]) -> dict[str, Any]:
    """Attach presigned contact-sheet URLs to approval requests so the timeline can show the sheet."""
    out = trace_view(line)
    items = (out.get("args") or {}).get("items") if out.get("type") == "tool_call" else None
    if isinstance(items, list):
        urls = [sheet_url_for_key(i.get("contact_sheet_key")) for i in items if isinstance(i, dict)]
        urls = [u for u in urls if u]
        if urls:
            out["contact_sheet_urls"] = urls
    return out


@app.get("/runs/{run_id}/trace")
def get_trace(
    run_id: str,
    principal: Reader,
    after: Annotated[int, Query(ge=-1, le=999_999)] = -1,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    group_id: str | None = None,
) -> dict[str, Any]:
    run = _run_or_404(run_id)
    run_id = _real_run_id(run, run_id)
    event_id = str(run.get("event_id") or str(run.get("pk", "")).removeprefix("EVENT#"))
    # Trace args name photos/groups no other response may have shown yet: learn the event's ids first so the
    # scrubber maps them to aliases instead of redacting them.
    aliases.preload_event(event_id)
    if group_id is None:
        page, more = data.trace_from_dynamo(run_id, after, limit)
        source = "dynamodb"
        # Fall back to results/<eventId>/trace.jsonl only when DynamoDB holds nothing for this run.
        if not page and (after < 0 or not data.trace_from_dynamo(run_id, -1, 1)[0]):
            lines = sorted(
                (line for line in data.full_trace(run_id, event_id) if int(line.get("seq", 0)) > after),
                key=lambda line: int(line.get("seq", 0)),
            )
            if lines:
                source = "s3"
                page, more = lines[:limit], len(lines) > limit
        views = [_annotate(line) for line in page]
        return {
            "run_id": run_id,
            "source": source,
            "items": views,
            "pivots": find_pivots(views),
            "next_after": views[-1]["seq"] if more and views else None,
        }
    check_id(group_id, "group_id")
    group_id = aliases.resolve_group(event_id, group_id, allow_raw=_raw_ok(principal))
    group = data.group_item(event_id, group_id)
    members = [
        m for m in (group or {}).get("members") or (group or {}).get("photo_ids") or [] if isinstance(m, str)
    ]
    full = [trace_view(line) for line in data.full_trace(run_id, event_id)]
    relevant = filter_for_group(full, group_id, members)
    keep = {line["seq"] for line in relevant}
    pivots = [p for p in find_pivots(full) if p["result_seq"] in keep and p["call_seq"] in keep]
    window = [line for line in relevant if line["seq"] > after]
    page = window[:limit]
    return {
        "run_id": run_id,
        "group_id": group_id,
        "source": "group-filter",
        "items": [_annotate(line) for line in page],
        "pivots": pivots,
        "next_after": page[-1]["seq"] if len(window) > limit and page else None,
    }


# ---------------------------------------------------------------- approvals

APPROVAL_FIELDS = (
    "approval_id",
    "run_id",
    "event_id",
    "group_id",
    "photo_id",
    "proposed",
    "reason",
    "reason_codes",
    "confidence",
    "created_at",
    "resume_error",
)


def _approval_id(item: dict[str, Any]) -> str:
    if isinstance(item.get("approval_id"), str):
        return str(item["approval_id"])
    return str(item.get("gsi3sk", "")).rsplit("#", 1)[-1]


def _approval_view(item: dict[str, Any]) -> dict[str, Any]:
    out = {k: item[k] for k in APPROVAL_FIELDS if k in item and item[k] is not None}
    out["approval_id"] = _approval_id(item)
    out.setdefault("event_id", str(item.get("pk", "")).removeprefix("EVENT#"))
    out.setdefault("created_at", str(item.get("gsi3sk", "")).split("#", 1)[0])
    event_id = str(out["event_id"])
    sheet = sheet_url_for_key(item.get("contact_sheet_key"))
    if sheet is None and isinstance(out.get("group_id"), str):
        sheet = sheet_url(event_id, out["group_id"])
    if sheet:
        out["contact_sheet_url"] = sheet
    members = item.get("members")
    if not isinstance(members, list) and isinstance(out.get("group_id"), str):
        group = data.group_item(event_id, out["group_id"])
        members = (group or {}).get("members") or (group or {}).get("photo_ids")
    if not isinstance(members, list) and isinstance(out.get("photo_id"), str):
        members = [out["photo_id"]]
    if isinstance(members, list):
        out["members"] = [
            {"photo_id": m, "proxy_url": proxy_url(event_id, m)} for m in members if isinstance(m, str)
        ]
    if isinstance(out.get("group_id"), str):
        out["group_label"] = group_label(out["group_id"], event_id)
    return out


def _pending(limit: int, start_key: dict[str, Any] | None) -> tuple[list[dict[str, Any]], Any]:
    return backend().query(
        data.decisions_table(), table_index("gsi3"), "PENDING", None, limit=limit, start_key=start_key
    )


@app.get("/approvals")
def list_approvals(
    _: Team,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    items, last = _pending(limit, key_cursor(cursor, "gsi3pk", "PENDING"))
    return {"items": [_approval_view(i) for i in items], "next_cursor": encode_cursor(last)}


VerdictMap = dict[Literal["keep", "reject", "review"], list[str]]


# LEAD_TEST_LABELLER (views): same rule as keepframe/agent/strands_agent.py. That labeller is the team
# exercising the resume path, never a photographer; its answers are stored as human_source = decided_by =
# "lead_test".
_LABELLER = re.compile(r"^[A-Za-z0-9 ._:@+/-]{1,64}$")


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "override"]
    note: str = Field(default="", max_length=500)
    # Required for an override: either one verdict for a single photo, or a keep/reject/review map for a group.
    human_verdict: Literal["keep", "reject", "review"] | VerdictMap | None = None
    # Who answered. The review desk never sends it; "lead-mechanism-test" marks a team mechanism test. Any other
    # value is stored verbatim as `labeller` and the answer stays a review-desk (ui) answer.
    labeller: str | None = Field(default=None, max_length=64)
    # Accepted so a client that says lead_test is never silently recorded as a person; nothing else is allowed.
    human_source: Literal["ui", "lead_test"] | None = None

    @field_validator("note")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("labeller")
    @classmethod
    def _labeller(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not _LABELLER.match(v):
            raise ValueError("labeller may only use letters, digits, space and . _ : @ + / -")
        return v

    def source(self) -> str:
        """`lead_test` for a team mechanism test, else `ui` (a person in the review desk)."""
        if self.labeller == LEAD_TEST_LABELLER or self.human_source == "lead_test":
            return "lead_test"
        return "ui"


def _find_pending(approval_id: str, event_id: str | None = None) -> dict[str, Any]:
    start: Any = None
    for _page in range(20):
        items, start = _pending(100, start)
        for item in items:
            if _approval_id(item) != approval_id:
                continue
            if (
                event_id
                and str(item.get("event_id") or str(item.get("pk", "")).removeprefix("EVENT#")) != event_id
            ):
                continue
            return item
        if not start:
            break
    raise HTTPException(status_code=404, detail="No pending approval with this id (already answered?)")


def _unalias_verdict(body: ApprovalDecision, item: dict[str, Any], principal: Principal) -> ApprovalDecision:
    """Photo aliases in an override's keep/reject/review map → real ids for this approval's event."""
    hv = body.human_verdict
    if not isinstance(hv, dict):
        return body
    event_id = str(item.get("event_id") or str(item.get("pk", "")).removeprefix("EVENT#"))
    real = {
        v: [aliases.resolve_photo(event_id, p, allow_raw=_raw_ok(principal)) for p in ids]
        for v, ids in hv.items()
    }
    return body.model_copy(update={"human_verdict": real})


def _human_verdict(body: ApprovalDecision, item: dict[str, Any]) -> Any:
    if body.decision == "approve":
        if item.get("proposed") is None:
            raise HTTPException(status_code=422, detail="Nothing proposed to approve; send an override")
        return item["proposed"]
    hv = body.human_verdict
    if hv is None:
        raise HTTPException(status_code=422, detail="An override needs human_verdict")
    if isinstance(hv, str):
        if item.get("group_id") and not item.get("photo_id"):
            raise HTTPException(status_code=422, detail="A group override needs a keep/reject/review map")
        return hv
    members = {m["photo_id"] for m in _approval_view(item).get("members", [])}
    named = [p for ids in hv.values() for p in ids]
    if len(named) != len(set(named)):
        raise HTTPException(status_code=422, detail="A photo appears under more than one verdict")
    if members and not set(named) <= members:
        raise HTTPException(status_code=422, detail="human_verdict names photos outside this group")
    if not hv.get("keep"):
        # Policy 4: never reject a whole group.
        raise HTTPException(status_code=422, detail="Keep at least one photo from the group")
    for ids in hv.values():
        for pid in ids:
            check_id(pid, "photo_id")
    return {k: v for k, v in hv.items() if v}


@app.post("/approvals/{approval_id}")
def answer_approval(approval_id: str, body: ApprovalDecision, principal: Team) -> dict[str, Any]:
    check_id(approval_id, "approval_id")
    resolved = aliases.resolve_approval(approval_id, allow_raw=_raw_ok(principal))
    scoped_event, approval_id = resolved if isinstance(resolved, tuple) else (None, resolved)
    item = _find_pending(approval_id, scoped_event)
    body = _unalias_verdict(body, item, principal)
    verdict = _human_verdict(body, item)
    decided_at = now_iso()
    source = body.source()
    human = {
        "decision": body.decision,
        "human_verdict": verdict,
        "note": body.note,
        "decided_at": decided_at,
        "human_source": source,
        # Non-human sources are their own decided_by (keepframe/tools/decision.py NON_HUMAN_SOURCES).
        "decided_by": "lead_test" if source == "lead_test" else "human",
    }
    if body.labeller:
        human["labeller"] = body.labeller
    stale_before = (datetime.now(UTC) - timedelta(seconds=RESUME_LOCK_S)).isoformat().replace("+00:00", "Z")
    try:
        backend().update_item(
            data.decisions_table(),
            item["pk"],
            item["sk"],
            set_values={"human_decision": human, "resume_lock": decided_at, "updated_at": decided_at},
            condition=COND_PENDING_UNLOCKED,
            cond_value=stale_before,
        )
    except ConditionFailed as exc:
        raise HTTPException(
            status_code=409, detail="This approval is being answered or was already answered"
        ) from exc

    run_id = str(item.get("run_id", ""))
    decision: dict[str, Any] = {
        "id": approval_id,
        "human_verdict": verdict,
        "note": body.note,
        "human_source": source,
    }
    if body.labeller:
        decision["labeller"] = body.labeller  # the agent maps lead-mechanism-test to lead_test itself too
    for k in ("group_id", "photo_id", "interrupt_id", "interrupt_name"):
        if item.get(k):
            decision[k] = item[k]
    run = data.find_run(run_id) if run_id else None
    if run is not None:
        # Durable copy for the audit trail and for an agent that re-reads its answers from the store.
        run = backend().update_item(
            data.decisions_table(),
            run["pk"],
            run["sk"],
            set_values={"resume_decisions": [decision], "updated_at": now_iso()},
        )
    try:
        ack = agent_resume.resume(run_id, [decision], run)
        if run is not None and ack.get("via") == "agentcore":
            _stamp_agent_status(run, str(ack.get("status", "")))
    except agent_resume.NotConfigured:
        # No agent to hand back to: apply the human verdicts directly so the review still completes.
        written = _apply_human_verdicts(item, verdict, body.note, run_id, source, body.labeller)
        ack = {"via": "api-direct", "status": 200, "ack": {"written": written}}
    except Exception as exc:
        reason = str(exc) if isinstance(exc, agent_resume.ResumeUnavailable) else type(exc).__name__
        backend().update_item(
            data.decisions_table(),
            item["pk"],
            item["sk"],
            set_values={"resume_error": reason[:200], "updated_at": now_iso()},
            remove=("resume_lock",),
        )
        log.error(
            json.dumps({"msg": "agent resume failed", "approval_id": approval_id, "error": reason[:200]})
        )
        raise HTTPException(
            status_code=502, detail="Decision saved, but the agent did not resume. Try again shortly."
        ) from exc
    backend().update_item(
        data.decisions_table(),
        item["pk"],
        item["sk"],
        set_values={
            "approval_status": "resolved",
            "resolved_at": now_iso(),
            "resolved_via": ack.get("via"),
            "updated_at": now_iso(),
        },
        remove=("gsi3pk", "gsi3sk", "resume_lock", "resume_error"),
    )
    return {"approval_id": approval_id, "status": "resolved", "human_decision": human, "resume": ack}


# AgentCore answer status -> RUN# status. The agent writes RUN# itself; this only fills the gap until it does.
AGENT_TO_RUN_STATUS = {
    "running": "running",
    "paused": "awaiting_approval",
    "done": "succeeded",
    "error": "failed",
}


def _stamp_agent_status(run: dict[str, Any], agent_status: str) -> None:
    """Set RUN# status from the runtime's synchronous answer, unless the agent has written RUN# since we
    read it (optimistic on `updated_at`), so a newer agent write is never overwritten."""
    new = AGENT_TO_RUN_STATUS.get(agent_status)
    if not new or run.get("status") == new or not isinstance(run.get("updated_at"), str):
        return
    values: dict[str, Any] = {"status": new, "updated_at": now_iso(), "agent_status_via": "agentcore"}
    remove: tuple[str, ...] = ()
    if new == "awaiting_approval":
        values["paused_at"] = values["updated_at"]
    else:
        remove = ("paused_at",)
    try:
        backend().update_item(
            data.decisions_table(),
            run["pk"],
            run["sk"],
            set_values=values,
            remove=remove,
            condition=COND_UNCHANGED,
            cond_value=run["updated_at"],
        )
    except ConditionFailed:
        pass  # the agent already wrote a newer state


def _apply_human_verdicts(
    item: dict[str, Any],
    verdict: Any,
    note: str,
    run_id: str,
    source: str = "ui",
    labeller: str | None = None,
) -> int:
    """Write human verdicts onto PHOTO# items (decided_by=human; lead_test for a team mechanism test) when no
    agent can resume.

    Reason codes follow the design: the group keep(s) get KEEP_BEST_OF_GROUP, group rejects BURST_NOT_BEST;
    a single-photo reject keeps the agent's existing codes. The stored model confidence is left as is;
    views hide it for human decisions.
    """
    event_id = str(item.get("event_id") or str(item.get("pk", "")).removeprefix("EVENT#"))
    group_id = item.get("group_id")
    if isinstance(verdict, str):
        targets = {str(item.get("photo_id")): verdict}
    else:
        targets = {pid: v for v, ids in verdict.items() for pid in (ids if isinstance(ids, list) else [ids])}
    lead_test = source == "lead_test"
    now = now_iso()
    written = 0
    for pid, v in targets.items():
        check_id(pid, "photo_id")
        existing = backend().get_item(data.decisions_table(), f"EVENT#{event_id}", f"PHOTO#{pid}") or {}
        codes = {
            "keep": ["KEEP_BEST_OF_GROUP"]
            if group_id
            else (existing.get("reason_codes") or ["KEEP_SINGLETON"]),
            "reject": ["BURST_NOT_BEST"]
            if group_id
            else (existing.get("reason_codes") or ["REVIEW_LOW_CONFIDENCE"]),
            "review": ["REVIEW_LOW_CONFIDENCE"],
        }[v]
        values: dict[str, Any] = {
            "photo_id": pid,
            "verdict": v,
            "reason_codes": codes,
            "reason_text": (
                "Team mechanism test, not a photographer judgement"
                if lead_test
                else (f"Decided by a person: {note}" if note else "Decided by a person")
            )[:140],
            "decided_by": "lead_test" if lead_test else "human",
            "human_source": source,
            "human_note": note[:200],
            "run_id": run_id or existing.get("run_id", ""),
            "updated_at": now,
            "gsi1pk": f"EVENT#{event_id}#VERDICT#{v}",
        }
        if labeller:
            values["labeller"] = labeller
        if group_id:
            values["group_id"] = group_id
            values["gsi2pk"] = f"GROUP#{group_id}"
        backend().update_item(data.decisions_table(), f"EVENT#{event_id}", f"PHOTO#{pid}", set_values=values)
        written += 1
    return written


# ---------------------------------------------------------------- images (S-1)

_IMG_ITEM = re.compile(r"^(ph_[0-9a-f]{10}|g_ev_\d{2,4}_\d{1,6}|gx_[0-9a-f]{10})$")


@app.get("/img/{ev}/{kind}/{name}", include_in_schema=False)
def get_image(ev: str, kind: str, name: str, exp: int, sig: str) -> Response:
    """A frame or contact sheet by alias. The link is the authorisation (HMAC over the aliases and expiry,
    issued only inside an authenticated response), like the presigned URL it replaces; <img> cannot send a
    bearer token. Bytes are streamed so no S3 key (proxy/<eventId>/<photoId>.jpg) ever reaches the client."""
    item = name.removesuffix(".jpg")
    if kind not in IMG_KINDS or not name.endswith(".jpg") or not _IMG_ITEM.match(item):
        raise HTTPException(status_code=404)
    if exp < int(time.time()) or exp > int(time.time()) + 2 * settings().presign_ttl_s + 120:
        raise HTTPException(status_code=403, detail="Expired link")
    if not hmac.compare_digest(image_sig(kind, ev, item, exp), sig):
        raise HTTPException(status_code=403, detail="Invalid link")
    event_id = aliases.resolve_event(ev, allow_raw=False)
    if kind == "p":
        real = aliases.resolve_photo(event_id, item, allow_raw=False)
    else:
        real = aliases.resolve_group(event_id, item, allow_raw=False)
    prefix, ext = IMG_KINDS[kind]
    raw = backend().get_object(f"{prefix}/{event_id}/{real}.{ext}")
    if raw is None:
        raise HTTPException(status_code=404)
    return Response(
        content=raw,
        media_type="image/jpeg",
        headers={
            "Cache-Control": f"private, max-age={min(300, settings().presign_ttl_s)}",
            "Referrer-Policy": "no-referrer",
        },
    )


# ---------------------------------------------------------------- bench


@app.get("/bench")
def get_bench(_: Reader) -> Any:
    doc = data.read_json("bench/results/latest/results.json")
    if not isinstance(doc, dict):
        raise HTTPException(
            status_code=404, detail="No benchmark published yet (bench/results/latest/results.json)"
        )
    return bench_contract(doc)


HOST_FIELDS = (
    "label",
    "instance_type",
    "opencv_version",
    "cv2_file",
    "images_per_sec",
    "cost_per_1k_usd",
    "cpu_util_mean",
    "peak_rss_mb",
    "n_images",
    "workers",
    "dataset",
    # The OpenCV HAL actually loaded (cv2 build info): carotene | kleidicv-26.03+carotene. The UI keys the
    # KleidiCV series on this, never on the label.
    "opencv_hal",
    "custom_hal",
    "cxx_compiler",
)


def bench_contract(doc: dict[str, Any]) -> dict[str, Any]:
    """Normalise results.json to the published shape
    `{generated_at, hosts: [{label, instance_type, opencv_version, cv2_file, images_per_sec, cost_per_1k_usd,
    cpu_util_mean, peak_rss_mb, stages: [{name, p50_ms, p95_ms}]}]}`.

    Also accepts the older flat shape (`hosts[].host_label/images_per_s/usd_per_1k/cpu_util_pct` plus a
    top-level `stages[{stage, host_label, p50_ms, p95_ms}]`) so a stale file still renders.
    """
    flat: dict[str, list[dict[str, Any]]] = {}
    for r in doc.get("stages") or []:
        if isinstance(r, dict) and r.get("host_label") and r.get("stage"):
            flat.setdefault(str(r["host_label"]), []).append(
                {"name": r["stage"], "p50_ms": r.get("p50_ms"), "p95_ms": r.get("p95_ms")}
            )
    hosts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in doc.get("hosts") or []:
        if not isinstance(h, dict):
            continue
        label = str(h.get("label") or h.get("host_label") or "")
        if not label or label in seen:
            continue
        seen.add(label)
        out: dict[str, Any] = {k: h[k] for k in HOST_FIELDS if h.get(k) is not None}
        out["label"] = label
        for new_k, old_k in (
            ("images_per_sec", "images_per_s"),
            ("cost_per_1k_usd", "usd_per_1k"),
            ("cpu_util_mean", "cpu_util_pct"),
        ):
            if new_k not in out and h.get(old_k) is not None:
                out[new_k] = h[old_k]
        if (
            "instance_type" not in out
            and isinstance(doc.get("instance_type"), str)
            and "/" not in doc["instance_type"]
        ):
            out["instance_type"] = doc["instance_type"]
        stages = h.get("stages")
        if isinstance(stages, list) and stages:
            out["stages"] = [
                {
                    "name": st.get("name") or st.get("stage"),
                    "p50_ms": st.get("p50_ms"),
                    "p95_ms": st.get("p95_ms"),
                }
                for st in stages
                if isinstance(st, dict) and (st.get("name") or st.get("stage"))
            ]
        else:
            out["stages"] = flat.get(label, [])
        if isinstance(h.get("worker"), dict):
            out["worker"] = h["worker"]
        hosts.append(out)
    for label, stages in flat.items():  # flat rows for a host with no host entry
        if label not in seen:
            hosts.append({"label": label, "stages": stages})
    result: dict[str, Any] = {
        "generated_at": doc.get("generated_at") or doc.get("date"),
        "hosts": hosts,
    }
    for k in ("synthetic", "n_images", "dataset"):
        if doc.get(k) is not None:
            result[k] = doc[k]
    return result


# ---------------------------------------------------------------- local object server (LOCAL_MODE only)

if settings().local_mode:
    from fastapi.responses import FileResponse

    @app.get("/_local/objects/{key:path}", include_in_schema=False)
    def local_object(key: str, expires: int, sig: str) -> FileResponse:
        b = backend()
        if not isinstance(b, LocalBackend) or not key.startswith(("proxy/", "sheets/", "preview/")):
            raise HTTPException(status_code=404)
        if not b.verify(key, expires, sig):
            raise HTTPException(status_code=403, detail="Expired or invalid signature")
        path = b._path(key)
        if not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})


handler = Mangum(app, lifespan="off")

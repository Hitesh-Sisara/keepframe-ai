"""Read models over the decisions/traces tables with S3 fallbacks (results/<eventId>/...)."""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .backend import RangeCond, backend, table_index
from .config import settings

RUNS_GSI1PK = "RUNS"  # sparse GSI1 partition that lists every RUN# item, newest last (ULID sort keys)
MAX_TRACE_LINES = 20_000


def decisions_table() -> str:
    return settings().decisions_table


def traces_table() -> str:
    return settings().traces_table


def read_json(key: str) -> Any:
    raw = backend().get_object(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


def read_jsonl(key: str, limit: int = 200_000) -> list[dict[str, Any]] | None:
    raw = backend().get_object(key)
    if raw is None:
        return None
    out: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
            if len(out) >= limit:
                break
    return out


# ---------------------------------------------------------------- per-run result keys

# 2026-10-09: the worker writes results/<eventId>/<runId>/<name> (immutable) and, for one release,
# the unversioned results/<eventId>/<name> latest copy. Readers try the run's own key first.
_RUN_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def run_prefix(event_id: str, run: dict[str, Any] | None) -> str | None:
    """results/<eventId>/<runId>/ for a run: RUN#.results_run_prefix when it is that exact shape, else built
    from run_id. None when there is no usable run."""
    if not run:
        return None
    stamped = run.get("results_run_prefix")
    if isinstance(stamped, str) and stamped.startswith(f"results/{event_id}/") and ".." not in stamped:
        seg = stamped.removeprefix(f"results/{event_id}/").rstrip("/")
        if _RUN_SEGMENT.match(seg):
            return f"results/{event_id}/{seg}/"
    rid = run.get("run_id") or str(run.get("sk", "")).removeprefix("RUN#")
    if isinstance(rid, str) and _RUN_SEGMENT.match(rid) and ".." not in rid:
        return f"results/{event_id}/{rid}/"
    return None


def result_keys(event_id: str, name: str, run: dict[str, Any] | None = None) -> list[str]:
    """Candidate keys, most specific first: the given run's (or the event's latest run's) versioned key, then
    the unversioned latest copy (runs written before per-run keys)."""
    if run is None:
        latest = runs_for_event(event_id, limit=1)
        run = latest[0] if latest else None
    keys = []
    prefix = run_prefix(event_id, run)
    if prefix:
        keys.append(f"{prefix}{name}")
    keys.append(f"results/{event_id}/{name}")
    return keys


def read_result_json(event_id: str, name: str, run: dict[str, Any] | None = None) -> Any:
    for key in result_keys(event_id, name, run):
        doc = read_json(key)
        if doc is not None:
            return doc
    return None


def read_result_jsonl(
    event_id: str, name: str, run: dict[str, Any] | None = None, limit: int = 200_000
) -> list[dict[str, Any]] | None:
    for key in result_keys(event_id, name, run):
        rows = read_jsonl(key, limit=limit)
        if rows is not None:
            return rows
    return None


# ---------------------------------------------------------------- runs


def find_run(run_id: str) -> dict[str, Any] | None:
    items, _ = backend().query(
        decisions_table(), table_index("gsi1"), RUNS_GSI1PK, RangeCond("eq", f"RUN#{run_id}"), limit=1
    )
    if items:
        return items[0]
    # Run items written without the RUNS pointer: look under each known event (small N).
    for event_id in event_ids():
        item = backend().get_item(decisions_table(), f"EVENT#{event_id}", f"RUN#{run_id}")
        if item is not None:
            return item
    return None


def runs_for_event(event_id: str, limit: int = 50) -> list[dict[str, Any]]:
    items, _ = backend().query(
        decisions_table(),
        table_index("table"),
        f"EVENT#{event_id}",
        RangeCond("begins_with", "RUN#"),
        limit=limit,
        forward=False,
    )
    return items


def all_runs(limit: int = 200) -> list[dict[str, Any]]:
    """RUN# items from the sparse RUNS index, newest first.

    Items written without `gsi1pk = "RUNS"` are only found by the per-event fallback, which costs one query
    per event, so it runs only when the index partition is empty.
    """
    seen: dict[str, dict[str, Any]] = {}
    items, _ = backend().query(
        decisions_table(),
        table_index("gsi1"),
        RUNS_GSI1PK,
        RangeCond("begins_with", "RUN#"),
        limit=limit,
        forward=False,
    )
    for it in items:
        seen[str(it["sk"])] = it
    if not seen:
        # Serial on purpose: the DynamoDB resource is not thread-safe, and this path is the rare fallback.
        for event_id in event_ids():
            for it in runs_for_event(event_id):
                seen.setdefault(str(it["sk"]), it)
    runs = sorted(seen.values(), key=lambda r: (str(r.get("started_at", "")), str(r["sk"])), reverse=True)
    return runs[:limit]


# ---------------------------------------------------------------- events

MAX_EVENTS = 200


META_TTL_S = 60.0
_meta_lock = threading.Lock()
_meta_cache: dict[str, Any] = {"at": 0.0, "key": None, "metas": None}


def event_metas() -> list[dict[str, Any]]:
    """manifests/*.meta.json, oldest export first so the 1-based index is stable as events are added.

    Cached per process for META_TTL_S (a warm Lambda serves many requests; manifests change only when an
    event is exported), and the meta objects are fetched in parallel.
    """
    key = (settings().work_bucket, settings().local_root if settings().local_mode else None)
    now = time.monotonic()
    with _meta_lock:
        if (
            _meta_cache["key"] == key
            and _meta_cache["metas"] is not None
            and now - _meta_cache["at"] < META_TTL_S
        ):
            return list(_meta_cache["metas"])
    keys = backend().list_keys("manifests/", ".meta.json", max_keys=MAX_EVENTS)
    metas: list[dict[str, Any]] = []
    if keys:
        with ThreadPoolExecutor(max_workers=min(8, len(keys))) as pool:
            for doc in pool.map(read_json, keys):
                if isinstance(doc, dict) and isinstance(doc.get("event_id"), str):
                    metas.append(doc)
    metas.sort(key=lambda m: (str(m.get("exported_at", "")), str(m["event_id"])))
    with _meta_lock:
        _meta_cache.update(at=now, key=key, metas=metas)
    return list(metas)


def clear_meta_cache() -> None:
    with _meta_lock:
        _meta_cache.update(at=0.0, key=None, metas=None)
        _groups_cache.clear()
        _decisions_cache.clear()


def event_ids() -> list[str]:
    return [m["event_id"] for m in event_metas()]


def event_meta(event_id: str) -> tuple[dict[str, Any], int] | None:
    for i, m in enumerate(event_metas(), start=1):
        if m["event_id"] == event_id:
            return m, i
    return None


# ---------------------------------------------------------------- photos / decisions


_decisions_cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}


def decisions_from_s3(event_id: str) -> dict[str, dict[str, Any]]:
    """Last write wins per photo, matching an append-only decisions.jsonl. Cached per process for
    GROUPS_TTL_S: an event page reads it once per page of groups."""
    now = time.monotonic()
    key = f"{settings().work_bucket}|{settings().local_root}|{event_id}"
    with _meta_lock:
        hit = _decisions_cache.get(key)
        if hit and now - hit[0] < GROUPS_TTL_S:
            return dict(hit[1])
    rows = read_result_jsonl(event_id, "decisions.jsonl") or []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        pid = row.get("photo_id")
        if isinstance(pid, str):
            out[pid] = row
    with _meta_lock:
        _decisions_cache[key] = (now, out)
    return dict(out)


def photo_items(event_id: str, photo_ids: list[str]) -> dict[str, dict[str, Any]]:
    keys = [(f"EVENT#{event_id}", f"PHOTO#{pid}") for pid in photo_ids]
    items = backend().batch_get(decisions_table(), keys) if keys else []
    return {str(i["sk"]).removeprefix("PHOTO#"): i for i in items}


def photo_item(event_id: str, photo_id: str) -> dict[str, Any] | None:
    item = backend().get_item(decisions_table(), f"EVENT#{event_id}", f"PHOTO#{photo_id}")
    if item is not None:
        return item
    return decisions_from_s3(event_id).get(photo_id)


def event_verdict_counts(event_id: str) -> dict[str, int]:
    idx = table_index("gsi1")
    return {
        v: backend().count(decisions_table(), idx, f"EVENT#{event_id}#VERDICT#{v}")
        for v in ("keep", "reject", "review")
    }


# ---------------------------------------------------------------- groups


GROUPS_TTL_S = 30.0
_groups_cache: dict[str, tuple[float, Any]] = {}


def groups_from_s3(event_id: str) -> tuple[list[dict[str, Any]], int] | None:
    """The worker's grouping for the event's latest run (results/<eventId>/<runId>/groups.json, else the
    unversioned copy), cached per process for GROUPS_TTL_S."""
    now = time.monotonic()
    key = f"{settings().work_bucket}|{settings().local_root}|{event_id}"
    with _meta_lock:
        hit = _groups_cache.get(key)
        if hit and now - hit[0] < GROUPS_TTL_S:
            return hit[1]
    doc = read_result_json(event_id, "groups.json")
    result: tuple[list[dict[str, Any]], int] | None = None
    if isinstance(doc, dict):
        groups = []
        for g in doc.get("groups") or []:
            if not isinstance(g, dict) or not isinstance(g.get("group_id"), str):
                continue
            members = [m for m in (g.get("photo_ids") or g.get("members") or []) if isinstance(m, str)]
            groups.append({**g, "members": members, "size": g.get("size", len(members))})
        singletons = doc.get("singletons")
        n_single = len(singletons) if isinstance(singletons, list) else int(singletons or 0)
        result = (groups, n_single)
    with _meta_lock:
        _groups_cache[key] = (now, result)
    if result is None:
        return None
    return [dict(g) for g in result[0]], result[1]


MAX_GROUP_ITEMS = 5000


def dynamo_groups(event_id: str) -> list[dict[str, Any]]:
    """Every GROUP# item for the event (paged Query; a few hundred per event)."""
    out: list[dict[str, Any]] = []
    start: Any = None
    while len(out) < MAX_GROUP_ITEMS:
        items, start = backend().query(
            decisions_table(),
            table_index("table"),
            f"EVENT#{event_id}",
            RangeCond("begins_with", "GROUP#"),
            limit=500,
            start_key=start,
        )
        out.extend(items)
        if not start:
            break
    return out


def _members_of(g: dict[str, Any]) -> list[str]:
    raw = g.get("members") or g.get("photo_ids") or []
    if isinstance(raw, list) and raw:
        return [m for m in raw if isinstance(m, str)]
    # A GROUP# row the agent wrote only for an approval carries membership in its proposal.
    proposed = g.get("proposed")
    ids: list[str] = []
    if isinstance(proposed, dict):
        for v in ("keep", "review", "reject"):
            vals = proposed.get(v)
            for m in vals if isinstance(vals, list) else [vals]:
                if isinstance(m, str) and m not in ids:
                    ids.append(m)
    return ids


# Fields where the worker's groups.json is authoritative; the agent's GROUP# row fills the rest.
_MEMBERSHIP = ("members", "size", "span_s")


def merged_groups(event_id: str) -> tuple[list[dict[str, Any]], str]:
    """All groups of an event: results/<eventId>/groups.json for membership, size, span and order, with
    the agent's GROUP# rows overlaid (best pick, margin, ranking, approval state, human decision). Rows
    only in DynamoDB are appended. Groups with no members are dropped. Returns (groups, source)."""
    found = groups_from_s3(event_id)
    base = found[0] if found else []
    ddb = {
        str(it.get("group_id") or str(it.get("sk", "")).removeprefix("GROUP#")): it
        for it in dynamo_groups(event_id)
    }
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for g in base:
        gid = g["group_id"]
        seen.add(gid)
        over = ddb.get(gid)
        if over:
            item = {
                **g,
                **{k: v for k, v in over.items() if k not in _MEMBERSHIP and v not in (None, "", [])},
            }
            if not g.get("members") and _members_of(over):
                item["members"] = _members_of(over)
        else:
            item = dict(g)
        merged.append(item)
    for gid, over in ddb.items():
        if gid in seen:
            continue
        merged.append({**over, "members": _members_of(over)})
    for g in merged:
        members = _members_of(g)
        g["members"] = members
        if not isinstance(g.get("size"), (int, float)) or not g.get("size"):
            g["size"] = len(members)
    merged = [g for g in merged if g["members"]]
    source = "merged" if base and ddb else ("s3" if base else "dynamodb")
    return merged, source


def group_item(event_id: str, group_id: str) -> dict[str, Any] | None:
    """One group, merged the same way as merged_groups (DynamoDB row over the groups.json entry)."""
    item = backend().get_item(decisions_table(), f"EVENT#{event_id}", f"GROUP#{group_id}")
    base = None
    found = groups_from_s3(event_id)
    if found:
        base = next((g for g in found[0] if g["group_id"] == group_id), None)
    if item is None:
        return base
    if base is None:
        return {**item, "members": _members_of(item)} if _members_of(item) else item
    out = {**base, **{k: v for k, v in item.items() if k not in _MEMBERSHIP and v not in (None, "", [])}}
    out["members"] = base.get("members") or _members_of(item)
    return out


# ---------------------------------------------------------------- traces


def trace_from_dynamo(run_id: str, after: int, limit: int) -> tuple[list[dict[str, Any]], bool]:
    cond = RangeCond("gt", f"{after:06d}") if after >= 0 else None
    items, last = backend().query(traces_table(), table_index("table"), f"RUN#{run_id}", cond, limit=limit)
    return items, last is not None


def full_trace(run_id: str, event_id: str | None) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    after = -1
    while len(lines) < MAX_TRACE_LINES:
        page, more = trace_from_dynamo(run_id, after, 1000)
        lines.extend(page)
        if not more or not page:
            break
        after = int(str(page[-1]["sk"]))
    if lines or not event_id:
        return lines
    rows = read_result_jsonl(event_id, "trace.jsonl", {"run_id": run_id}, limit=MAX_TRACE_LINES * 5) or []
    return [r for r in rows if r.get("run_id") in (None, run_id)]

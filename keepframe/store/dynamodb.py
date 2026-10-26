"""The only writer of ``keepframe-decisions`` and ``keepframe-traces``.

Backends (env ``CULL_DDB``):
  * ``aws``     boto3 DynamoDB resource (default when ``WORK_BUCKET`` is set)
  * ``local``   JSONL files under ``$CULL_LOCAL_ROOT/dynamodb/<table>.jsonl`` -- the exact layout the API's
                LocalBackend reads (default in LOCAL_MODE), so the UI works against a local agent run
  * ``memory``  process-local dicts (tests); no moto, no network

Item shapes (read by the API):
  RUN#<runId>      gsi1pk="RUNS" + run_id, event_id, status, started_at (if_not_exists), updated_at, paused_at,
                   ended_at (terminal only); agent_started_at (once), agent_ended_at, agent_wall_s,
                   agent_escalations, tool_call_count, agent_verdict_counts, agent_decided, llm_provider,
                   planner_model, reasons_model, agent_tokens_in, agent_tokens_out (planner, summed over
                   every resume), agent_photos, agent_host (agentcore|fargate|local), run_kind (once). The worker owns worker_*.
  PHOTO#<photoId>  verdict, reason_codes, reason_text, confidence, group_id, decided_by, scores, run_id,
                   updated_at, gsi1pk=EVENT#<e>#VERDICT#<v>, gsi2pk=GROUP#<g> (only when grouped)
  GROUP#<groupId>  members, best_photo_id, margin, tie, span_s, size, ranking, run_id, gsi2pk
  pending approval: the GROUP#/PHOTO# item itself gets gsi3pk="PENDING", gsi3sk="<ts>#<eventId>#<id>",
                   approval_id, proposed, reason, confidence, contact_sheet_key, interrupt_id, interrupt_name
There is deliberately no delete method: nothing in this package can remove an item.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from keepframe.store.tables import DECISIONS_TABLE, TRACES_TABLE

Item = dict[str, Any]

RUNS_GSI1PK = "RUNS"
PENDING = "PENDING"


# --------------------------------------------------------------------------- value conversion


def to_dynamo(value: Any) -> Any:
    """floats -> Decimal (DynamoDB rejects float), drop None inside maps, recurse lists/maps."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return Decimal(str(round(value, 6)))
    if isinstance(value, int):
        return value
    if isinstance(value, Mapping):
        return {str(k): to_dynamo(v) for k, v in value.items() if v is not None}
    if isinstance(value, list | tuple):
        return [to_dynamo(v) for v in value]
    return value


def plain(value: Any) -> Any:
    """Decimal -> int/float, recursively (inverse of to_dynamo)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


# --------------------------------------------------------------------------- backends


class Backend(Protocol):
    def put(self, table: str, item: Item) -> None: ...
    def update(self, table: str, pk: str, sk: str, set_values: Mapping[str, Any],
               remove: Iterable[str] = (), set_if_absent: Mapping[str, Any] | None = None) -> None: ...
    def get(self, table: str, pk: str, sk: str) -> Item | None: ...
    def query(self, table: str, pk: str, sk_prefix: str = "") -> list[Item]: ...
    def query_index(self, table: str, attr: str, value: str) -> list[Item]: ...


class MemoryBackend:
    """Thread-safe dict store. Also the base for LocalFileBackend."""

    kind = "memory"

    def __init__(self) -> None:
        self._tables: dict[str, dict[tuple[str, str], Item]] = {}
        self._lock = threading.RLock()

    def _t(self, table: str) -> dict[tuple[str, str], Item]:
        return self._tables.setdefault(table, {})

    def _changed(self, table: str) -> None:
        """Hook for persistence."""

    def put(self, table: str, item: Item) -> None:
        with self._lock:
            self._t(table)[(item["pk"], item["sk"])] = json.loads(json.dumps(plain(item), default=str))
            self._changed(table)

    def update(self, table: str, pk: str, sk: str, set_values: Mapping[str, Any],
               remove: Iterable[str] = (), set_if_absent: Mapping[str, Any] | None = None) -> None:
        with self._lock:
            cur = self._t(table).setdefault((pk, sk), {"pk": pk, "sk": sk})
            cur.update(json.loads(json.dumps(plain(dict(set_values)), default=str)))
            for k, v in (set_if_absent or {}).items():
                if v is not None and k not in cur:
                    cur[k] = json.loads(json.dumps(plain(v), default=str))
            for k in remove:
                cur.pop(k, None)
            self._changed(table)

    def get(self, table: str, pk: str, sk: str) -> Item | None:
        with self._lock:
            it = self._t(table).get((pk, sk))
            return json.loads(json.dumps(it)) if it is not None else None

    def query(self, table: str, pk: str, sk_prefix: str = "") -> list[Item]:
        with self._lock:
            rows = [v for (p, s), v in self._t(table).items() if p == pk and s.startswith(sk_prefix)]
        return [json.loads(json.dumps(r)) for r in sorted(rows, key=lambda r: r["sk"])]

    def query_index(self, table: str, attr: str, value: str) -> list[Item]:
        with self._lock:
            rows = [v for v in self._t(table).values() if v.get(attr) == value]
        return [json.loads(json.dumps(r)) for r in rows]


class LocalFileBackend(MemoryBackend):
    """JSONL per table, same files the API's LocalBackend reads/writes."""

    kind = "local"

    def __init__(self, root: str | os.PathLike[str]) -> None:
        super().__init__()
        self.root = Path(root)

    def _path(self, table: str) -> Path:
        return self.root / "dynamodb" / f"{table}.jsonl"

    def _t(self, table: str) -> dict[tuple[str, str], Item]:
        # Re-read every access: the API process may have written (answers, resume_decisions).
        p = self._path(table)
        rows: dict[tuple[str, str], Item] = {}
        if p.exists():
            for line in p.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    rows[(r["pk"], r["sk"])] = r
        self._tables[table] = rows
        return rows

    def _changed(self, table: str) -> None:
        p = self._path(table)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in self._tables[table].values()))
        tmp.replace(p)


class AwsBackend:
    kind = "aws"

    def __init__(self) -> None:
        import boto3

        self._ddb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
        self._tables: dict[str, Any] = {}

    def _t(self, table: str) -> Any:
        if table not in self._tables:
            self._tables[table] = self._ddb.Table(table)
        return self._tables[table]

    def put(self, table: str, item: Item) -> None:
        self._t(table).put_item(Item=to_dynamo(item))

    def update(self, table: str, pk: str, sk: str, set_values: Mapping[str, Any],
               remove: Iterable[str] = (), set_if_absent: Mapping[str, Any] | None = None) -> None:
        vals = {k: v for k, v in set_values.items() if v is not None and k not in ("pk", "sk")}
        once = {k: v for k, v in (set_if_absent or {}).items() if v is not None and k not in vals}
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        sets, rems = [], []
        for i, (k, v) in enumerate(vals.items()):
            names[f"#s{i}"] = k
            values[f":v{i}"] = to_dynamo(v)
            sets.append(f"#s{i} = :v{i}")
        for i, (k, v) in enumerate(once.items()):
            names[f"#o{i}"] = k
            values[f":o{i}"] = to_dynamo(v)
            sets.append(f"#o{i} = if_not_exists(#o{i}, :o{i})")
        for j, k in enumerate(remove):
            names[f"#r{j}"] = k
            rems.append(f"#r{j}")
        expr = ""
        if sets:
            expr += "SET " + ", ".join(sets)
        if rems:
            expr += " REMOVE " + ", ".join(rems)
        if not expr:
            return
        kw: dict[str, Any] = {"Key": {"pk": pk, "sk": sk}, "UpdateExpression": expr.strip(),
                              "ExpressionAttributeNames": names}
        if values:
            kw["ExpressionAttributeValues"] = values
        self._t(table).update_item(**kw)

    def get(self, table: str, pk: str, sk: str) -> Item | None:
        it = self._t(table).get_item(Key={"pk": pk, "sk": sk}, ConsistentRead=True).get("Item")
        return plain(it) if it else None

    def query(self, table: str, pk: str, sk_prefix: str = "") -> list[Item]:
        from boto3.dynamodb.conditions import Key

        cond: Any = Key("pk").eq(pk)
        if sk_prefix:
            cond = cond & Key("sk").begins_with(sk_prefix)
        return self._collect(table, KeyConditionExpression=cond, ConsistentRead=True)

    def query_index(self, table: str, attr: str, value: str) -> list[Item]:
        from boto3.dynamodb.conditions import Key

        index = {"gsi1pk": "gsi1", "gsi2pk": "gsi2", "gsi3pk": "gsi3"}[attr]
        return self._collect(table, IndexName=index, KeyConditionExpression=Key(attr).eq(value))

    def _collect(self, table: str, **kw: Any) -> list[Item]:
        out: list[Item] = []
        while True:
            r = self._t(table).query(**kw)
            out.extend(plain(i) for i in r.get("Items", []))
            if "LastEvaluatedKey" not in r:
                return out
            kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    def batch_put(self, table: str, items: list[Item]) -> None:
        with self._t(table).batch_writer(overwrite_by_pkeys=["pk", "sk"]) as bw:
            for it in items:
                bw.put_item(Item=to_dynamo(it))


_backend: Backend | None = None
_backend_lock = threading.Lock()


def backend_kind() -> str:
    kind = os.environ.get("CULL_DDB")
    if kind:
        return kind
    return "aws" if os.environ.get("WORK_BUCKET") else "local"


def backend() -> Backend:
    global _backend
    with _backend_lock:
        if _backend is None:
            kind = backend_kind()
            if kind == "aws":
                _backend = AwsBackend()
            elif kind == "local":
                _backend = LocalFileBackend(os.environ.get("CULL_LOCAL_ROOT", "data/local"))
            elif kind == "memory":
                _backend = MemoryBackend()
            else:
                raise ValueError(f"CULL_DDB must be aws|local|memory, got {kind!r}")
        return _backend


def reset_backend(b: Backend | None = None) -> None:
    global _backend
    with _backend_lock:
        _backend = b


# --------------------------------------------------------------------------- item helpers


def event_pk(event_id: str) -> str:
    return f"EVENT#{event_id}"


def photo_item(event_id: str, d: Mapping[str, Any], updated_at: str) -> Item:
    """PHOTO# item from a validated decision dict (see keepframe.tools.decision)."""
    verdict = d["verdict"]
    it: Item = {
        "pk": event_pk(event_id),
        "sk": f"PHOTO#{d['photo_id']}",
        "photo_id": d["photo_id"],
        "event_id": event_id,
        "verdict": verdict,
        "reason_codes": list(d["reason_codes"]),
        "reason_text": d.get("reason_text", ""),
        "confidence": float(d["confidence"]),
        "decided_by": d["decided_by"],
        "human_source": d.get("human_source"),
        "agent_confidence": d.get("agent_confidence"),
        "run_id": d.get("run_id"),
        "scores": d.get("scores") or {},
        "needs_approval": bool(d.get("needs_approval", False)),
        "updated_at": updated_at,
        "gsi1pk": f"{event_pk(event_id)}#VERDICT#{verdict}",
    }
    if d.get("group_id"):
        it["group_id"] = d["group_id"]
        it["gsi2pk"] = f"GROUP#{d['group_id']}"
    if d.get("note"):
        it["human_note"] = str(d["note"])[:500]
    return it


def group_item(event_id: str, g: Mapping[str, Any], run_id: str, updated_at: str) -> Item:
    return {
        "pk": event_pk(event_id),
        "sk": f"GROUP#{g['group_id']}",
        "group_id": g["group_id"],
        "event_id": event_id,
        "members": list(g.get("members") or g.get("photo_ids") or []),
        "size": int(g.get("size") or len(g.get("members") or [])),
        "span_s": float(g.get("span_s") or 0.0),
        "best_photo_id": g.get("best_photo_id"),
        "margin": g.get("margin"),
        "tie": bool(g.get("tie", False)),
        "ranking": [{"photo_id": r["photo_id"], "score": r.get("score"), "breakdown": r.get("breakdown")}
                    for r in (g.get("ranking") or [])],
        "contact_sheet_key": g.get("contact_sheet_key"),
        "run_id": run_id,
        "updated_at": updated_at,
        "gsi2pk": f"GROUP#{g['group_id']}",
    }


# --------------------------------------------------------------------------- writer API


def put_photo_decisions(event_id: str, decisions: Sequence[Mapping[str, Any]], updated_at: str) -> int:
    b = backend()
    items = [photo_item(event_id, d, updated_at) for d in decisions]
    if isinstance(b, AwsBackend):
        b.batch_put(DECISIONS_TABLE, items)
    else:
        for it in items:
            b.put(DECISIONS_TABLE, it)
    return len(items)


def put_groups(event_id: str, groups: Sequence[Mapping[str, Any]], run_id: str, updated_at: str) -> int:
    b = backend()
    for g in groups:
        it = group_item(event_id, g, run_id, updated_at)
        # update (not put) so an existing pending marker / human_decision on the item survives
        b.update(DECISIONS_TABLE, it.pop("pk"), it.pop("sk"), it)
    return len(groups)


RUN_ONCE_FIELDS = ("started_at", "agent_started_at", "run_kind")  # set by whoever creates the item (API, worker or agent), never overwritten


def run_kind() -> str:
    """Same rule as the worker: ``deployed`` inside ECS/Fargate, on AgentCore Runtime (CULL_AGENT_HOST=agentcore,
    set by the runtime app, or CULL_INSTANCE_TYPE=agentcore-*, set by infra) or when CULL_RUN_KIND is set, else
    ``local``. Where the agent ran is ``agent_host`` (strands_agent.agent_host), not run_kind."""
    deployed = (os.environ.get("ECS_CONTAINER_METADATA_URI_V4") or os.environ.get("CULL_RUN_KIND")
                or os.environ.get("CULL_AGENT_HOST") == "agentcore"
                or os.environ.get("CULL_INSTANCE_TYPE", "").startswith("agentcore"))
    return "deployed" if deployed else "local"


def update_run(event_id: str, run_id: str, remove: Iterable[str] = (), **fields: Any) -> None:
    """Upsert the RUN# item (the API or the worker may have created it).

    Two writers share the item: the worker owns worker_*,
    the agent owns agent_*; ``started_at`` is written with ``if_not_exists`` so a later writer never moves it.
    """
    vals = {k: v for k, v in fields.items() if v is not None and k not in RUN_ONCE_FIELDS}
    once = {k: fields[k] for k in RUN_ONCE_FIELDS if fields.get(k) is not None}
    once.setdefault("run_kind", run_kind())  # labels agent-only runs too; never overrides an existing value
    vals.update({"run_id": run_id, "event_id": event_id, "gsi1pk": RUNS_GSI1PK})
    backend().update(DECISIONS_TABLE, event_pk(event_id), f"RUN#{run_id}", vals, remove=remove,
                     set_if_absent=once)


def get_run(event_id: str, run_id: str) -> Item | None:
    return backend().get(DECISIONS_TABLE, event_pk(event_id), f"RUN#{run_id}")


def find_run(run_id: str) -> Item | None:
    for it in backend().query_index(DECISIONS_TABLE, "gsi1pk", RUNS_GSI1PK):
        if it.get("sk") == f"RUN#{run_id}":
            return it
    return None


def set_agent_confidence(event_id: str, photo_confidences: Mapping[str, float], ts: str) -> None:
    """Record the agent's proposed confidence on EXISTING PHOTO# rows of escalated photos.

    Rows are not created here: a verdict-less PHOTO# item would shadow the worker's verdict in the API
    (DynamoDB row preferred over decisions.jsonl). Photos without a row get agent_confidence on their write."""
    b = backend()
    for pid, conf in photo_confidences.items():
        if b.get(DECISIONS_TABLE, event_pk(event_id), f"PHOTO#{pid}") is None:
            continue
        b.update(DECISIONS_TABLE, event_pk(event_id), f"PHOTO#{pid}",
                 {"agent_confidence": round(float(conf), 4), "updated_at": ts})


def mark_pending(event_id: str, item_id: str, kind: str, approval: Mapping[str, Any], ts: str) -> str:
    """Put the GROUP#/PHOTO# item on the GSI3 approval queue. Returns the approval_id."""
    sk = f"{'GROUP' if kind == 'group' else 'PHOTO'}#{item_id}"
    approval_id = item_id
    vals = {
        "gsi3pk": PENDING,
        "gsi3sk": f"{ts}#{event_id}#{approval_id}",
        "approval_id": approval_id,
        "approval_status": "pending",
        "event_id": event_id,
        ("group_id" if kind == "group" else "photo_id"): item_id,
        "proposed": approval.get("proposed"),
        "reason": str(approval.get("reason", ""))[:500],
        "confidence": approval.get("confidence"),
        "contact_sheet_key": approval.get("contact_sheet_key"),
        "interrupt_id": approval.get("interrupt_id"),
        "interrupt_name": approval.get("interrupt_name"),
        "run_id": approval.get("run_id"),
        "updated_at": ts,
    }
    backend().update(DECISIONS_TABLE, event_pk(event_id), sk, vals)
    return approval_id


def resolve_pending(event_id: str, item_id: str, kind: str, human_decision: Mapping[str, Any], ts: str,
                    via: str = "agent") -> None:
    """Record the answer and drop the item from the approval queue (idempotent with the API's own update)."""
    sk = f"{'GROUP' if kind == 'group' else 'PHOTO'}#{item_id}"
    backend().update(DECISIONS_TABLE, event_pk(event_id), sk,
                     {"human_decision": dict(human_decision), "approval_status": "resolved",
                      "resolved_at": ts, "resolved_via": via, "updated_at": ts},
                     remove=("gsi3pk", "gsi3sk", "resume_lock"))


def pending_for_run(run_id: str) -> list[Item]:
    return [i for i in backend().query_index(DECISIONS_TABLE, "gsi3pk", PENDING) if i.get("run_id") == run_id]


def answered_for_run(event_id: str, run_id: str) -> list[Item]:
    """GROUP#/PHOTO# items of this run carrying a human_decision (the ECS resume path reads these)."""
    out = []
    for prefix in ("GROUP#", "PHOTO#"):
        out += [i for i in backend().query(DECISIONS_TABLE, event_pk(event_id), prefix)
                if i.get("run_id") == run_id and i.get("human_decision")]
    return out


def write_human_decisions(run_id: str, decisions: Sequence[Mapping[str, Any]]) -> int:
    """For the API: apply human verdicts without an agent (decided_by=human, human_source=ui).

    ``decisions`` = [{"photo_id", "verdict", "reason_codes"?, "group_id"?, "note"?, "event_id"?}].
    Reason codes default by design (group keep -> KEEP_BEST_OF_GROUP, group reject -> BURST_NOT_BEST).
    Confidence is the decision's own (the proposal's) when given, else the existing item's, else 1.0.
    """
    from keepframe.tools.decision import validate_decisions

    run = find_run(run_id) or {}
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    by_event: dict[str, list[dict[str, Any]]] = {}
    for d in decisions:
        ev = str(d.get("event_id") or run.get("event_id") or "")
        if not ev:
            raise ValueError("event_id unknown: pass it per decision or create the RUN# item first")
        codes = d.get("reason_codes") or (
            {"keep": ["KEEP_BEST_OF_GROUP"], "reject": ["BURST_NOT_BEST"]}.get(d["verdict"], ["REVIEW_LOW_CONFIDENCE"])
            if d.get("group_id") else
            {"keep": ["KEEP_SINGLETON"]}.get(d["verdict"], ["REVIEW_LOW_CONFIDENCE"]))
        note = str(d.get("note") or "")
        existing = backend().get(DECISIONS_TABLE, event_pk(ev), f"PHOTO#{d['photo_id']}") or {}
        conf = d.get("confidence", existing.get("confidence", 1.0))
        by_event.setdefault(ev, []).append({
            "photo_id": d["photo_id"], "verdict": d["verdict"], "reason_codes": codes,
            "reason_text": (f"Decided by a person: {note}" if note else "Decided by a person")[:140],
            "confidence": conf, "group_id": d.get("group_id"), "decided_by": "human", "human_source": "ui",
            "run_id": run_id,
            "note": note,
        })
    n = 0
    for ev, ds in by_event.items():
        n += put_photo_decisions(ev, validate_decisions(ds), now)
    return n


def put_trace(item: Item) -> None:
    backend().put(TRACES_TABLE, item)


def query_trace(run_id: str) -> list[Item]:
    return backend().query(TRACES_TABLE, f"RUN#{run_id}")

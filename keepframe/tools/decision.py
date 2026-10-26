"""write_decision tool: validated verdicts -> DynamoDB + results/<eventId>/<runId>/decisions.jsonl.

With a run_id, rows are appended to the run's own file and to the unversioned latest copy
results/<eventId>/decisions.jsonl (kept for one release); without one, only to the latest copy.

Never modifies the uploaded photos. There is no delete path: the only verbs are keep | reject | review (P8), and the
store layer has no delete method. Rows are appended to decisions.jsonl (last write per photo wins, the
same convention the API reads), so an agent/human decision overrides the worker's earlier row.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from keepframe.schemas import Decision, validate_reason_code

VERDICTS = ("keep", "reject", "review")
DECIDED_BY = ("agent", "human", "worker", "scripted", "lead_test")
HUMAN_SOURCES = ("ui", "file", "http", "scripted", "lead_test")
NON_HUMAN_SOURCES = ("scripted", "lead_test")  # decided_by equals the source for these, never "human"
REASON_TEXT_MAX = 140

_jsonl_lock = threading.Lock()


class DecisionError(ValueError):
    """A decision failed validation; nothing was written."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _opt_conf(v: Any, where: str) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError) as e:
        raise DecisionError(f"{where}: agent_confidence must be a number in [0, 1]") from e
    if not 0.0 <= f <= 1.0:
        raise DecisionError(f"{where}: agent_confidence {f} outside [0, 1]")
    return round(f, 4)


def validate_decisions(decisions: Sequence[Mapping[str, Any]], run_id: str | None = None) -> list[dict[str, Any]]:
    """Return normalised decision dicts or raise DecisionError (all-or-nothing)."""
    if not decisions:
        raise DecisionError("decisions is empty")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, d in enumerate(decisions):
        where = f"decisions[{i}]"
        pid = d.get("photo_id")
        if not isinstance(pid, str) or not pid or "/" in pid or "#" in pid:
            raise DecisionError(f"{where}: photo_id missing or invalid")
        if pid in seen:
            raise DecisionError(f"{where}: photo {pid} appears twice")
        seen.add(pid)
        verdict = d.get("verdict")
        if verdict not in VERDICTS:
            raise DecisionError(f"{where}: verdict must be one of {VERDICTS} (there is no delete), got {verdict!r}")
        codes = d.get("reason_codes") or []
        if not isinstance(codes, list) or not codes:
            raise DecisionError(f"{where}: at least one reason code is required")
        try:
            codes = [validate_reason_code(str(c)) for c in codes]
        except ValueError as e:
            raise DecisionError(f"{where}: {e}") from e
        text = str(d.get("reason_text") or "")
        if len(text) > REASON_TEXT_MAX:
            raise DecisionError(f"{where}: reason_text is {len(text)} chars (max {REASON_TEXT_MAX})")
        raw_conf = d.get("confidence")
        try:
            if raw_conf is None or isinstance(raw_conf, bool):
                raise TypeError("missing")
            conf = float(raw_conf)
        except (TypeError, ValueError) as e:
            raise DecisionError(f"{where}: confidence must be a number in [0, 1]") from e
        if not 0.0 <= conf <= 1.0:
            raise DecisionError(f"{where}: confidence {conf} outside [0, 1]")
        by = d.get("decided_by", "agent")
        if by not in DECIDED_BY:
            raise DecisionError(f"{where}: decided_by must be one of {DECIDED_BY}")
        src = d.get("human_source")
        if by in ("human", *NON_HUMAN_SOURCES):
            if src not in HUMAN_SOURCES:
                raise DecisionError(f"{where}: decided_by={by} needs human_source in {HUMAN_SOURCES}")
            expected = src if src in NON_HUMAN_SOURCES else "human"
            if by != expected:
                raise DecisionError(f"{where}: human_source={src!r} requires decided_by={expected!r}")
        elif src is not None:
            raise DecisionError(f"{where}: human_source is only for human/scripted decisions")
        row = {
            "photo_id": pid,
            "verdict": verdict,
            "reason_codes": codes,
            "reason_text": text,
            "confidence": round(conf, 4),
            "group_id": d.get("group_id") or None,
            "decided_by": by,
            "human_source": src,
            "agent_confidence": _opt_conf(d.get("agent_confidence"), where),
            "run_id": d.get("run_id") or run_id,
            "scores": dict(d.get("scores") or {}),
            "needs_approval": bool(d.get("needs_approval", False)),
        }
        if d.get("note"):
            row["note"] = str(d["note"])[:500]
        out.append(row)
    return out


def write_decision(event_id: str, decisions: list[dict[str, Any]], run_id: str | None = None) -> dict[str, Any]:
    """Persist verdicts for photos of one event.

    Args:
        event_id: the event.
        decisions: [{photo_id, verdict: keep|reject|review, reason_codes: [>=1 code], confidence 0..1,
            group_id?, decided_by: agent|human|worker|scripted, human_source?, reason_text? (<=140 chars; templated when empty),
            scores?}].
    Returns:
        {written, verdict_counts}
    """
    from keepframe.agent.reasons import template_reason
    from keepframe.io import proxies
    from keepframe.store import dynamodb

    rows = validate_decisions(decisions, run_id)
    for r in rows:
        if not r["reason_text"]:
            t = template_reason(r["verdict"], r["reason_codes"], r["scores"])
            if r["decided_by"] == "human":
                note = r.get("note") or ""
                t = (f"Decided by a person: {note}" if note else f"Decided by a person. {t}")
            elif r["decided_by"] == "scripted":
                t = f"Auto-approved by a script (no human review). {t}"
            elif r["decided_by"] == "lead_test":
                t = f"Mechanism test by the team (not a photographer judgement). {t}"
            r["reason_text"] = t[:REASON_TEXT_MAX]
    now = _now()
    dynamodb.put_photo_decisions(event_id, rows, now)
    lines = "".join(
        Decision(**{k: v for k, v in r.items() if k != "note"}, event_id=event_id).model_dump_json() + "\n"
        for r in rows)
    from keepframe.worker.run_record import result_keys

    keys = result_keys(event_id, run_id, "decisions.jsonl") if run_id else (f"results/{event_id}/decisions.jsonl",)
    store = proxies.get_store()
    with _jsonl_lock:  # S3 has no append: read-modify-write under a process lock (single writer per run)
        for key in keys:
            prev = store.get_bytes(key) if store.exists(key) else b""
            store.put_bytes(key, prev + lines.encode(), "application/x-ndjson")
    counts = Counter(r["verdict"] for r in rows)
    return {"written": len(rows), "verdict_counts": {v: counts.get(v, 0) for v in VERDICTS}}

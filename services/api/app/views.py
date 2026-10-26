"""Item → response shaping. Allowlists only: nothing from storage reaches a client unless named here."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from datetime import datetime
from typing import Any

from .config import settings
from .labels import display_category

VERDICTS = ("keep", "reject", "review")
_SHEET_KEY = re.compile(r"^sheets/[A-Za-z0-9_.:-]+/[A-Za-z0-9_.:-]+\.(jpg|jpeg|png|webp)$")

RUN_FIELDS = (
    "run_id",
    "event_id",
    "status",
    "flavor",
    "started_at",
    "ended_at",
    "updated_at",
    "opencv_version",
    # HAL cv2 actually loaded (carotene | kleidicv-26.03+carotene), copied onto RUN# by Summarise.
    "opencv_hal",
    "cv2_file",
    "instance_type",
    "thresholds_sha",
    "verdict_counts",
    "tool_call_count",
    "escalation_count",
    "cost_estimate_usd",
    "photos_processed",
    "llm_provider",
    "approvals_source",
    "planner_model",
    "reasons_model",
    "error",
    # Split two-writer shape (2026-10-05): worker pass and agent pass on the same RUN# item.
    "worker_started_at",
    "worker_ended_at",
    "worker_wall_s",
    "worker_escalations",
    "worker_status",
    "n_photos",
    "n_groups",
    "images_per_s",
    "agent_started_at",
    "agent_ended_at",
    "agent_wall_s",
    "agent_escalations",
    "agent_verdict_counts",
    "agent_decided",
    "agent_photos",
    "run_kind",
    "run_note",
    # Where the agent pass ran, stamped by the agent (agentcore | fargate | local); shown verbatim.
    "agent_host",
)
PHOTO_FIELDS = (
    "photo_id",
    "verdict",
    "reason_codes",
    "reason_text",
    "confidence",
    "group_id",
    "decided_by",
    "human_source",
    "labeller",
    "agent_confidence",
    "scores",
    "run_id",
    "updated_at",
)
TRACE_DROP = {"pk", "sk", "ttl", "gsi1pk", "gsi2pk", "gsi3pk", "gsi3sk"}


def pick(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {k: item[k] for k in fields if k in item and item[k] is not None}


TERMINAL_RUN_STATES = {"succeeded", "failed", "cancelled"}


def _iso(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_duration_s(item: dict[str, Any]) -> float | None:
    """Wall time of a finished run, or None. A paused or running run has no duration, and an ended_at that
    is not after started_at (two writers stamped different phases) is never turned into a duration."""
    if item.get("status") not in TERMINAL_RUN_STATES:
        return None
    start, end = _iso(item.get("started_at")), _iso(item.get("ended_at"))
    if start and end and end > start:
        return round((end - start).total_seconds(), 1)
    # Without consistent shared stamps, fall back to the passes' own wall times (old shape: wall_s).
    walls = [item.get(k) for k in ("worker_wall_s", "agent_wall_s")]
    walls = [w for w in walls if isinstance(w, (int, float)) and not isinstance(w, bool) and w > 0]
    if walls:
        return round(float(sum(walls)), 1)
    wall = item.get("wall_s")
    if isinstance(wall, (int, float)) and not isinstance(wall, bool) and wall > 0:
        return float(wall)
    return None


def _count(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def escalations(item: dict[str, Any]) -> dict[str, int]:
    """{"worker": N, "agent": M} from the split worker_escalations / agent_escalations attributes.

    The old single `escalation_count` is NOT attributed: on runs both passes touched (e.g. E07) it holds the
    worker's count even though the agent wrote llm_provider, so guessing would mislabel it. Old rows keep
    `escalation_count`, which the UI shows as "N policy escalations"."""
    out: dict[str, int] = {}
    w, a = _count(item.get("worker_escalations")), _count(item.get("agent_escalations"))
    if w is not None:
        out["worker"] = w
    if a is not None:
        out["agent"] = a
    return out


_RUN_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def run_kind(item: dict[str, Any]) -> str | None:
    """`run_kind` exactly as stored (writers: worker, agent, operator; e.g. deployed, local, local_calibration,
    agentcore). Only a malformed value is dropped. Absent on older rows, where run_aws_* / run_infra_* ids
    were AWS runs."""
    kind = item.get("run_kind")
    if isinstance(kind, str):
        return kind if _RUN_KIND_RE.match(kind) else None
    rid = str(item.get("run_id") or str(item.get("sk", "")).removeprefix("RUN#"))
    if rid.startswith(("run_aws_", "run_infra_")):
        return "deployed"
    return None


def last_answer(item: dict[str, Any]) -> dict[str, Any]:
    """Who gave the most recent approval answer on this run, from RUN# `resume_decisions` (written by the API
    on every answer): `last_answer_source` (ui | lead_test | scripted | file | http | unknown) and the verbatim
    `last_labeller` when one was given."""
    decisions = item.get("resume_decisions")
    if not isinstance(decisions, list) or not decisions or not isinstance(decisions[-1], dict):
        return {}
    d = decisions[-1]
    out: dict[str, Any] = {"last_answer_source": _answer_source([d]) or "unknown"}
    if isinstance(d.get("labeller"), str):
        out["last_labeller"] = d["labeller"][:64]
    return out


_ERROR_CODE = re.compile(
    r"^([A-Z][A-Za-z0-9]{2,40}(?:Error|Exception|Exceeded|Denied|Timeout|NotFound|Failed))\b"
)


def error_code(message: Any) -> str | None:
    """A short code for a stored error (S-13): the leading exception/AWS error class when there is one
    ("ClientError", "TimeoutError", "ThrottlingException"), else "error". Never the message text."""
    if not isinstance(message, str) or not message.strip():
        return None
    m = _ERROR_CODE.match(message.strip())
    return m.group(1) if m else "error"


def judge_safe_run(view: dict[str, Any]) -> dict[str, Any]:
    """Drop free-text error fields for the read-only judge; `error_code` stays."""
    for k in ("error", "resume_error"):
        view.pop(k, None)
    return view


def run_view(item: dict[str, Any]) -> dict[str, Any]:
    out = pick(item, RUN_FIELDS)
    out.setdefault("run_id", str(item.get("sk", "")).removeprefix("RUN#"))
    out.setdefault("event_id", str(item.get("pk", "")).removeprefix("EVENT#"))
    duration = run_duration_s(item)
    if duration is not None:
        out["duration_s"] = duration
    kind = run_kind(item)
    if kind:
        out["run_kind"] = kind
    esc = escalations(item)
    if esc:
        out["escalations"] = esc
    # photos_processed is written by both passes on the transitional shape; agent_photos supersedes it.
    if isinstance(item.get("agent_photos"), (int, float)):
        out["agent_photos"] = item["agent_photos"]
    last = last_answer(item)
    if last:
        out.update(last)
    code = error_code(item.get("error"))
    if code:
        out["error_code"] = code
    if isinstance(item.get("paused_at"), str) and item.get("status") == "awaiting_approval":
        out["paused_at"] = item["paused_at"]
    else:
        out.pop("paused_at", None)
    return out


def photo_id_of(item: dict[str, Any]) -> str:
    return str(item.get("photo_id") or str(item.get("sk", "")).removeprefix("PHOTO#"))


# human_source values written by the agent (2026-10-05 contract): a person answered through the review
# desk (ui), a decisions file (file) or an HTTP resume (http); or an automatic approver did (scripted).
PERSON_CHANNELS = {"ui", "file", "http"}
# lead_test: the team answered to exercise the resume path; a mechanism test, not a photographer's judgement.
SOURCES = {"agent", "scripted", "lead_test", "unknown", *PERSON_CHANNELS}
SCRIPTED_TEXT = "Auto-approved by a script (no human review)"
LEAD_TEST_TEXT = "Team mechanism test, not a photographer judgement"
LEAD_TEST_LABELLER = "lead-mechanism-test"


def _stamp(*values: Any) -> str | None:
    for v in values:
        if isinstance(v, str) and v in SOURCES:
            return v
    return None


def decision_source(item: dict[str, Any], group: dict[str, Any] | None = None) -> str:
    """Who made a stored verdict: `agent`, `scripted` (an automatic approver; no person looked), a person
    channel (`ui` / `file` / `http`), or `unknown` when a non-agent verdict carries no source stamp.

    Only an explicit stamp makes a verdict a person's: `human_source` on the PHOTO# item, then on the
    GROUP#'s `human_decision`. `decided_by: "scripted"` is scripted; `decided_by: "worker"` is the
    deterministic worker, shown like the agent. Anything else unstamped is `unknown`, never a person.
    """
    decided_by = item.get("decided_by")
    if decided_by in (None, "agent", "worker"):
        return "agent"
    if decided_by in ("scripted", "lead_test"):
        return str(decided_by)
    hd = (group or {}).get("human_decision")
    hd = hd if isinstance(hd, dict) else {}
    if LEAD_TEST_LABELLER in (item.get("labeller"), hd.get("labeller")):
        return "lead_test"  # same rule as the agent; covers rows stored as ui before the API knew it
    stamp = _stamp(item.get("human_source"), hd.get("human_source"), hd.get("source"))
    if stamp == "agent":
        return "agent"
    return stamp or "unknown"


def agent_confidence(item: dict[str, Any], group: dict[str, Any] | None) -> Any:
    """The agent's own confidence for a verdict a person or script confirmed. PHOTO# rows written before the
    2026-10-05 fix hold a placeholder 1.0 there, so prefer `agent_confidence`, then the GROUP#'s proposal
    confidence, and never return the placeholder."""
    for v in (item.get("agent_confidence"), (group or {}).get("confidence")):
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1:
            return v
    v = item.get("confidence")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1:
        return v
    return None


def photo_view(item: dict[str, Any], group: dict[str, Any] | None = None) -> dict[str, Any]:
    out = pick(item, PHOTO_FIELDS)
    out["photo_id"] = photo_id_of(item)
    source = decision_source(item, group)
    out["decision_source"] = source
    if source != "agent":
        conf = agent_confidence(item, group)
        if conf is None:
            out.pop("confidence", None)
        else:
            out["confidence"] = conf
        if isinstance(item.get("human_note"), str):
            out["human_note"] = item["human_note"][:200]
        text = str(out.get("reason_text", ""))
        if source == "scripted" and (not text or text.startswith("Decided by a person")):
            out["reason_text"] = SCRIPTED_TEXT
        elif source == "lead_test" and (not text or text.startswith("Decided by a person")):
            out["reason_text"] = LEAD_TEST_TEXT
        elif source == "unknown" and text.startswith("Decided by a person"):
            out["reason_text"] = "Confirmed outside the agent (source not recorded)"
    return out


# Images (S-1): a presigned S3 URL carries its key, proxy/<eventId>/<photoId>.jpg, which is exactly the pair
# a public CDN of the source can serve. Clients get an API link keyed by aliases instead; GET /img streams the bytes.
IMG_KINDS = {"p": ("proxy", "jpg"), "s": ("sheets", "jpg")}


def image_sig(kind: str, ev: str, item: str, exp: int) -> str:
    from .aliases import alias_key  # local: aliases imports config/aws only

    msg = f"img\n{kind}\n{ev}\n{item}\n{exp}".encode()
    return base64.urlsafe_b64encode(hmac.new(alias_key.get(), msg, hashlib.sha256).digest()[:18]).decode()


def image_url(kind: str, event_id: str, real_item: str) -> str:
    """/img/<ev alias>/<kind>/<item alias>.jpg?exp=&sig=, expiring like a presigned URL (CULL_PRESIGN_TTL_S)."""
    from .aliases import event_map

    em = event_map(event_id)
    if em is None:
        return ""
    item = em.add_photo(real_item) if kind == "p" else em.add_group(real_item)
    # Rounded so a page of thumbnails shares one expiry and the browser can cache across polls.
    ttl = settings().presign_ttl_s
    exp = (int(time.time()) // 60 + 1) * 60 + ttl
    base = settings().public_base.rstrip("/")
    return f"{base}/img/{em.alias}/{kind}/{item}.jpg?exp={exp}&sig={image_sig(kind, em.alias, item, exp)}"


def proxy_url(event_id: str, photo_id: str) -> str:
    return image_url("p", event_id, photo_id)


def sheet_url(event_id: str, group_id: str) -> str:
    return image_url("s", event_id, group_id)


def sheet_url_for_key(key: Any) -> str | None:
    """A contact-sheet key named in a trace/approval (sheets/<eventId>/<groupId>.jpg) as an image link."""
    if isinstance(key, str) and _SHEET_KEY.match(key) and ".." not in key:
        _, event_id, name = key.split("/", 2)
        group_id = name.rsplit(".", 1)[0]
        return sheet_url(event_id, group_id) or None
    return None


def verdict_counts(photos: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(VERDICTS, 0)
    for p in photos:
        v = p.get("verdict")
        if v in counts:
            counts[v] += 1
    return counts


def reason_counts(photos: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in photos:
        for code in p.get("reason_codes") or []:
            base = str(code).split(":", 1)[0]  # DUPLICATE_OF:<photo_id> counts as DUPLICATE_OF
            counts[base] = counts.get(base, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def event_view(meta: dict[str, Any], index: int) -> dict[str, Any]:
    """Event index entry. Never carries source user/studio ids; the name only when explicitly enabled."""
    out: dict[str, Any] = {
        "event_id": meta.get("event_id"),
        "index": index,
        "label": f"Event {index}",
        "category": display_category(meta.get("category")),
        "event_date": meta.get("event_date"),
        "n_photos": meta.get("n_photos"),
        "n_with_original": meta.get("n_with_original"),
        "n_cameras": len(meta.get("cameras") or []),
    }
    if settings().show_event_names and meta.get("name"):
        out["name"] = meta["name"]
    return {k: v for k, v in out.items() if v is not None}


def _answer_source(decisions: Any) -> str | None:
    """Source of the answers on an interrupt_resumed / request_human_approval result line. Unstamped
    answers are `unknown`: older trace lines predate `human_source`, and they are never shown as a person."""
    flat: list[dict[str, Any]] = []
    stack = [decisions]
    while stack:
        v = stack.pop()
        if isinstance(v, list):
            stack.extend(v)
        elif isinstance(v, dict):
            flat.append(v)
    if not flat:
        return None
    sources = {
        "lead_test"
        if d.get("labeller") == LEAD_TEST_LABELLER
        else (_stamp(d.get("human_source"), d.get("source")) or "unknown")
        for d in flat
    }
    # Mixed answers are reported as the least trustworthy one.
    for s in ("scripted", "lead_test", "unknown", "http", "file", "ui"):
        if s in sources:
            return s
    return "unknown"


def trace_view(item: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in item.items() if k not in TRACE_DROP}
    if "seq" not in out and "sk" in item:
        out["seq"] = int(str(item["sk"]))
    out["seq"] = int(out.get("seq", 0))
    if out.get("type") == "interrupt_resumed":
        src = _answer_source(out.get("decisions"))
    elif out.get("type") == "tool_result" and out.get("tool") == "request_human_approval":
        src = _answer_source((out.get("result") or {}).get("decisions"))
    else:
        src = None
    if src:
        out["answered_by"] = src
    return out

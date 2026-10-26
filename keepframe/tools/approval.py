"""request_human_approval tool: pause the run on a Strands interrupt.

Called from inside a Strands tool with a ``ToolContext``: the first call raises the interrupt (the agent
loop stops with ``stop_reason == "interrupt"``); after the human answers, Strands re-runs the tool with the
response, which this function returns as ``{"decisions": [...]}``. The pending item is also put on the
DynamoDB GSI3 approval queue so the API/UI can show it while the run is paused.

This module has no Strands import at module level (tool modules stay framework-free); the
``ToolContext`` is passed in by keepframe.agent.toolset.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

INTERRUPT_PREFIX = "keepframe-approval"


def interrupt_name(items: Sequence[Mapping[str, Any]]) -> str:
    """Deterministic, unique-per-item-set name (Strands requires uniqueness per tool call)."""
    ids = ",".join(sorted(str(i.get("group_id") or i.get("photo_id")) for i in items))
    return f"{INTERRUPT_PREFIX}:{hashlib.sha1(ids.encode()).hexdigest()[:12]}"


_V = ("keep", "reject", "review")


def normalise_proposed(prop: Any, is_group: bool) -> Any:
    """A proposal in the shape an "approve" answer must have (the API sends ``proposed`` back as human_verdict).

    Photo: the bare verdict string ("reject", {"verdict": "review"} and {"review": ["p_1"]} all map to it).
    Group: the {keep|reject|review: [ids]} map with empty verdicts dropped. Unreadable -> None (the UI then
    needs an override, which is the honest outcome)."""
    if is_group:
        if isinstance(prop, Mapping):
            out = {str(k): [str(x) for x in v] for k, v in prop.items()
                   if k in _V and isinstance(v, list | tuple) and v}
            return out or None
        return None
    if isinstance(prop, str):
        return prop if prop in _V else None
    if isinstance(prop, Mapping):
        v = prop.get("verdict")
        if isinstance(v, str) and v in _V:
            return v
        return next((k for k in _V if prop.get(k)), None)
    return None


def normalise_items(items: Sequence[Mapping[str, Any]], event_id: str) -> list[dict[str, Any]]:
    out = []
    for it in items:
        gid, pid = it.get("group_id"), it.get("photo_id")
        if not gid and not pid:
            raise ValueError("each approval item needs group_id or photo_id")
        conf = float(it.get("confidence", 0.5))
        out.append({
            "group_id": gid,
            "photo_id": None if gid else pid,
            "proposed": normalise_proposed(it.get("proposed"), bool(gid)),
            "reason": str(it.get("reason", ""))[:500],
            "confidence": max(0.0, min(1.0, conf)),
            "contact_sheet_key": it.get("contact_sheet_key") or (f"sheets/{event_id}/{gid}.jpg" if gid else None),
        })
    return out


def normalise_response(response: Any, items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Accept the API's payload ({decisions:[...]}, a list, or a single decision) -> {decisions:[...]}."""
    if isinstance(response, Mapping) and "decisions" in response:
        decs = list(response["decisions"])
    elif isinstance(response, list):
        decs = list(response)
    elif isinstance(response, Mapping):
        decs = [dict(response)]
    else:
        decs = [{"human_verdict": response}]
    if len(items) == 1:
        it = items[0]
        for d in decs:
            d.setdefault("group_id", it.get("group_id"))
            if not it.get("group_id"):
                d.setdefault("photo_id", it.get("photo_id"))
    for d in decs:
        d.setdefault("id", d.get("group_id") or d.get("photo_id"))
        d.setdefault("note", "")
    return {"decisions": decs}


def request_human_approval(tool_context: Any, items: list[dict[str, Any]], event_id: str, run_id: str,
                           on_pending: Any = None) -> dict[str, Any]:
    """Raise a Strands interrupt carrying the items; return the human decisions once resumed.

    Args:
        tool_context: Strands ToolContext (``.interrupt(name, reason)``).
        items: [{group_id | photo_id, proposed, reason, confidence, contact_sheet_key}].
        on_pending: callback(items, interrupt_name, interrupt_id) run before the interrupt is raised (queue + trace).
    Returns:
        {decisions: [{id, group_id|photo_id, human_verdict, note}]}
    """
    norm = normalise_items(items, event_id)
    name = interrupt_name(norm)
    reason = {"kind": "keepframe_approval", "event_id": event_id, "run_id": run_id, "items": norm}
    # Ask Strands whether a response is already present without raising (resume path).
    state = getattr(getattr(tool_context, "agent", None), "_interrupt_state", None)
    iid = tool_context._interrupt_id(name)
    hit = state.interrupts.get(iid) if state is not None else None
    if (hit is None or hit.response is None) and on_pending is not None:
        on_pending(norm, name, iid)
    response = tool_context.interrupt(name, reason=reason)
    return normalise_response(response, norm)


VERDICTS = ("keep", "reject", "review")
REF_KEYS = ("group_id", "photo_id", "id", "interrupt_id")


def _ref(d: Mapping[str, Any]) -> str:
    return next((f"{k}={d[k]}" for k in REF_KEYS if d.get(k)), "no id")


def validate_answers(decisions: Any, groups: Mapping[str, Sequence[str]] | None = None) -> None:
    """Raise ValueError (one clear message) unless every human answer is well formed.

    Schema (the API's ApprovalDecision after approve/override is resolved):
      {group_id | photo_id | id | interrupt_id, human_verdict, note?}
      human_verdict: "keep" | "reject" | "review" for a photo, or {keep|reject|review: [photo_id, ...]} for a group
      (at least one keep, no photo under two verdicts, members only when ``groups`` is given).
    Extra keys (human_source, labeller, interrupt_name, ...) are allowed.
    """
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("decisions must be a non-empty list")
    errs: list[str] = []
    for n, d in enumerate(decisions):
        if not isinstance(d, Mapping):
            errs.append(f"decision {n}: must be an object")
            continue
        ref = _ref(d)
        if ref == "no id":
            errs.append(f"decision {n}: needs one of {'/'.join(REF_KEYS)}")
            continue
        for k in REF_KEYS:
            if d.get(k) is not None and not isinstance(d[k], str):
                errs.append(f"decision {n} ({ref}): {k} must be a string")
        if d.get("note") is not None and not isinstance(d["note"], str):
            errs.append(f"decision {n} ({ref}): note must be a string")
        hv = d.get("human_verdict")
        gid, pid = d.get("group_id"), d.get("photo_id")
        if hv is None:
            errs.append(f"decision {n} ({ref}): human_verdict is required (got keys {sorted(map(str, d))}); "
                        f"send \"keep\"|\"reject\"|\"review\" for a photo or a keep/reject/review map for a group")
        elif isinstance(hv, str):
            if hv not in VERDICTS:
                errs.append(f"decision {n} ({ref}): human_verdict {hv!r} not in {VERDICTS}")
            elif gid and not pid:
                errs.append(f"decision {n} ({ref}): a group needs a keep/reject/review map, not {hv!r}")
        elif isinstance(hv, Mapping):
            if pid and not gid:
                errs.append(f"decision {n} ({ref}): a single photo needs \"keep\"|\"reject\"|\"review\", not a map")
                continue
            bad = [str(k) for k in hv if k not in VERDICTS]
            if bad:
                errs.append(f"decision {n} ({ref}): human_verdict keys {bad} not in {VERDICTS}")
                continue
            if not all(isinstance(v, list) and all(isinstance(p, str) and p for p in v) for v in hv.values()):
                errs.append(f"decision {n} ({ref}): human_verdict values must be lists of photo ids")
                continue
            named = [p for v in hv.values() for p in v]
            if not named:
                errs.append(f"decision {n} ({ref}): human_verdict names no photos")
            elif len(named) != len(set(named)):
                errs.append(f"decision {n} ({ref}): a photo appears under more than one verdict")
            elif not hv.get("keep"):
                errs.append(f"decision {n} ({ref}): keep at least one photo from the group (policy 4)")
            elif groups is not None and gid and gid in groups and not set(named) <= set(groups[gid]):
                errs.append(f"decision {n} ({ref}): human_verdict names photos outside the group: "
                            f"{sorted(set(named) - set(groups[gid]))}")
        else:
            errs.append(f"decision {n} ({ref}): human_verdict must be a string or a map")
    if errs:
        raise ValueError("invalid decisions: " + "; ".join(errs)[:900])

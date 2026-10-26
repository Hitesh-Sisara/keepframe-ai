"""Find the trace steps where a vision result changed the agent's next action (docs/AGENT.md "Trace schema").

A pivot is a (tool_result → [model_reasoning] → tool_call) triple where the call is a direct consequence
of the numbers in the result. The three policy pivots the judges look for are detected by rule; any line
the agent explicitly tags with `pivot: true` (or a `policy` field on its reasoning) also counts.
"""

from __future__ import annotations

from typing import Any

Line = dict[str, Any]

# Bookkeeping tools the agent may call between a vision result and its consequence; they never break a
# pivot (e.g. pick_best_in_cluster → make_contact_sheet → request_human_approval).
HELPER_TOOLS = frozenset({"make_contact_sheet", "get_run_budget", "next_work_item"})
# Scoring calls that may precede the group comparison a closed-eyes result leads to (Policy 3).
SCORING_TOOLS = frozenset(
    {
        "score_sharpness",
        "score_exposure",
        "detect_faces",
        "score_eyes_open",
        "score_face_quality",
        "dedup_phash",
    }
)
MAX_SCAN = 40  # lines to look ahead for the consequent call


def _entries(result: Any) -> list[dict[str, Any]]:
    """Per-photo entries of a tool result: `{"per_photo": [...]}` (list tools) or the flat result itself."""
    if not isinstance(result, dict):
        return []
    per_photo = result.get("per_photo")
    if isinstance(per_photo, list):
        return [e for e in per_photo if isinstance(e, dict)]
    return [result]


def _eyes(result: Any) -> tuple[bool, bool]:
    """(some face closed/partial, main subject closed) across every photo in the result."""
    face_closed = main_closed = False
    for e in _entries(result):
        faces = [f for f in e.get("per_face") or [] if isinstance(f, dict)]
        if any(f.get("state") in {"closed", "partial"} for f in faces):
            face_closed = True
        if e.get("any_main_subject_closed") or any(
            f.get("is_main") and f.get("state") == "closed" for f in faces
        ):
            main_closed = True
    return face_closed, main_closed


def _borderline(result: Any) -> dict[str, Any] | None:
    return next((e for e in _entries(result) if e.get("label") == "borderline"), None)


def _num(v: Any) -> str:
    return f"{v:.0f}" if isinstance(v, (int, float)) and abs(v) >= 100 else str(v)


def _classify(result_line: Line, call_line: Line) -> tuple[str, str, str] | None:
    """Return (kind, policy, summary) when the call is the policy consequence of the result."""
    tool = result_line.get("tool")
    result = result_line.get("result") or {}
    nxt = call_line.get("tool")
    args = call_line.get("args") or {}
    prev_args = result_line.get("_call_args") or {}
    if tool == "score_sharpness" and nxt == "score_sharpness":
        hit = _borderline(result)
        if hit and (
            args.get("roi") != prev_args.get("roi") or args.get("variant") != prev_args.get("variant")
        ):
            where = (
                f"{args.get('roi')} ROI"
                if args.get("roi") != prev_args.get("roi")
                else f"{args.get('variant')} image"
            )
            summary = f"Borderline sharpness (Laplacian variance {_num(hit.get('lap_var'))}) → re-scored on the {where}"
            return ("sharpness_rescore", "Policy 2", summary)
    if tool == "score_eyes_open" and nxt == "pick_best_in_cluster":
        face_closed, main_closed = _eyes(result)
        if main_closed:
            return (
                "eyes_compare_in_group",
                "Policy 3",
                "Main subject's eyes closed → compared within the group",
            )
        if face_closed:
            summary = "A secondary face has eyes closed, main subject open → compared within the group"
            return ("eyes_compare_in_group", "Policy 3", summary)
    if tool == "pick_best_in_cluster" and nxt == "request_human_approval":
        margin = result.get("margin")
        if result.get("tie") or (isinstance(margin, (int, float)) and margin < 0.05):
            why = "tie" if result.get("tie") else f"margin {margin:.2f} < 0.05"
            if result.get("tie") and isinstance(margin, (int, float)):
                why = f"tie (margin {margin:.2f})"
            return ("small_margin_to_human", "Policy 5", f"Best-of-group {why} → asked a human")
    if call_line.get("pivot") or result_line.get("pivot"):
        return (
            "tagged",
            str(call_line.get("policy") or result_line.get("policy") or ""),
            "Agent-tagged pivot",
        )
    return None


def _skippable(result_tool: Any, call: Line) -> bool:
    tool = call.get("tool")
    if tool in HELPER_TOOLS:
        return True
    # A closed-eyes result may be followed by more scoring before the comparison it motivates.
    return result_tool == "score_eyes_open" and tool in SCORING_TOOLS and tool != "score_eyes_open"


def find_pivots(lines: list[Line]) -> list[dict[str, Any]]:
    ordered = sorted(lines, key=lambda line: int(line.get("seq", 0)))
    open_args: dict[str, Any] = {}
    pivots: list[dict[str, Any]] = []
    for i, line in enumerate(ordered):
        kind = line.get("type")
        if kind == "tool_call":
            open_args[str(line.get("tool"))] = line.get("args") or {}
            continue
        if kind != "tool_result":
            continue
        result_line = {**line, "_call_args": open_args.get(str(line.get("tool")), {})}
        reasoning: list[int] = []
        skipped: list[int] = []
        for nxt in ordered[i + 1 : i + 1 + MAX_SCAN]:
            t = nxt.get("type")
            if t == "model_reasoning":
                reasoning.append(int(nxt["seq"]))
                continue
            if t == "tool_result":
                if skipped:  # the result of a skipped helper/scoring call
                    continue
                break
            if t != "tool_call":
                break  # interrupt, run_end, ...: the chain ends here
            hit = _classify(result_line, nxt)
            if hit:
                pivots.append(
                    {
                        "kind": hit[0],
                        "policy": hit[1],
                        "summary": hit[2],
                        "result_seq": int(line["seq"]),
                        "reasoning_seqs": reasoning,
                        "via_seqs": skipped,
                        "call_seq": int(nxt["seq"]),
                    }
                )
                break
            if _skippable(line.get("tool"), nxt):
                skipped.append(int(nxt["seq"]))
                continue
            break
    return pivots


def _mentions(value: Any, ids: set[str]) -> bool:
    if isinstance(value, str):
        return value in ids
    if isinstance(value, dict):
        return any(_mentions(v, ids) for v in value.values())
    if isinstance(value, list):
        return any(_mentions(v, ids) for v in value)
    return False


def filter_for_group(lines: list[Line], group_id: str, members: list[str]) -> list[Line]:
    """Trace lines that concern one group: calls/results touching the group or a member photo, the
    results of those calls, and the reasoning lines that mention them or lead into a relevant call."""
    ids = {group_id, *members}
    ordered = sorted(lines, key=lambda line: int(line.get("seq", 0)))
    keep: set[int] = set()
    open_calls: dict[str, bool] = {}
    for line in ordered:
        seq = int(line.get("seq", 0))
        kind = line.get("type")
        hit = (
            line.get("group_id") == group_id
            or _mentions(line.get("args"), ids)
            or _mentions(line.get("decisions"), ids)
        )
        if kind == "tool_call":
            open_calls[str(line.get("tool"))] = bool(hit)
        elif kind == "tool_result":
            hit = hit or open_calls.get(str(line.get("tool")), False) or _mentions(line.get("result"), ids)
        elif kind == "model_reasoning":
            text = str(line.get("text", ""))
            hit = any(i in text for i in ids)
        if hit:
            keep.add(seq)
    # Reasoning immediately before a kept call explains that call; keep it too.
    for i, line in enumerate(ordered):
        if line.get("type") == "model_reasoning" and int(line["seq"]) not in keep:
            nxt = next((n for n in ordered[i + 1 :] if n.get("type") != "model_reasoning"), None)
            if nxt is not None and int(nxt["seq"]) in keep and nxt.get("type") == "tool_call":
                keep.add(int(line["seq"]))
    return [line for line in ordered if int(line.get("seq", 0)) in keep]

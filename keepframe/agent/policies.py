"""docs/REPORT.md §4 policies as pure checks the hooks ENFORCE (the model cannot skip them).

The numeric rules live in keepframe.worker.policies (shared with the deterministic worker) and
config/thresholds.yaml; this module turns them into gate decisions over the agent's recorded evidence:

  check_tool_call(ctx, tool, args)   -> Block | None        (P1 prerequisites, P3 eye skip, P7 budget)
  check_write(ctx, decisions)        -> WriteCheck          (P2, P3, P4, P5, P6, human-only answers)

Evidence (RunContext.photos[pid]["ev"]) uses the worker's result keys: sharpness_<roi>_<variant>, exposure,
detect_faces, eyes, face_quality, composition. Eye probabilities are Optional everywhere (small faces return
None + state "unknown"); face sources are frsInfo | yunet.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from keepframe.agent.context import RunContext
from keepframe.config import section
from keepframe.worker import policies as wp

OPTIONAL_TOOLS = ("score_composition", "enhance_keeper")
SHARP_ORDER = ("sharpness_faces_original", "sharpness_full_original", "sharpness_faces_processed",
               "sharpness_saliency_processed", "sharpness_full_processed")


@dataclass
class Block:
    """A tool call the hook cancels; ``message`` goes back to the model as the tool result."""

    policy: str
    message: str


@dataclass
class WriteCheck:
    blocks: list[Block] = field(default_factory=list)           # cancel the whole write_decision call
    approvals: list[dict[str, Any]] = field(default_factory=list)  # items that need a human first (P5)

    @property
    def ok(self) -> bool:
        return not self.blocks and not self.approvals


# --------------------------------------------------------------------------- evidence accessors


def ev(ctx: RunContext, pid: str) -> dict[str, Any]:
    return (ctx.photos.get(pid) or {}).get("ev") or {}


def sharpness_chain(e: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Sharpness results in the order they were scored (``_order`` recorded by the toolset)."""
    out = [dict(v, _key=k) for k, v in e.items() if k.startswith("sharpness_") and isinstance(v, Mapping)]
    return sorted(out, key=lambda r: r.get("_order", 0))


def final_sharpness(e: Mapping[str, Any]) -> dict[str, Any] | None:
    """The most specific sharpness result: original over processed, faces over full (docs/REPORT.md §4 P2)."""
    for k in SHARP_ORDER:
        v = e.get(k)
        if isinstance(v, Mapping):
            return dict(v)
    chain = sharpness_chain(e)
    return chain[-1] if chain else None


def faces_of(e: Mapping[str, Any]) -> list[dict[str, Any]]:
    df = e.get("detect_faces")
    return list(df.get("faces") or []) if isinstance(df, Mapping) else []


def n_faces_known(ctx: RunContext, pid: str) -> int | None:
    """Detected faces if detect_faces ran, else the manifest's frsInfo count (None = unknown)."""
    e = ev(ctx, pid)
    if isinstance(e.get("detect_faces"), Mapping):
        return len(faces_of(e))
    return (ctx.photos.get(pid) or {}).get("known_faces")


def tried(e: Mapping[str, Any]) -> list[str]:
    out = []
    for r in sharpness_chain(e):
        roi = r.get("roi_requested") or r["_key"].split("_")[1]
        out.append(f"{roi}:{r.get('variant_requested') or r.get('variant', 'processed')}")
    return out


def pending_sharpness_step(ctx: RunContext, pid: str) -> dict[str, str] | None:
    """P2: the escalation still owed for this photo, or None if its sharpness label is final."""
    e = ev(ctx, pid)
    chain = sharpness_chain(e)
    if not chain:
        return None
    last = chain[-1]
    n = n_faces_known(ctx, pid) or 0
    has_orig = bool((ctx.photos.get(pid) or {}).get("has_original"))
    return wp.next_sharpness_step(last, n, has_orig, tried(e))


def evidence(ctx: RunContext, pid: str) -> wp.PhotoEvidence:
    e = ev(ctx, pid)
    p = ctx.photos.get(pid) or {}
    return wp.PhotoEvidence(
        photo_id=pid,
        sharpness=final_sharpness(e),
        exposure=e.get("exposure"),
        faces=faces_of(e),
        faces_known=isinstance(e.get("detect_faces"), Mapping),
        mfaces=p.get("known_faces"),
        eyes=e.get("eyes"),
        eyes_known=isinstance(e.get("eyes"), Mapping),
        exposure_time_s=p.get("exposure_time_s"),
        hour=p.get("hour"),
    )


def eyes_needed(ctx: RunContext, pid: str) -> bool:
    """P3: eye state is owed when faces were detected and the scene is not dark-with-small-faces."""
    e = ev(ctx, pid)
    faces = faces_of(e)
    if not faces or isinstance(e.get("eyes"), Mapping):
        return False
    return wp.should_run_eyes((e.get("exposure") or {}).get("mean_L"), faces)


# --------------------------------------------------------------------------- tool-call gates


def check_tool_call(ctx: RunContext, tool: str, args: Mapping[str, Any]) -> Block | None:
    if tool in OPTIONAL_TOOLS and not wp.optional_tools_allowed(ctx.remaining_frac()):
        return Block("P7", f"Policy 7 (budget): {ctx.remaining_frac():.0%} of the run budget left (< 20%); "
                           f"{tool} is optional and is skipped. Finish with the evidence you have.")
    if tool in ("score_eyes_open", "score_face_quality"):
        pids = args.get("photo_ids") or ([args["photo_id"]] if args.get("photo_id") else [])
        missing = [str(p) for p in pids if not isinstance(ev(ctx, str(p)).get("detect_faces"), Mapping)]
        if missing:
            return Block("P1", f"Call detect_faces(photo_ids={missing}) before {tool}.")
    if tool == "pick_best_in_cluster":
        gid = str(args.get("group_id", ""))
        g = ctx.groups.get(gid)
        if g is None:
            return Block("P1", f"Unknown group {gid}; call next_work_item to get a group.")
        missing = []
        for pid in g["members"]:
            e = ev(ctx, pid)
            if not sharpness_chain(e):
                missing.append(f"score_sharpness({pid})")
            known = (ctx.photos.get(pid) or {}).get("known_faces")
            if not isinstance(e.get("detect_faces"), Mapping) and known != 0:
                missing.append(f"detect_faces({pid})")
            elif eyes_needed(ctx, pid):
                missing.append(f"score_eyes_open({pid})")
        if missing:
            return Block("P1", "Policy 1: score every frame of the group before ranking it. Missing: "
                               + ", ".join(missing[:8]))
    return None


# --------------------------------------------------------------------------- write gates


def _codes(d: Mapping[str, Any]) -> list[str]:
    return [str(c).partition(":")[0] for c in d.get("reason_codes") or []]


def human_answer_for(ctx: RunContext, pid: str) -> str | None:
    item = ctx.item_of_photo(pid)
    keys = [k for k in (item.item_id if item else None, ctx.group_of(pid), pid) if k]
    for k in keys:
        ans = ctx.human_answers.get(k)
        if ans and pid in ans:
            return str(ans[pid])
    return None


def human_source_for(ctx: RunContext, pid: str) -> str | None:
    item = ctx.item_of_photo(pid)
    for k in (item.item_id if item else None, ctx.group_of(pid), pid):
        if k and k in ctx.human_sources:
            return ctx.human_sources[k]
    return None


def group_needs_approval(ctx: RunContext, gid: str) -> dict[str, Any] | None:
    """P4/P5 group triggers from the recorded pick_best result: margin < 0.05, tie, or every frame failed."""
    g = ctx.groups.get(gid) or {}
    best = g.get("best")
    if not best:
        return None
    pol = section("policy")
    members = list(g["members"])
    gd = wp.decide_group(gid, best, {p: evidence(ctx, p) for p in members})
    if not gd.needs_approval:
        return None
    return {"group_id": gid, "reasons": gd.approval_reasons, "margin": gd.margin, "tie": gd.tie,
            "all_failed": gd.all_failed, "keeper": gd.keeper_photo_id,
            "threshold": float(pol["best_margin_review"])}


def check_write(ctx: RunContext, decisions: Sequence[Mapping[str, Any]]) -> WriteCheck:
    out = WriteCheck()
    pol = section("policy")
    eyes_cfg = section("eyes")
    reject_min = float(pol["reject_conf_min"])
    by_group: dict[str, dict[str, str]] = {}
    for d in decisions:
        pid = str(d.get("photo_id"))
        verdict = d.get("verdict")
        by = d.get("decided_by", "agent")
        codes = _codes(d)
        if by in ("human", "scripted", "lead_test"):
            if human_answer_for(ctx, pid) is None:
                out.blocks.append(Block("HITL", f"{pid}: decided_by='human' needs a human answer from "
                                                "request_human_approval; the agent cannot decide as a human."))
            continue
        if by == "worker":
            out.blocks.append(Block("HITL", f"{pid}: the agent writes decided_by='agent' (or 'human' after an "
                                            "approval), never 'worker'."))
            continue
        gid = d.get("group_id") or ctx.group_of(pid)
        if gid:
            by_group.setdefault(str(gid), {})[pid] = str(verdict)
        e = ev(ctx, pid)
        # P2 borderline -> faces/original before labelling
        step = pending_sharpness_step(ctx, pid)
        if step is not None and (verdict == "reject" or (final_sharpness(e) or {}).get("label") != "sharp"):
            if step["roi"] == "faces" and not isinstance(e.get("detect_faces"), Mapping):
                hint = f"detect_faces(photo_id='{pid}') then score_sharpness(photo_id='{pid}', roi='faces', variant='processed')"
            else:
                hint = f"score_sharpness(photo_id='{pid}', roi='{step['roi']}', variant='{step['variant']}')"
            out.blocks.append(Block("P2", f"Policy 2: sharpness for {pid} is not final "
                                          f"({(final_sharpness(e) or {}).get('label')}); call {hint} before "
                                          "labelling it."))
            continue
        if verdict == "reject":
            # blur rejects need sharpness evidence that says blurry
            if any(c in ("BLUR_MOTION", "BLUR_FOCUS") for c in codes):
                fs = final_sharpness(e)
                if not fs:
                    out.blocks.append(Block("P2", f"{pid}: no sharpness evidence; score it before a blur reject."))
                    continue
                if fs.get("label") != "blurry":
                    out.blocks.append(Block("P2", f"{pid}: final sharpness is '{fs.get('label')}' "
                                                  f"(lap_var {fs.get('lap_var')}), not blurry; a blur reject "
                                                  "is not supported by the evidence."))
                    continue
                pe = evidence(ctx, pid)
                if "BLUR_MOTION" in codes and wp.artistic_intent(fs, pe.exposure, pe.exposure_time_s, pe.hour):
                    out.blocks.append(Block("P6", f"Policy 6: {pid} shows motion blur with a long exposure or a "
                                                  "dark reception frame; use verdict 'review' with "
                                                  "REVIEW_ARTISTIC_INTENT instead of reject."))
                    continue
            # P3 eyes per-face, main subject only
            if "EYES_CLOSED" in codes:
                eyes = e.get("eyes")
                if not isinstance(eyes, Mapping):
                    out.blocks.append(Block("P3", f"{pid}: EYES_CLOSED needs score_eyes_open evidence."))
                    continue
                dfct = wp.eyes_defect(eyes, faces_of(e), eyes_cfg)
                if dfct is None or dfct.code != "EYES_CLOSED":
                    alt = (" With >= 3 faces and one non-main face closed the verdict is 'review' with "
                           "REVIEW_POLICY_GROUP_EYES." if dfct is not None else "")
                    out.blocks.append(Block("P3", f"Policy 3: the main subject of {pid} is not closed with "
                                                  f"p >= {eyes_cfg['reject_closed_p']}; EYES_CLOSED reject "
                                                  f"is not allowed.{alt}"))
                    continue
            # P5 low confidence -> human
            conf = float(d.get("confidence", 0.0))
            if conf < reject_min:
                out.approvals.append({"photo_id": pid, "group_id": gid, "proposed": "reject",
                                      "reason": f"reject at confidence {conf:.2f} < {reject_min} "
                                                f"({', '.join(codes)})", "confidence": conf})
    # P4 never reject a whole group; P5 small margin / tie -> human
    for gid, verdicts in by_group.items():
        g = ctx.groups.get(gid)
        if not g:
            continue
        final = {p: (ctx.decided.get(p) or {}).get("verdict") for p in g["members"]}
        final.update(verdicts)
        if g["members"] and all(final.get(p) == "reject" for p in g["members"]):
            out.blocks.append(Block("P4", f"Policy 4: never reject a whole group; keep the best-scoring frame "
                                          f"of {gid} with KEEP_BEST_OF_GROUP and request human approval."))
            continue
        trig = group_needs_approval(ctx, gid)
        if trig and not ctx.human_answers.get(gid):
            keep = [p for p, v in final.items() if v == "keep"]
            rej = [p for p, v in final.items() if v == "reject"]
            rev = [p for p, v in final.items() if v == "review"]
            out.approvals.append({"group_id": gid, "proposed": {"keep": keep, "reject": rej, "review": rev},
                                  "reason": "; ".join(trig["reasons"]) or "group needs a human check",
                                  "confidence": 0.6, "contact_sheet_key": g.get("contact_sheet_key")})
    return out

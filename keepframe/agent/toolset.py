"""Strands ``@tool`` wrappers over the plain tool functions (tool modules never import strands).

``Toolset`` binds one run: it holds the RunContext (event_id / run_id are injected, the model never passes
them), records every tool result as evidence for the hooks' policy checks, and selects the backend:
``CULL_TOOLS=stub`` (deterministic scenario, keepframe.agent._tool_stubs) or ``real`` (OpenCV 5 on the
event's images, keepframe.agent._tool_real).

The tool surface the planner sees (plus the work-queue helper):
  next_work_item, score_sharpness, score_exposure, detect_faces, score_eyes_open, score_face_quality,
  pick_best_in_cluster, score_composition, enhance_keeper, make_contact_sheet, request_human_approval,
  write_decision, get_run_budget
ingest_event / compute_hashes / cluster_near_duplicates already ran in the batch worker; the agent starts
from its results/<eventId>/{groups.json,decisions.jsonl} (see strands_agent.load_work).
"""

from __future__ import annotations

import os
import time
from typing import Any

from strands import tool
from strands.types.tools import ToolContext

from keepframe.agent import policies as ap
from keepframe.agent.budget import get_run_budget as _budget
from keepframe.agent.context import RunContext
from keepframe.tools import approval as approval_mod
from keepframe.tools import decision as decision_mod
from keepframe.worker import policies as wp

SLIM_DROP = {"votes", "roi_rect", "build_info_head", "timings", "landmarks5", "saliency_centroid",
             "saliency_bbox", "breakdown_detail"}


def slim(v: Any, depth: int = 0) -> Any:
    """Compact a tool result for the model (full result still goes to the trace and the evidence)."""
    if isinstance(v, dict):
        return {k: slim(x, depth + 1) for k, x in v.items() if k not in SLIM_DROP and x is not None}
    if isinstance(v, list):
        return [slim(x, depth + 1) for x in v[:12]]
    if isinstance(v, float):
        return round(v, 4)
    return v


def slim_dict(v: dict[str, Any]) -> dict[str, Any]:
    """slim() for a tool's top-level result dict (typed boundary for the @tool wrappers)."""
    out: dict[str, Any] = slim(v)
    return out


def tools_backend(event_id: str, scenario: dict[str, Any] | None = None) -> Any:
    kind = os.environ.get("CULL_TOOLS", "real")
    if kind == "stub":
        from keepframe.agent._tool_stubs import StubTools, demo_scenario

        return StubTools(scenario or demo_scenario(event_id))
    if kind == "real":
        from keepframe.agent._tool_real import RealTools

        return RealTools(event_id)
    raise ValueError(f"CULL_TOOLS must be stub|real, got {kind!r}")


class Toolset:
    """One run's tools. ``ctx`` is the live RunContext (mirrored into agent.state by the hooks)."""

    def __init__(self, ctx: RunContext, backend: Any, trace: Any = None) -> None:
        self.ctx = ctx
        self.b = backend
        self.trace = trace
        self._order = 0

    # ------------------------------------------------------------------ evidence

    def _ev(self, pid: str) -> dict[str, Any]:
        if pid not in self.ctx.photos:
            raise KeyError(f"photo {pid} is not in this run's work set; use next_work_item")
        ev: dict[str, Any] = self.ctx.photos[pid].setdefault("ev", {})
        return ev

    def _pids(self, photo_ids: Any) -> list[str]:
        """Validate a tool's photo list: non-empty, unique, all in this run's work set."""
        if isinstance(photo_ids, str):
            photo_ids = [photo_ids]
        pids = list(dict.fromkeys(str(p) for p in (photo_ids or [])))
        if not pids:
            raise ValueError("photo_ids is empty")
        for p in pids:
            self._ev(p)
        return pids

    def _record(self, pid: str, key: str, res: dict[str, Any]) -> None:
        self._order += 1
        self._ev(pid)[key] = {**res, "_order": max(self._order, self._max_order(pid) + 1)}

    def _max_order(self, pid: str) -> int:
        return max((int(v.get("_order", 0)) for v in self._ev(pid).values() if isinstance(v, dict)), default=0)

    def _face_boxes(self, pid: str) -> list[list[float]]:
        df = self._ev(pid).get("detect_faces") or {}
        faces = df.get("faces") or []
        p = self.ctx.photos[pid]
        W = df.get("image_w") or p.get("width")
        H = df.get("image_h") or p.get("height")
        if not faces or not W or not H:
            return []
        return [[f["bbox_px"][0] / W, f["bbox_px"][1] / H, f["bbox_px"][2] / W, f["bbox_px"][3] / H] for f in faces]

    # ------------------------------------------------------------------ tools

    def tools(self) -> list[Any]:
        ts = self

        @tool
        def next_work_item() -> dict[str, Any]:
            """Get the next group or photo to work on, with the batch worker's baseline verdicts and why it
            needs the agent. Returns {done: true} when nothing is left; then reply with a short summary."""
            ctx = ts.ctx
            cur = ctx.item(ctx.current_item)
            if cur is not None:
                ctx.refresh_item(cur)
                if cur.status in ("open", "active"):
                    return {"item_id": cur.item_id, "kind": cur.kind, "note": "finish this item first",
                            "photo_ids": cur.photo_ids, "undecided": ctx.undecided(cur)}
            nxt = next((i for i in ctx.items if i.status == "open"), None)
            if nxt is None:
                ctx.current_item = None
                return {"done": True, "decided": len(ctx.decided), "escalations": ctx.escalations}
            nxt.status = "active"
            ctx.current_item = nxt.item_id
            photos = []
            for pid in nxt.photo_ids:
                p = ctx.photos[pid]
                photos.append({"photo_id": pid, "known_faces": p.get("known_faces"),
                               "has_original": p.get("has_original"), "worker": nxt.baseline.get(pid),
                               "exif": {"exposure_time_s": p.get("exposure_time_s"), "hour": p.get("hour")}})
            out: dict[str, Any] = {"item_id": nxt.item_id, "kind": nxt.kind, "why": nxt.why, "photos": photos,
                                   "items_left": len([i for i in ctx.items if i.status == "open"])}
            if nxt.group_id:
                out["group_id"] = nxt.group_id
                out["span_s"] = nxt.span_s
            return out

        @tool
        def score_sharpness(photo_ids: list[str], roi: str = "full", variant: str = "processed") -> dict[str, Any]:
            """Sharpness of one or more photos of the current item: variance of Laplacian, Tenengrad, FFT
            high-frequency energy, BRISQUE (OpenCV 5). roi: full | faces (union of detected face boxes; run
            detect_faces first) | saliency. variant: processed (1024-px proxy) | original (camera original
            resized to 1024 px). Returns {per_photo:[{photo_id, lap_var, tenengrad, fft_hf, brisque,
            label: sharp|borderline|blurry, confidence, blur_kind: motion|focus|null, roi_used}]}."""
            if roi not in ("full", "faces", "saliency"):
                raise ValueError("roi must be full|faces|saliency")
            if variant not in ("processed", "original"):
                raise ValueError("variant must be processed|original")
            out = []
            for pid in ts._pids(photo_ids):
                boxes = ts._face_boxes(pid) if roi == "faces" else None
                res = ts.b.score_sharpness(pid, roi, variant, boxes)
                res["roi_requested"] = roi
                res["variant_requested"] = variant
                key = f"sharpness_{res.get('roi_used', roi)}_{variant}"
                ts._record(pid, key + ("_fallback" if res.get("variant_fallback") else ""), res)
                out.append({"photo_id": pid, **{k: v for k, v in res.items()
                                                 if k not in ("roi_requested", "variant_requested")}})
            return slim_dict({"per_photo": out})

        @tool
        def score_exposure(photo_ids: list[str]) -> dict[str, Any]:
            """Exposure from the Lab L histogram for one or more photos: clipping %, mean/std of L, entropy.
            Returns {per_photo:[{photo_id, clip_lo_pct, clip_hi_pct, mean_L, std_L, entropy,
            label: ok|under|over|low_contrast, confidence}]}."""
            out = []
            for pid in ts._pids(photo_ids):
                res = ts.b.score_exposure(pid)
                ts._record(pid, "exposure", res)
                out.append({"photo_id": pid, **res})
            return slim_dict({"per_photo": out})

        @tool
        def detect_faces(photo_ids: list[str]) -> dict[str, Any]:
            """Faces with 5 landmarks: reuses the platform's frsInfo boxes when indexed (landmarks refined with
            YuNet), else cv2.FaceDetectorYN. Returns {per_photo:[{photo_id, n_faces, main_face_px,
            faces:[{face_id, bbox_px, score, source: frsInfo|yunet}]}]}."""
            out = []
            for pid in ts._pids(photo_ids):
                res = ts.b.detect_faces(pid)
                ts._record(pid, "detect_faces", res)
                faces = res.get("faces") or []
                main = max((min(f["bbox_px"][2], f["bbox_px"][3]) for f in faces), default=None)
                out.append({"photo_id": pid, "n_faces": len(faces),
                            "main_face_px": int(main) if main is not None else None, "faces": faces})
            return slim_dict({"per_photo": out})

        @tool
        def score_eyes_open(photo_ids: list[str]) -> dict[str, Any]:
            """Eye state per face (OCEC ONNX via cv2.dnn on landmark-centred eye crops). Probabilities are
            null for small faces (state unknown). Dark scenes with small faces are skipped (Policy 3).
            Returns {per_photo:[{photo_id, per_face:[{face_id, left_open_p, right_open_p,
            state: open|closed|partial|unknown, is_main}], any_main_subject_closed, main_subject_face_id}]}."""
            out = []
            for pid in ts._pids(photo_ids):
                e = ts._ev(pid)
                faces = e.get("detect_faces")
                if not isinstance(faces, dict):
                    out.append({"photo_id": pid, "error": "run detect_faces first"})
                    continue
                mean_l = (e.get("exposure") or {}).get("mean_L")
                res: dict[str, Any]
                if faces.get("faces") and not wp.should_run_eyes(mean_l, faces["faces"]):
                    res = {"per_face": [], "skipped": "Policy 3: dark scene and small faces; eye state unknown",
                           "any_main_subject_closed": False, "main_subject_face_id": None}
                elif not faces.get("faces"):
                    res = {"per_face": [], "any_main_subject_closed": False, "main_subject_face_id": None,
                           "skipped": "no faces"}
                else:
                    res = ts.b.score_eyes_open(pid, {k: v for k, v in faces.items() if k != "_order"})
                ts._record(pid, "eyes", res)
                out.append({"photo_id": pid, **res})
            return slim_dict({"per_photo": out})

        @tool
        def score_face_quality(photo_ids: list[str]) -> dict[str, Any]:
            """Face image quality per face: SFace-aligned 112x112 crop -> eDifFIQA(T) ONNX via cv2.dnn,
            plus per-face Laplacian. Returns {per_photo:[{photo_id, per_face:[{face_id, fiqa, lap_var,
            minPx, good}], main_subject_face_id}]}."""
            out = []
            for pid in ts._pids(photo_ids):
                faces = ts._ev(pid).get("detect_faces")
                res: dict[str, Any]
                if not isinstance(faces, dict) or not faces.get("faces"):
                    res = {"per_face": [], "main_subject_face_id": None}
                else:
                    res = ts.b.score_face_quality(pid, {k: v for k, v in faces.items() if k != "_order"})
                ts._record(pid, "face_quality", res)
                out.append({"photo_id": pid, **res})
            return slim_dict({"per_photo": out})

        @tool
        def pick_best_in_cluster(group_id: str) -> dict[str, Any]:
            """Rank a group's frames with the deterministic weighted score
            (0.35 sharpness + 0.25 eyes open + 0.15 exposure + 0.15 face quality + 0.10 composition) over the
            results recorded so far. Returns {best_photo_id, ranking:[{photo_id, score}], tie, margin}."""
            g = ts.ctx.groups[group_id]
            results = {}
            for pid in g["members"]:
                e = ts._ev(pid)
                r: dict[str, Any] = {k: v for k, v in e.items() if k.startswith("sharpness_")}
                for k in ("exposure", "eyes", "face_quality", "composition"):
                    if k in e:
                        r[k] = e[k]
                results[pid] = r
            res = ts.b.pick_best_in_cluster(group_id, results)
            g["best"] = res
            return slim_dict({k: res[k] for k in ("best_photo_id", "tie", "margin")} |
                        {"ranking": [{"photo_id": x["photo_id"], "score": x["score"]} for x in res["ranking"]]})

        @tool
        def score_composition(photo_id: str) -> dict[str, Any]:
            """Optional tiebreaker: saliency-based subject centrality (and NIMA when available).
            Skipped automatically when less than 20% of the run budget is left."""
            ts._ev(photo_id)
            res = ts.b.score_composition(photo_id)
            ts._record(photo_id, "composition", res)
            return slim_dict(res)

        @tool
        def enhance_keeper(photo_id: str, ops: list[str] | None = None) -> dict[str, Any]:
            """Optional: OpenCV preview enhancement of a keeper (grayworld WB, NLMeans denoise, CLAHE) written
            to preview/. Originals are never touched. Skipped under 20% budget."""
            ts._ev(photo_id)
            return slim_dict(ts.b.enhance_keeper(ts.ctx.event_id, photo_id, ops))

        @tool
        def make_contact_sheet(group_id: str) -> dict[str, Any]:
            """Render the group's frames side by side (cv2.hconcat / cv2.putText with cv2.FontFace) to
            sheets/<eventId>/<groupId>.jpg for a human reviewer. Returns {key, n_tiles}."""
            g = ts.ctx.groups[group_id]
            best = g.get("best") or {}
            order = [r["photo_id"] for r in best.get("ranking", [])] or list(g["members"])
            caps = {r["photo_id"]: f"{r['photo_id'][:8]}  {r['score']:.2f}" for r in best.get("ranking", [])}
            res = ts.b.make_contact_sheet(ts.ctx.event_id, group_id, order, caps, best.get("best_photo_id"))
            g["contact_sheet_key"] = res["key"]
            return slim_dict(res)

        @tool(context=True)
        def request_human_approval(tool_context: ToolContext, items: list[dict[str, Any]]) -> dict[str, Any]:
            """Pause the run and ask a person (docs/REPORT.md §4 P4/P5). items: [{group_id | photo_id, proposed
            ({keep:[...], reject:[...], review:[...]} for a group, or one verdict for a photo), reason,
            confidence, contact_sheet_key}]. Returns {decisions:[{id, human_verdict, note}]} once answered;
            then write those verdicts with decided_by='human'."""
            ctx = ts.ctx
            for it in items:
                gid = it.get("group_id")
                if gid and not it.get("contact_sheet_key"):
                    it["contact_sheet_key"] = (ctx.groups.get(gid) or {}).get("contact_sheet_key")
            res = approval_mod.request_human_approval(tool_context, items, ctx.event_id, ctx.run_id,
                                                      on_pending=ts.on_pending)
            ts.absorb_answers(res["decisions"])
            return res

        @tool
        def write_decision(decisions: list[dict[str, Any]]) -> dict[str, Any]:
            """Write verdicts (keep | reject | review; there is no delete) for photos of this event to
            DynamoDB and results/<eventId>/decisions.jsonl. Each: {photo_id, verdict, reason_codes:[>=1 of
            the fixed codes], confidence 0..1, group_id?, decided_by: agent|human, reason_text? (<=140
            chars, templated from the tool numbers when omitted)}. Policies are enforced before writing."""
            ctx = ts.ctx
            rows = []
            for d in decisions:
                d = dict(d)
                d.setdefault("decided_by", "agent")
                pid = str(d.get("photo_id"))
                d.setdefault("group_id", ctx.group_of(pid))
                if d["decided_by"] in ("human", "scripted", "lead_test"):
                    # An answer to an approval: attribution comes from how the answer arrived, never from the
                    # model (scripted auto-approvals are decided_by='scripted', not 'human'). The proposal's
                    # confidence is kept; the model's own wording is dropped from the reason text.
                    src = ap.human_source_for(ctx, pid) or "ui"
                    d["human_source"] = src
                    d["decided_by"] = src if src in ("scripted", "lead_test") else "human"
                    d.pop("reason_text", None)
                    item = ctx.item_of_photo(pid)
                    note = ctx.human_notes.get(item.item_id if item else "", "") or \
                        ctx.human_notes.get(str(d.get("group_id") or ""), "")
                    if note:
                        d["note"] = note
                else:
                    d.pop("human_source", None)
                if d["decided_by"] in ("human", "scripted", "lead_test"):
                    d["agent_confidence"] = ctx.photos.get(pid, {}).get("agent_confidence", d.get("confidence"))
                else:
                    d["agent_confidence"] = d.get("confidence")
                d["scores"] = ts.scores_for(pid)
                rows.append(d)
            res = decision_mod.write_decision(ctx.event_id, rows, run_id=ctx.run_id)
            for d in rows:
                ctx.decided[str(d["photo_id"])] = {"verdict": d["verdict"], "decided_by": d["decided_by"],
                                                   "reason_codes": d["reason_codes"],
                                                   "confidence": d.get("confidence")}
            for it in ctx.items:
                ctx.refresh_item(it)
            return res

        @tool
        def get_run_budget() -> dict[str, Any]:
            """Tool calls used vs the 1.5 x N cap, wall clock vs 60 min, calls on the current item vs 25."""
            return _budget(ts.ctx)

        return [next_work_item, score_sharpness, score_exposure, detect_faces, score_eyes_open,
                score_face_quality, pick_best_in_cluster, score_composition, enhance_keeper, make_contact_sheet,
                request_human_approval, write_decision, get_run_budget]

    # ------------------------------------------------------------------ helpers used by hooks/tools

    def scores_for(self, pid: str) -> dict[str, Any]:
        """Raw tool outputs for the PHOTO# item (the API/UI shape: scores.faces[] merged per face)."""
        e = (self.ctx.photos.get(pid) or {}).get("ev") or {}
        fs = ap.final_sharpness(e)
        out: dict[str, Any] = {}
        if fs:
            out["sharpness"] = {k: v for k, v in fs.items() if not k.startswith("_") and k not in
                                ("votes", "roi_requested", "variant_requested")}
            out["sharpness_chain"] = ap.tried(e)
        if e.get("exposure"):
            out["exposure"] = {k: v for k, v in e["exposure"].items() if not k.startswith("_")}
        faces = ap.faces_of(e)
        if isinstance(e.get("detect_faces"), dict):
            eyes = {f["face_id"]: f for f in (e.get("eyes") or {}).get("per_face", [])}
            fq = {f["face_id"]: f for f in (e.get("face_quality") or {}).get("per_face", [])}
            main = (e.get("eyes") or {}).get("main_subject_face_id") or (e.get("face_quality") or {}).get(
                "main_subject_face_id")
            out["n_faces"] = len(faces)
            out["faces"] = [{
                "face_id": f["face_id"], "bbox_px": f.get("bbox_px"), "source": f.get("source"),
                "min_px": int(min(f["bbox_px"][2], f["bbox_px"][3])) if f.get("bbox_px") else None,
                "minPx": (fq.get(f["face_id"]) or {}).get("minPx"),
                "state": (eyes.get(f["face_id"]) or {}).get("state"),
                "left_open_p": (eyes.get(f["face_id"]) or {}).get("left_open_p"),
                "right_open_p": (eyes.get(f["face_id"]) or {}).get("right_open_p"),
                "fiqa": (fq.get(f["face_id"]) or {}).get("fiqa"),
                "main_subject": f["face_id"] == main,
            } for f in faces]
        gid = self.ctx.group_of(pid)
        best = (self.ctx.groups.get(gid) or {}).get("best") if gid else None
        if best:
            r = next((x for x in best["ranking"] if x["photo_id"] == pid), None)
            if r:
                out["best_score"] = r.get("score")
                out["breakdown"] = r.get("breakdown")
            out["margin"] = best.get("margin")
        return out

    queue: Any = None  # set by strands_agent to KeepFrameHooks._queue (DynamoDB GSI3 + counters)

    def on_pending(self, items: list[dict[str, Any]], name: str, interrupt_id: str) -> None:
        """Before the interrupt is raised: put the items on the approval queue."""
        if self.queue is not None:
            self.queue(items, name, interrupt_id)
        else:
            self.ctx.pending[name] = {"items": items, "interrupt_id": interrupt_id, "ts": time.time()}

    def absorb_answers(self, decisions: list[dict[str, Any]]) -> None:
        """Turn the human's answers into {item_id: {photo_id: verdict}} so check_write lets
        decided_by='human' through only for exactly these photos."""
        ctx = self.ctx
        for d in decisions:
            hv = d.get("human_verdict")
            gid = d.get("group_id")
            pid = d.get("photo_id")
            key = gid or pid
            if not key:
                continue
            ans: dict[str, str] = {}
            if isinstance(hv, dict):
                for verdict, ids in hv.items():
                    for p in ids if isinstance(ids, list) else [ids]:
                        ans[str(p)] = str(verdict)
            elif isinstance(hv, str) and pid:
                ans[str(pid)] = hv
            elif isinstance(hv, str) and gid and hv in ("approve", "approved"):
                pass
            ctx.human_answers[str(key)] = ans
            ctx.human_sources[str(key)] = ctx.human_source or "ui"
            if d.get("note"):
                ctx.human_notes[str(key)] = str(d["note"])[:500]

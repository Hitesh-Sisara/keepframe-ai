"""FakeModel: a deterministic scripted planner implementing the Strands ``Model`` interface.

``CULL_MODEL_PROVIDER=fake``. It makes zero network calls. Each turn it reads ONLY what a real model would
see: the conversation (its own earlier tool calls and the tool results) and the system prompt's policies.
From that it emits one text block (the reasoning that becomes a ``model_reasoning`` trace line) and one
tool_use block, or a final text when the work queue is empty. Because it decides from the tool *results*,
the vision output really does change the next call (borderline -> faces ROI; main-subject eyes closed ->
compare within group; small margin -> human), and the hooks still enforce every policy on its calls.
"""

from __future__ import annotations

import itertools
import json
import os
import threading
from collections.abc import AsyncGenerator, AsyncIterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from strands.models.model import Model
from strands.types.content import Messages, SystemContentBlock
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

if TYPE_CHECKING:
    from strands.agent.agent_metadata import AgentMetadata

from keepframe.config import section

_ids = itertools.count(1)
_PREFIX = os.urandom(3).hex()  # tool-use ids must be unique across processes (resume)


def _results(messages: Sequence[Mapping[str, Any]]) -> list[tuple[str, dict[str, Any], Any, str]]:
    """[(tool_name, input, parsed_result, status)] in order, pairing toolUse with toolResult."""
    uses: dict[str, tuple[str, dict[str, Any]]] = {}
    out = []
    for m in messages:
        for c in m.get("content", []):
            if "toolUse" in c:
                u = c["toolUse"]
                uses[u["toolUseId"]] = (u["name"], u.get("input") or {})
            elif "toolResult" in c:
                r = c["toolResult"]
                name, inp = uses.get(r["toolUseId"], ("?", {}))
                txt = "".join(x.get("text", "") for x in r.get("content", []) if isinstance(x, dict))
                try:
                    parsed: Any = json.loads(txt)
                except ValueError:
                    parsed = txt
                out.append((name, inp, parsed, r.get("status", "success")))
    return out


class Planner:
    """Pure function of the tool history -> (reasoning text, tool name, tool input) or (final text, None)."""

    def __init__(self, history: list[tuple[str, dict[str, Any], Any, str]]) -> None:
        self.h = history
        self.pol = section("policy")
        self.eyes_cfg = section("eyes")

    # -------------------------------------------------------------- history views

    def current_item(self) -> dict[str, Any] | None:
        for name, _inp, res, st in reversed(self.h):
            if name == "next_work_item" and st == "success" and isinstance(res, dict):
                return None if res.get("done") else res
        return None

    def since_item(self) -> list[tuple[str, dict[str, Any], Any, str]]:
        idx = max((i for i, (n, *_r) in enumerate(self.h) if n == "next_work_item"), default=-1)
        return self.h[idx + 1:]

    def per_photo(self, tool: str, **match: Any) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name, inp, res, st in self.since_item():
            if name != tool or st != "success" or not isinstance(res, dict):
                continue
            if any(inp.get(k, d) != v for k, (v, d) in match.items()):
                continue
            for r in res.get("per_photo", []):
                out[r["photo_id"]] = r
        return out

    def last(self, tool: str) -> tuple[dict[str, Any], Any, str] | None:
        for name, inp, res, st in reversed(self.since_item()):
            if name == tool:
                return inp, res, st
        return None

    def cancelled(self) -> str | None:
        """A hook-cancelled or failed last call: its message steers the next step."""
        if not self.h:
            return None
        name, _inp, res, st = self.h[-1]
        if st == "error":
            return res if isinstance(res, str) else json.dumps(res)
        return None

    # -------------------------------------------------------------- decision

    def step(self) -> tuple[str, str | None, dict[str, Any]]:
        if not self.h:
            return ("Start: take the first work item the batch worker flagged.", "next_work_item", {})
        err = self.cancelled()
        last_name = self.h[-1][0]
        if err and "Policy" in err and last_name == "write_decision":
            return self.after_policy_block(err)
        if err and last_name not in ("write_decision", "request_human_approval"):
            if "cap" in err.lower() or "escalat" in err.lower():
                return ("A hard cap was hit; the hook escalated this item. Move on.", "next_work_item", {})
        item = self.current_item()
        if item is None:
            name, _i, res, _s = self.h[-1]
            if name == "next_work_item" and isinstance(res, dict) and res.get("done"):
                return (f"Work queue empty: {res.get('decided', 0)} photos decided, "
                        f"{res.get('escalations', 0)} escalations. Done.", None, {})
            return ("Take the next work item.", "next_work_item", {})
        if item.get("note") == "finish this item first":
            pass
        if item["kind"] == "group":
            return self.group_step(item)
        return self.singleton_step(item)

    def after_policy_block(self, err: str) -> tuple[str, str | None, dict[str, Any]]:
        item = self.current_item() or {}
        txt = err.replace("Error: ", "")
        if "score_sharpness(" in txt:
            import re

            m = re.search(r"score_sharpness\(photo_id='([^']+)', roi='(\w+)', variant='(\w+)'\)", txt)
            if m:
                return (f"The write was blocked: {txt[:160]}. Follow the policy.", "score_sharpness",
                        {"photo_ids": [m.group(1)], "roi": m.group(2), "variant": m.group(3)})
        if "detect_faces(" in txt:
            import re

            m = re.search(r"detect_faces\(photo_id='([^']+)'\)", txt)
            if m:
                return (f"The write was blocked: {txt[:160]}.", "detect_faces", {"photo_ids": [m.group(1)]})
        if "REVIEW_ARTISTIC_INTENT" in txt or "REVIEW_POLICY_GROUP_EYES" in txt:
            return self.rewrite_as_review(txt)
        return (f"Write blocked ({txt[:120]}); asking a human instead.", "next_work_item", {}) if not item else \
            self.escalate_item(item, txt[:200])

    def rewrite_as_review(self, txt: str) -> tuple[str, str | None, dict[str, Any]]:
        inp, _res, _st = self.last("write_decision") or ({"decisions": []}, None, "")
        decs = []
        for d in inp.get("decisions", []):
            d = dict(d)
            if d.get("verdict") == "reject":
                code = "REVIEW_ARTISTIC_INTENT" if "ARTISTIC" in txt else "REVIEW_POLICY_GROUP_EYES"
                d["verdict"] = "review"
                d["reason_codes"] = [code] + [c for c in d.get("reason_codes", []) if c != "EYES_CLOSED"]
                d.pop("reason_text", None)
            decs.append(d)
        return ("Policy says review, not reject; rewriting the verdict.", "write_decision", {"decisions": decs})

    def escalate_item(self, item: dict[str, Any], why: str) -> tuple[str, str | None, dict[str, Any]]:
        it: dict[str, Any] = {"reason": why, "confidence": 0.5}
        if item.get("group_id"):
            it["group_id"] = item["group_id"]
        else:
            it["photo_id"] = item["photos"][0]["photo_id"]
            it["proposed"] = "review"
        return (f"Escalating {item['item_id']}: {why}", "request_human_approval", {"items": [it]})

    # -------------------------------------------------------------- shared per-photo analysis

    def analysis_step(self, item: dict[str, Any], pids: list[str],
                      first_roi: str) -> tuple[str, str, dict[str, Any]] | None:
        """P1/P2/P3 analysis. Returns the next call, or None when all evidence is in."""
        full = self.per_photo("score_sharpness", roi=(first_roi, "full"), variant=("processed", "processed"))
        if not all(p in full for p in pids):
            what = "group first, then score inside it" if item["kind"] == "group" else "singleton"
            return (f"{item['item_id']} ({what}, {len(pids)} frames): {item.get('why', '')}. Policy 1: score "
                    f"sharpness on the {first_roi} ROI and exposure.", "score_sharpness",
                    {"photo_ids": pids, "roi": first_roi})
        exp = self.per_photo("score_exposure")
        if not all(p in exp for p in pids):
            return ("Exposure next.", "score_exposure", {"photo_ids": pids})
        known = {p["photo_id"]: p.get("known_faces") for p in item["photos"]}
        faces = self.per_photo("detect_faces")
        need_faces = [p for p in pids if p not in faces and (item["kind"] == "group" and known.get(p) != 0
                      or full[p].get("label") != "sharp" and known.get(p))]
        if need_faces:
            soft = [p for p in need_faces if full[p].get("label") != "sharp"]
            why = (f"{', '.join(_short(p) for p in soft)} not sharp on the full frame and the manifest lists "
                   "faces; find them so the soft background is not mistaken for blur" if soft else
                   "groups need faces for the eye-state comparison")
            return (f"Detect faces: {why}.", "detect_faces", {"photo_ids": need_faces})
        # P2: borderline/blurry with faces -> faces ROI; still borderline + original -> original
        fr = self.per_photo("score_sharpness", roi=("faces", "full"), variant=("processed", "processed"))
        need_fr = [p for p in pids if p in faces and faces[p].get("n_faces") and p not in fr
                   and (full[p].get("label") != "sharp" or item["kind"] == "group")]
        if need_fr:
            soft = [p for p in need_fr if full[p].get("label") != "sharp"]
            if soft:
                p = soft[0]
                f = full[p]
                why = (f"Sharpness is {f.get('label')} on the full frame for {_short(p)} (lap_var "
                       f"{f.get('lap_var')}) and detect_faces found {faces[p]['n_faces']} face(s) (main face "
                       f"{faces[p].get('main_face_px')} px). Policy 2: re-score on the face ROI before labelling.")
            else:
                why = "Score the face ROI of every frame so the group is compared like for like."
            return (why, "score_sharpness", {"photo_ids": need_fr, "roi": "faces"})
        orig = self.per_photo("score_sharpness", variant=("original", "processed"))
        has_o = {p["photo_id"]: p.get("has_original") for p in item["photos"]}
        need_o = [p for p in pids if has_o.get(p) and p not in orig and (fr.get(p) or full[p]).get("label") ==
                  "borderline"]
        if need_o:
            p = need_o[0]
            roi = "faces" if p in fr else "full"
            return (f"{_short(p)} is still borderline on the {roi} ROI of the proxy; the original exists. Policy 2: "
                    "check the original before labelling.", "score_sharpness",
                    {"photo_ids": need_o, "roi": roi, "variant": "original"})
        if item["kind"] == "group":
            eyes = self.per_photo("score_eyes_open")
            need_e = [p for p in pids if faces.get(p, {}).get("n_faces") and p not in eyes]
            if need_e:
                return ("Faces found in the group; Policy 3: eye state per face, main subject first.",
                        "score_eyes_open", {"photo_ids": need_e})
            fq = self.per_photo("score_face_quality")
            need_q = [p for p in pids if faces.get(p, {}).get("n_faces") and p not in fq]
            if need_q:
                closed = [p for p in pids if (eyes.get(p) or {}).get("any_main_subject_closed")]
                lead = ""
                if closed:
                    p = closed[0]
                    e = eyes[p]
                    mf: dict[str, Any] = next((f for f in e.get("per_face", []) if f.get("is_main")), {})
                    lead = (f"Main subject {mf.get('face_id')} has eyes closed in {_short(p)} "
                            f"(open p {mf.get('left_open_p')}/{mf.get('right_open_p')}); group has {len(pids)} "
                            "frames. Policy 1/3: compare within the group rather than reject this frame outright. ")
                else:
                    group_eyes = [p for p in pids if any(f.get("state") == "closed" and not f.get("is_main")
                                                         for f in (eyes.get(p) or {}).get("per_face", []))]
                    if group_eyes:
                        lead = (f"Main subject open but a secondary face is closed in {_short(group_eyes[0])}; "
                                "compare within the group. ")
                return (lead + "Face quality next, then rank.", "score_face_quality", {"photo_ids": need_q})
        return None

    # -------------------------------------------------------------- groups

    def group_step(self, item: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
        gid = item["group_id"]
        pids = [p["photo_id"] for p in item["photos"]]
        nxt = self.analysis_step(item, pids, "full")
        if nxt is not None:
            return nxt
        best = self.last("pick_best_in_cluster")
        if best is None or best[2] != "success":
            return ("All frames scored. Rank the group.", "pick_best_in_cluster", {"group_id": gid})
        b = best[1]
        appr = self.last("request_human_approval")
        if appr is not None and appr[2] == "success":
            return self.write_human(item, appr[1])
        verdicts = self.group_verdicts(item, b)
        margin, tie = float(b["margin"]), bool(b["tie"])
        thr = float(self.pol["best_margin_review"])
        all_failed = all(v["verdict"] == "reject" for v in verdicts.values())
        keeper = next(p for p, v in verdicts.items() if v["verdict"] == "keep")
        top_defect = keeper != b["best_photo_id"]
        needs = tie or margin < thr or all_failed or top_defect
        w = self.last("write_decision")
        if needs:
            sheet = self.last("make_contact_sheet")
            if sheet is None:
                why = []
                if margin < thr or tie:
                    why.append(f"Margin {margin:.2f} < {thr} (Policy 5)")
                if top_defect:
                    why.append(f"top-scored {_short(b['best_photo_id'])} has a defect")
                return ("; ".join(why) + ". Make a contact sheet for the reviewer.", "make_contact_sheet",
                        {"group_id": gid})
            keep = [p for p, v in verdicts.items() if v["verdict"] == "keep"]
            rej = [p for p, v in verdicts.items() if v["verdict"] == "reject"]
            rev = [p for p, v in verdicts.items() if v["verdict"] == "review"]
            top = [r["photo_id"] for r in b["ranking"][:2]]
            reason = (f"Top two frames within {margin:.2f}" if margin < thr or tie else "Group needs a check")
            closed = [p for p, v in verdicts.items() if "EYES_CLOSED" in v["reason_codes"]]
            if closed:
                reason += f"; main subject's eyes closed in {', '.join(_short(c) for c in closed)}"
            return (f"Escalate {' vs '.join(_short(t) for t in top)} with the contact sheet; proposed keeper "
                    f"{_short(keeper)}.",
                    "request_human_approval",
                    {"items": [{"group_id": gid, "proposed": {"keep": keep, "reject": rej, "review": rev},
                                "reason": reason[:300], "confidence": 0.6,
                                "contact_sheet_key": sheet[1].get("key") if isinstance(sheet[1], dict) else None}]})
        if w is None:
            return (f"Clear winner {_short(keeper)} (margin {margin:.2f} >= {thr}); write the group's verdicts.",
                    "write_decision", {"decisions": [dict(photo_id=p, group_id=gid, decided_by="agent", **v)
                                                     for p, v in verdicts.items()]})
        return ("Group done.", "next_work_item", {})

    def group_verdicts(self, item: dict[str, Any], best: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Agent proposal from the recorded tool results, mirroring the shared worker policy."""
        from keepframe.worker import policies as wp

        full = self.per_photo("score_sharpness", roi=("full", "full"), variant=("processed", "processed"))
        fr = self.per_photo("score_sharpness", roi=("faces", "full"), variant=("processed", "processed"))
        orig = self.per_photo("score_sharpness", variant=("original", "processed"))
        exp = self.per_photo("score_exposure")
        faces = self.per_photo("detect_faces")
        eyes = self.per_photo("score_eyes_open")
        ev = {}
        for r in best["ranking"]:
            p = r["photo_id"]
            sh = orig.get(p) or fr.get(p) or full.get(p)
            ev[p] = wp.PhotoEvidence(photo_id=p, sharpness=sh, exposure=exp.get(p),
                                     faces=(faces.get(p) or {}).get("faces") or [],
                                     faces_known=p in faces, eyes=eyes.get(p), eyes_known=p in eyes)
        gd = wp.decide_group(item["group_id"], best, ev)
        return {d.photo_id: {"verdict": d.verdict, "reason_codes": d.reason_codes,
                             "confidence": round(d.confidence, 3)} for d in gd.decisions}

    def write_human(self, item: dict[str, Any], answer: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
        prev = self.last("write_decision")
        if prev is not None and prev[2] == "success":
            return ("Human verdicts written.", "next_work_item", {})
        decs = []
        pids = [p["photo_id"] for p in item["photos"]]
        gid = item.get("group_id")
        req = self.last("request_human_approval")
        req_items = (req[0].get("items") if req else None) or [{}]
        conf = float(req_items[0].get("confidence", 0.5))
        for d in answer.get("decisions", []):
            hv = d.get("human_verdict")
            if isinstance(hv, dict):
                for verdict, ids in hv.items():
                    for p in ids if isinstance(ids, list) else [ids]:
                        if p in pids:
                            codes = {"keep": ["KEEP_BEST_OF_GROUP"], "reject": ["BURST_NOT_BEST"],
                                     "review": ["REVIEW_LOW_CONFIDENCE"]}[verdict] if gid else \
                                ({"keep": ["KEEP_SINGLETON"]}.get(verdict, ["REVIEW_LOW_CONFIDENCE"]))
                            decs.append({"photo_id": p, "verdict": verdict, "reason_codes": codes,
                                         "confidence": conf, "group_id": gid, "decided_by": "human"})
            elif isinstance(hv, str):
                p = d.get("photo_id") or pids[0]
                decs.append({"photo_id": p, "verdict": hv, "reason_codes":
                             ["KEEP_SINGLETON"] if hv == "keep" else ["REVIEW_LOW_CONFIDENCE"],
                             "confidence": conf, "group_id": gid, "decided_by": "human"})
        note = next((d.get("note") for d in answer.get("decisions", []) if d.get("note")), "")
        return (f"Approval answered{': ' + note if note else ''}. Write the answered verdicts as decided_by=human.",
                "write_decision", {"decisions": decs})

    # -------------------------------------------------------------- singletons

    def singleton_step(self, item: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
        from keepframe.worker import policies as wp

        pid = item["photos"][0]["photo_id"]
        nxt = self.analysis_step(item, [pid], "saliency")
        if nxt is not None:
            return nxt
        appr = self.last("request_human_approval")
        if appr is not None and appr[2] == "success":
            return self.write_human(item, appr[1])
        w = self.last("write_decision")
        if w is not None and w[2] == "success":
            return ("Done with this photo.", "next_work_item", {})
        sal = self.per_photo("score_sharpness", roi=("saliency", "full"), variant=("processed", "processed"))
        fr = self.per_photo("score_sharpness", roi=("faces", "full"), variant=("processed", "processed"))
        orig = self.per_photo("score_sharpness", variant=("original", "processed"))
        sh = orig.get(pid) or fr.get(pid) or sal.get(pid)
        exp = self.per_photo("score_exposure").get(pid)
        faces = self.per_photo("detect_faces").get(pid) or {}
        ex = item["photos"][0].get("exif") or {}
        d = wp.decide_singleton(wp.PhotoEvidence(photo_id=pid, sharpness=sh, exposure=exp,
                                                 faces=faces.get("faces") or [], faces_known=bool(faces),
                                                 mfaces=item["photos"][0].get("known_faces"),
                                                 exposure_time_s=ex.get("exposure_time_s"), hour=ex.get("hour")))
        dec = {"photo_id": pid, "verdict": d.verdict, "reason_codes": d.reason_codes,
               "confidence": round(d.confidence, 3), "decided_by": "agent"}
        if d.verdict == "reject" and d.confidence < float(self.pol["reject_conf_min"]):
            return (f"Reject at confidence {d.confidence:.2f} < {self.pol['reject_conf_min']} (Policy 5): ask.",
                    "request_human_approval",
                    {"items": [{"photo_id": pid, "proposed": "reject", "reason": d.reason_text,
                                "confidence": d.confidence}]})
        return (f"{_short(pid)}: {d.reason_text}", "write_decision", {"decisions": [dec]})


def _short(pid: str) -> str:
    return pid[:8]


FAKE_TOKENS_IN_PER_MESSAGE = 100
FAKE_TOKENS_OUT_PER_TURN = 10


class FakeModel(Model):
    """Strands Model that streams the Planner's choice as Bedrock-shaped stream events."""

    def __init__(self, model_id: str = "fake-planner-v1") -> None:
        self.config = {"model_id": model_id}

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> Any:
        return self.config

    def structured_output(self, output_model: Any, prompt: Any, system_prompt: str | None = None,
                          **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError("FakeModel has no structured output")

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        system_prompt_content: list[SystemContentBlock] | None = None,
        invocation_state: dict[str, Any] | None = None,
        cancel_signal: threading.Event | None = None,
        agent_metadata: AgentMetadata | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        text, name, inp = Planner(_results(messages)).step()
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": text}}}
        yield {"contentBlockStop": {}}
        if name is not None:
            tid = f"tooluse_fake_{_PREFIX}_{next(_ids):04d}"
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tid, "name": name}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(inp)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"messageStop": {"stopReason": "end_turn"}}
        # deterministic usage so token accounting is testable: 100 input tokens per message, 10 output per turn
        tin, tout = FAKE_TOKENS_IN_PER_MESSAGE * len(messages), FAKE_TOKENS_OUT_PER_TURN
        yield {"metadata": {"usage": {"inputTokens": tin, "outputTokens": tout, "totalTokens": tin + tout},
                            "metrics": {"latencyMs": 0}}}

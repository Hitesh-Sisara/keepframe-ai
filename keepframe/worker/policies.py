"""Decision policies (docs/REPORT.md §4) as pure functions. No I/O, no OpenCV, no model calls.

Shared by the batch worker (keepframe.worker.run_event) and the agent (keepframe.agent hooks), so the
LLM path and the deterministic path apply identical rules. Inputs are the plain tool-result dicts.
Every threshold comes from config/thresholds.yaml (pass `cfg=` to override in tests).

Policy map
  P1 group first          -> tools_for(); the worker/agent decide which tools run per photo
  P2 borderline sharpness -> next_sharpness_step()
  P3 eyes per-face        -> should_run_eyes(), eyes_defect()
  P4 never reject a group -> decide_group() keeps the best and flags all_failed for approval
  P5 low confidence       -> gate_confidence(), group margin/tie -> needs_approval
  P6 artistic intent      -> artistic_intent()
  P7 budget               -> optional_tools_allowed()
  P8 never delete         -> Verdict is keep|reject|review only
  P9 explain              -> reason_text() (template, <= 140 chars)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from keepframe.config import section
from keepframe.schemas import ReasonCode as RC
from keepframe.schemas import duplicate_of

SEVERITY = {"keep": 0, "review": 1, "reject": 2}


def _cfg(name: str, cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(cfg) if cfg is not None else section(name)


@dataclass
class Defect:
    code: str
    verdict: str  # "reject" | "review"
    confidence: float
    detail: str = ""


@dataclass
class PhotoDecision:
    photo_id: str
    verdict: str
    reason_codes: list[str]
    confidence: float
    reason_text: str = ""
    group_id: str | None = None
    needs_approval: bool = False
    defects: list[Defect] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "photo_id": self.photo_id,
            "verdict": self.verdict,
            "reason_codes": list(self.reason_codes),
            "confidence": round(float(self.confidence), 4),
            "reason_text": self.reason_text,
            "group_id": self.group_id,
            "needs_approval": self.needs_approval,
        }


# --------------------------------------------------------------------------- P1 / P7


def tools_for(group_size: int, budget_remaining_frac: float = 1.0, cfg: Mapping[str, Any] | None = None) -> list[str]:
    """P1: singletons -> saliency sharpness + exposure; groups -> faces/eyes/face_quality then best."""
    if group_size <= 1:
        tools = ["score_sharpness:saliency", "score_exposure"]
    else:
        tools = ["score_sharpness:full", "score_exposure", "detect_faces", "score_eyes_open", "score_face_quality",
                 "pick_best_in_cluster"]
    if optional_tools_allowed(budget_remaining_frac, cfg):
        tools += ["score_composition"]
    return tools


def optional_tools_allowed(budget_remaining_frac: float, cfg: Mapping[str, Any] | None = None) -> bool:
    """P7: stop optional tools (composition, enhance) under 20% budget remaining."""
    return budget_remaining_frac >= float(_cfg("policy", cfg)["budget_optional_min_frac"])


# --------------------------------------------------------------------------- P2


def next_sharpness_step(last: Mapping[str, Any], n_faces: int, has_original: bool,
                        tried: Sequence[str] = ()) -> dict[str, str] | None:
    """P2: what to call after a sharpness result. Returns {"roi", "variant"} or None (label is final).

    not sharp + n_faces >= 1 + faces not tried    -> roi=faces, variant=processed
        (the plan triggers on borderline; we also re-score *blurry* frames that have faces, so a
        bokeh portrait whose soft background drags the full-frame lap_var under 60 is not rejected)
    borderline + original exists, not tried       -> variant=original (faces ROI if faces exist)
    `tried` holds "roi:variant" strings already run.
    """
    label = last.get("label")
    if label == "sharp":
        return None
    tried_set = set(tried)
    if n_faces >= 1 and "faces:processed" not in tried_set:
        return {"roi": "faces", "variant": "processed"}
    if label != "borderline":
        return None
    roi = "faces" if n_faces >= 1 else "full"
    if has_original and f"{roi}:original" not in tried_set and not last.get("variant_fallback"):
        return {"roi": roi, "variant": "original"}
    return None


def final_sharpness(chain: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The result that labels the photo: the last step of the P2 escalation chain."""
    return chain[-1] if chain else None


# --------------------------------------------------------------------------- faces / P3


def face_min_px(face: Mapping[str, Any]) -> float:
    if face.get("min_px") is not None:
        return float(face["min_px"])
    if face.get("minPx") is not None:
        return float(face["minPx"])
    b = face.get("bbox_px") or [0, 0, 0, 0]
    return float(min(b[2], b[3]))


def _main_min_px(cfg: Mapping[str, Any] | None) -> float:
    if cfg is not None and "main_subject_min_px" in cfg:
        return float(cfg["main_subject_min_px"])
    return float(section("faces")["main_subject_min_px"])


def main_subject(faces: Sequence[Mapping[str, Any]], cfg: Mapping[str, Any] | None = None) -> Mapping[str, Any] | None:
    """Largest face with min side >= faces.main_subject_min_px (ties -> lowest face_id)."""
    min_px = _main_min_px(cfg)
    cands = [f for f in faces if face_min_px(f) >= min_px]
    if not cands:
        return None
    return sorted(cands, key=lambda f: (-face_min_px(f), str(f.get("face_id"))))[0]


def should_run_eyes(mean_L: float | None, faces: Sequence[Mapping[str, Any]], cfg: Mapping[str, Any] | None = None) -> bool:
    """P3: skip eye-state when the scene is dark AND faces are small; also when no usable face exists."""
    c = _cfg("eyes", cfg)
    if not faces:
        return False
    biggest = max(face_min_px(f) for f in faces)
    if biggest < _main_min_px(cfg):
        return False
    dark = mean_L is not None and mean_L < float(c["skip_dark_mean_l"])
    return not (dark and biggest < float(c["skip_small_face_px"]))


def _p_closed(f: Mapping[str, Any]) -> float:
    """1 - mean(open p) of both eyes (same rule as eyes.score_eyes_open's any_main_subject_closed)."""
    ps = [p for p in (f.get("left_open_p"), f.get("right_open_p")) if p is not None]
    return 1.0 - sum(ps) / len(ps) if ps else 0.0


def eyes_defect(eyes: Mapping[str, Any] | None, faces: Sequence[Mapping[str, Any]],
                cfg: Mapping[str, Any] | None = None) -> Defect | None:
    """P3: EYES_CLOSED (reject) only for the main subject closed with p >= 0.8 (lower p -> gated to review).

    Group photo (n_faces >= group_photo_min_faces): REVIEW_POLICY_GROUP_EYES (review, never reject) when at least
    one non-main face is closed AND (closed >= group_eyes_min_closed OR closed / detected non-main faces >=
    1 / group_eyes_share_den, compared in integers). Unknown eye states never count as closed.
    C08: the old rule fired only for exactly one closed non-main face."""
    if not eyes or not eyes.get("per_face"):
        return None
    c = _cfg("eyes", cfg)
    main = main_subject(faces, c)
    main_id = (eyes.get("main_subject_face_id") or (main or {}).get("face_id"))
    per = {str(f["face_id"]): f for f in eyes["per_face"]}
    if main_id is not None and str(main_id) in per:
        mf = per[str(main_id)]
        if mf.get("state") == "closed":
            p = _p_closed(mf)
            # p >= reject_closed_p -> reject; weaker evidence goes through the confidence gate (-> review)
            conf = p if p >= float(c["reject_closed_p"]) else min(p, float(c["reject_closed_p"]) - 1e-3)
            return Defect(RC.EYES_CLOSED.value, "reject", round(conf, 4), f"main face {main_id} p_closed={p:.2f}")
    closed_others = [fid for fid, f in per.items() if fid != str(main_id) and f.get("state") == "closed"]
    n_others = max(len(faces) - (1 if main_id is not None else 0), len(closed_others))
    if len(faces) >= int(c["group_photo_min_faces"]) and closed_others and (
            len(closed_others) >= int(c.get("group_eyes_min_closed", 2))
            or len(closed_others) * int(c.get("group_eyes_share_den", 3)) >= max(n_others, 1)):  # share >= 1/den
        who = (f"face {closed_others[0]} closed" if len(closed_others) == 1
               else f"{len(closed_others)} of {n_others} other faces closed")
        return Defect(RC.REVIEW_POLICY_GROUP_EYES.value, "review", 0.6, who)
    return None


def faces_unreadable(faces: Sequence[Mapping[str, Any]], eyes: Mapping[str, Any] | None) -> bool:
    """Faces exist but none could be read (all eye states unknown / eye-state skipped)."""
    if not faces:
        return False
    if not eyes or not eyes.get("per_face"):
        return True
    return all(f.get("state") == "unknown" for f in eyes["per_face"])


# --------------------------------------------------------------------------- P6


def local_hour(ts: float | None, exif_local_hour: int | None, cfg: Mapping[str, Any] | None = None) -> int | None:
    if exif_local_hour is not None:
        return int(exif_local_hour)
    if ts is None:
        return None
    off = int(_cfg("policy", cfg)["default_tz_offset_min"])
    return datetime.fromtimestamp(ts, tz=UTC).astimezone(timezone(timedelta(minutes=off))).hour


def is_reception_hour(hour: int | None, cfg: Mapping[str, Any] | None = None) -> bool:
    if hour is None:
        return False
    c = _cfg("policy", cfg)
    start, end = int(c["reception_hour_start"]), int(c["reception_hour_end"])
    return hour >= start or hour < end if start > end else start <= hour < end


def artistic_intent(sharp: Mapping[str, Any], exposure: Mapping[str, Any] | None, exposure_time_s: float | None,
                    hour: int | None, cfg: Mapping[str, Any] | None = None) -> bool:
    """P6: motion blur + (ExposureTime >= 1/30 s OR (clip_lo >= 30% and dance-floor/reception hours))."""
    c = _cfg("policy", cfg)
    if sharp.get("blur_kind") != "motion":
        return False
    if exposure_time_s is not None and exposure_time_s >= float(c["artistic_exposure_time_s"]):
        return True
    clip_lo = (exposure or {}).get("clip_lo_pct")
    return clip_lo is not None and clip_lo >= float(c["artistic_clip_lo_pct"]) and is_reception_hour(hour, c)


# --------------------------------------------------------------------------- per-photo defects


@dataclass
class PhotoEvidence:
    """Everything the policies need about one photo (plain values; built by worker or agent)."""

    photo_id: str
    sharpness: Mapping[str, Any] | None = None          # final result after the P2 chain
    exposure: Mapping[str, Any] | None = None
    faces: Sequence[Mapping[str, Any]] = ()
    faces_known: bool = False                           # False -> face tools unavailable
    mfaces: int | None = None                           # known face count from metadata (PhotoRecord.known_face_count)
    eyes: Mapping[str, Any] | None = None
    eyes_known: bool = False                            # eye-state tool ran or was skipped by P3 (not missing)
    exposure_time_s: float | None = None
    hour: int | None = None


def photo_defects(ev: PhotoEvidence, cfg: Mapping[str, Any] | None = None) -> list[Defect]:
    """Collect defects with their proposed verdict (before the confidence gate)."""
    pol = _cfg("policy", None if cfg is None else cfg.get("policy"))
    eyes_cfg = None if cfg is None else cfg.get("eyes")
    out: list[Defect] = []
    s = ev.sharpness or {}
    e = ev.exposure or {}
    no_subject = bool(e.get("no_subject")) and not ev.faces and (ev.mfaces or 0) == 0
    if no_subject:
        # near-uniform frame (lens cap, wall): sharpness is meaningless, report the real cause
        out.append(Defect(RC.NO_SUBJECT.value, "reject", float(pol["no_subject_conf"]), "near-uniform frame"))
    elif s.get("label") == "blurry":
        code = RC.BLUR_MOTION.value if s.get("blur_kind") == "motion" else RC.BLUR_FOCUS.value
        conf = float(s.get("confidence", 0.5))
        if code == RC.BLUR_MOTION.value and artistic_intent(s, ev.exposure, ev.exposure_time_s, ev.hour, pol):
            out.append(Defect(RC.REVIEW_ARTISTIC_INTENT.value, "review", conf, "motion blur, likely intentional"))
            out.append(Defect(code, "review", conf))
        else:
            out.append(Defect(code, "reject", conf, f"lap_var={s.get('lap_var')}"))
    lab = e.get("label")
    verdicts = pol["exposure_verdicts"]
    if lab == "over" and e.get("over_flag", True):  # soft label alone is not a defect (see over_flag)
        out.append(Defect(RC.EXPOSURE_OVER.value, verdicts["over"], float(e.get("confidence", 0.5))))
    elif lab == "under" and e.get("under_flag", True):
        # "under" alone is a soft label; only a dark, flat subject (under_flag) is a defect.
        # Results without the field (older runs / external callers) keep the plain rule.
        v = verdicts["under"]
        if ev.eyes_known and faces_unreadable(ev.faces, ev.eyes):
            v = "reject"  # dark AND faces unreadable (still subject to the confidence gate)
        out.append(Defect(RC.EXPOSURE_UNDER.value, v, float(e.get("confidence", 0.5))))
    elif lab == "low_contrast":
        out.append(Defect(RC.LOW_CONTRAST.value, verdicts["low_contrast"], float(e.get("confidence", 0.5))))
    d = eyes_defect(ev.eyes, ev.faces, eyes_cfg)
    if d:
        out.append(d)
    return out


def gate_confidence(verdict: str, confidence: float, cfg: Mapping[str, Any] | None = None) -> tuple[str, bool]:
    """P5: a reject below policy.reject_conf_min becomes review. Returns (verdict, downgraded)."""
    if verdict == "reject" and confidence < float(_cfg("policy", cfg)["reject_conf_min"]):
        return "review", True
    return verdict, False


def _combine(defects: Sequence[Defect], pol: Mapping[str, Any]) -> tuple[str, list[str], float, bool]:
    """Most severe gated verdict wins. Returns (verdict, codes, confidence, downgraded)."""
    gated = []
    downgraded_any = False
    for d in defects:
        v, dg = gate_confidence(d.verdict, d.confidence, pol)
        downgraded_any |= dg
        gated.append((v, d))
    rejects = [d for v, d in gated if v == "reject"]
    if rejects:
        rejects.sort(key=lambda d: -d.confidence)
        others = [d.code for v, d in gated if v != "reject" and d.code not in {r.code for r in rejects}]
        return "reject", [d.code for d in rejects] + others, max(d.confidence for d in rejects), False
    reviews = [d for v, d in gated]
    if reviews:
        codes = []
        for d in reviews:
            if d.code not in codes:
                codes.append(d.code)
        if downgraded_any and RC.REVIEW_LOW_CONFIDENCE.value not in codes:
            codes.append(RC.REVIEW_LOW_CONFIDENCE.value)
        return "review", codes, min(d.confidence for d in reviews), downgraded_any
    return "keep", [], 1.0, False


# --------------------------------------------------------------------------- P9 reason text

_CODE_TEXT = {
    RC.BLUR_MOTION.value: "motion blur",
    RC.BLUR_FOCUS.value: "out of focus",
    RC.EYES_CLOSED.value: "main subject's eyes closed",
    RC.EXPOSURE_UNDER.value: "underexposed",
    RC.EXPOSURE_OVER.value: "overexposed",
    RC.LOW_CONTRAST.value: "low contrast",
    RC.BURST_NOT_BEST.value: "weaker frame of its burst",
    RC.NO_SUBJECT.value: "no subject",
    RC.KEEP_BEST_OF_GROUP.value: "best frame of its group",
    RC.KEEP_SINGLETON.value: "unique frame, passes checks",
    RC.REVIEW_LOW_CONFIDENCE.value: "low confidence",
    RC.REVIEW_POLICY_GROUP_EYES.value: "people in group have eyes closed",
    RC.REVIEW_ARTISTIC_INTENT.value: "blur may be intentional",
}


def reason_text(verdict: str, codes: Sequence[str], scores: Mapping[str, Any] | None = None,
                group_size: int | None = None, cfg: Mapping[str, Any] | None = None) -> str:
    """Deterministic <= 140-char explanation built from the codes and the tool numbers."""
    limit = int(_cfg("policy", cfg)["reason_text_max"])
    parts = []
    for c in codes:
        base, _, param = c.partition(":")
        parts.append(f"near-identical to {param}" if base == RC.DUPLICATE_OF.value else _CODE_TEXT.get(base, base))
    s = scores or {}
    nums = []
    sh = s.get("sharpness") or {}
    if sh.get("lap_var") is not None:
        nums.append(f"sharpness {float(sh['lap_var']):.0f}" + (f" ({sh['roi_used']})" if sh.get("roi_used") not in
                                                                (None, "full") else ""))
    ex = s.get("exposure") or {}
    if ex.get("mean_L") is not None and any(c in codes for c in (RC.EXPOSURE_UNDER.value, RC.EXPOSURE_OVER.value,
                                                                 RC.LOW_CONTRAST.value)):
        nums.append(f"L {float(ex['mean_L']):.0f}")
    if s.get("margin") is not None and group_size:
        nums.append(f"margin {float(s['margin']):.2f} of {group_size}")
    text = verdict.capitalize() + ": " + "; ".join(parts)
    if nums:
        text += " [" + ", ".join(nums) + "]"
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


# --------------------------------------------------------------------------- singletons


def decide_singleton(ev: PhotoEvidence, cfg: Mapping[str, Any] | None = None) -> PhotoDecision:
    pol = _cfg("policy", None if cfg is None else cfg.get("policy"))
    defects = photo_defects(ev, cfg)
    verdict, codes, conf, _ = _combine(defects, pol)
    if verdict == "keep":
        codes = [RC.KEEP_SINGLETON.value]
        s = ev.sharpness or {}
        conf = float(s.get("confidence", 0.7)) if s.get("label") == "sharp" else 0.6
        sh_cfg = section("sharpness") if cfg is None else cfg.get("sharpness", section("sharpness"))
        if s.get("label") == "borderline" and sh_cfg.get("borderline_final") == "review":
            verdict, codes = "review", [RC.REVIEW_LOW_CONFIDENCE.value]
    d = PhotoDecision(ev.photo_id, verdict, codes, conf, defects=defects, needs_approval=verdict == "review")
    d.reason_text = reason_text(verdict, codes, {"sharpness": ev.sharpness, "exposure": ev.exposure}, cfg=pol)
    return d


# --------------------------------------------------------------------------- groups (P4, P5)


@dataclass
class GroupDecision:
    group_id: str
    keeper_photo_id: str
    best_photo_id: str
    margin: float
    tie: bool
    needs_approval: bool          # at most ONE human question per group (P5 applied per group)
    all_failed: bool
    approval_reasons: list[str]
    decisions: list[PhotoDecision]
    tied_candidates: list[str] = field(default_factory=list)  # keeper + rivals for the KEEP slot (the question)


def _eye_signature(ev: PhotoEvidence) -> tuple[str, ...]:
    """Per-face eye states in face order ('' when eye-state did not run): near-identical frames share it."""
    per = (ev.eyes or {}).get("per_face") or []
    return tuple(str(f.get("state", "")) for f in per)


def _main_eyes_open(ev: PhotoEvidence) -> bool | None:
    per = {str(f.get("face_id")): f for f in (ev.eyes or {}).get("per_face") or []}
    main = main_subject(ev.faces) if ev.faces else None
    mid = (ev.eyes or {}).get("main_subject_face_id") or (main or {}).get("face_id")
    f = per.get(str(mid)) if mid is not None else None
    if not f or f.get("state") in (None, "unknown"):
        return None
    return bool(f.get("state") == "open")


def distinct_merit(runner: PhotoEvidence, keeper: PhotoEvidence) -> str | None:
    """Why a runner-up might deserve a human look although the keeper is decided, else None:
    its main subject has eyes open where the keeper's are closed, or it shows more faces than the keeper."""
    r_open, k_open = _main_eyes_open(runner), _main_eyes_open(keeper)
    if r_open is True and k_open is False:
        return "main subject eyes open here, closed in the keeper"
    if runner.faces_known and keeper.faces_known:
        min_px = _main_min_px(None)
        big_r = sum(1 for f in runner.faces if face_min_px(f) >= min_px)
        big_k = sum(1 for f in keeper.faces if face_min_px(f) >= min_px)
        if big_r > big_k:  # only faces large enough to be a subject count (hands / background people do not)
            return f"{big_r} subject faces vs {big_k} in the keeper"
    return None


MD5_DUPLICATE_CONF = 1.0  # same bytes: no judgement involved


def md5_representatives(ordered: Sequence[str], keeper: str, duplicates: Mapping[str, str]) -> dict[str, str]:
    """{copy: representative} for every member that is a byte-identical copy of another member.

    `duplicates` maps a copy to another file with the same md5 (cluster.exact_duplicates: the class's first file);
    two members are copies when their chains end at the same root. Per md5 class the representative is the keeper
    if the keeper is in the class, else the first member in `ordered` (ranking order). Representatives themselves
    are not in the result. Deterministic for a given ranking."""
    def root(pid: str) -> str:
        seen = {pid}
        while (nxt := duplicates.get(pid)) is not None and nxt not in seen:
            seen.add(nxt)
            pid = nxt
        return pid

    classes: dict[str, list[str]] = {}
    for pid in ordered:
        classes.setdefault(root(pid), []).append(pid)
    out: dict[str, str] = {}
    for members in classes.values():
        if len(members) < 2:
            continue
        repr_ = keeper if keeper in members else members[0]
        out.update({m: repr_ for m in members if m != repr_})
    return out


def decide_group(
    group_id: str,
    best: Mapping[str, Any],
    evidence: Mapping[str, PhotoEvidence],
    duplicates: Mapping[str, str] | None = None,
    phash_to: Mapping[tuple[str, str], int] | None = None,
    cfg: Mapping[str, Any] | None = None,
) -> GroupDecision:
    """Decide every member of a group (docs/REPORT.md §4 P4/P5, applied per group, not per frame).

    best        pick_best_in_cluster result (ranking sorted best-first)
    duplicates  md5 twins {photo_id: twin_id}
    phash_to    pHash Hamming {(a, b): d} for member pairs (either order)

    * Keeper: the highest-ranked member with no gated reject defect. If every member fails, the top one is
      still kept (P4: never reject a whole group) and the group gets its one question.
    * Tied candidates: members within policy.best_margin_review of the keeper that are real alternatives
      (not duplicates or near-identical copies of the keeper, no reject defect), at most
      policy.max_tied_candidates - 1 rivals. If any exist, the group asks ONE question about the KEEP slot.
      Only the strongest alternative is `review` (REVIEW_LOW_CONFIDENCE); other candidates are rejected but listed
      in tied_candidates, and the keeper stays `keep` pending the answer. So review <= 1 per group.
    * Byte-identical copies (same md5, `duplicates` maps each copy to the first file of its md5 class) are collapsed
      first: one representative per class (the keeper if it is in the class, else the class's highest-ranked
      member); every other copy is rejected as DUPLICATE_OF:<representative> with confidence 1.0, never asked
      about and never a tied candidate (C09/C22).
    * Everyone else is rejected: DUPLICATE_OF:<keeper> (pHash <= dup_phash_max with an
      equal score), BURST_NOT_BEST at burst_conf_near when near-identical to the keeper (pHash <=
      dup_phash_max, or gap <= near_gap with the same eye states), otherwise BURST_NOT_BEST with
      confidence base + slope*gap, floored at reject_conf_min. Ranking within a burst is relative, so the
      per-frame P5 gate does not turn them into questions.
    * A runner-up with a distinct merit the keeper lacks (eyes open vs closed, more subject-sized faces) joins
      the same single group question (it is the asked alternative when there is no score rival).
    """
    pol = _cfg("policy", None if cfg is None else cfg.get("policy"))
    hcfg = _cfg("hashes", None if cfg is None else cfg.get("hashes"))
    ranking = list(best["ranking"])
    score = {r["photo_id"]: float(r["score"]) for r in ranking}
    defects = {pid: photo_defects(evidence[pid], cfg) for pid in score}
    gated = {pid: _combine(ds, pol) for pid, ds in defects.items()}

    clean = [r["photo_id"] for r in ranking if gated[r["photo_id"]][0] != "reject"]
    all_failed = not clean
    keeper = clean[0] if clean else ranking[0]["photo_id"]
    pairs = phash_to or {}
    dup_max = int(hcfg["dup_phash_max"])
    dup_gap = float(hcfg.get("dup_score_gap", 0.05))
    near_gap = float(pol.get("burst_near_gap", 0.01))
    md5_copy_of = md5_representatives([r["photo_id"] for r in ranking], keeper, duplicates or {})
    kev = evidence[keeper]

    def dist(pid: str) -> int | None:
        return pairs.get((pid, keeper), pairs.get((keeper, pid)))

    def is_dup_of_keeper(pid: str) -> bool:
        """md5 copy of the keeper, or pHash-identical AND of equal quality (pHash is blind to blur and eyes)."""
        if md5_copy_of.get(pid) == keeper:
            return True
        d = dist(pid)
        return d is not None and d <= dup_max and abs(score[keeper] - score[pid]) < dup_gap

    def near_identical(pid: str) -> bool:
        d = dist(pid)
        same_eyes = _eye_signature(evidence[pid]) == _eye_signature(kev)
        return (d is not None and d <= dup_max) or (abs(score[keeper] - score[pid]) <= near_gap and same_eyes)

    sk = score[keeper]
    review_margin = float(pol["best_margin_review"])
    max_rivals = max(0, int(pol.get("max_tied_candidates", 3)) - 1)
    rivals = [r["photo_id"] for r in ranking
              if r["photo_id"] != keeper and r["photo_id"] not in md5_copy_of and gated[r["photo_id"]][0] != "reject"
              and sk - score[r["photo_id"]] < review_margin
              and not is_dup_of_keeper(r["photo_id"]) and not near_identical(r["photo_id"])][:max_rivals]
    merits = {pid: m for r in ranking if (pid := r["photo_id"]) != keeper and pid not in rivals
              and pid not in md5_copy_of and not is_dup_of_keeper(pid) and gated[pid][0] != "reject"
              and (m := distinct_merit(evidence[pid], kev))}
    alternatives = [r["photo_id"] for r in ranking if r["photo_id"] != keeper and r["photo_id"] not in md5_copy_of
                    and not is_dup_of_keeper(r["photo_id"])]
    runner_up = alternatives[0] if alternatives else None
    margin = (sk - score[runner_up]) if runner_up else 1.0
    tie_margin = float(_cfg("best", None if cfg is None else cfg.get("best"))["tie_margin"])
    tie = bool(rivals) and margin < tie_margin

    reasons: list[str] = []
    if all_failed:
        reasons.append("every frame failed")
    if rivals:
        reasons.append(f"{len(rivals) + 1} distinct frames within {review_margin:.2f} for the keep slot")
    if keeper != best["best_photo_id"]:
        reasons.append(f"top-scored {best['best_photo_id']} has a defect")
    if merits:
        reasons.append(f"{len(merits)} frame(s) with a merit the keeper lacks")
    needs_approval = bool(reasons)
    # The ONE question: keeper vs the strongest alternative (a rival, else a merit frame). Every other candidate is
    # listed in tied_candidates (visible on the contact sheet) but rejected, so review <= 1 per group.
    ordered = [r["photo_id"] for r in ranking]
    alt_pool = rivals + [pid for pid in ordered if pid in merits and pid not in rivals]
    asked = alt_pool[0] if alt_pool else None
    tied = ([keeper, *alt_pool][: max_rivals + 1]
            if (alt_pool or all_failed or keeper != best["best_photo_id"]) else [])

    out: list[PhotoDecision] = []
    burst_floor = float(pol["reject_conf_min"])
    for r in ranking:
        pid = r["photo_id"]
        ev = evidence[pid]
        scores = {"sharpness": ev.sharpness, "exposure": ev.exposure, "margin": margin}
        flagged = needs_approval and pid in tied
        if pid == keeper:
            codes = [RC.KEEP_BEST_OF_GROUP.value]
            conf = 0.5 if all_failed else min(0.99, 0.6 + 4.0 * max(margin, 0.0))
            d = PhotoDecision(pid, "keep", codes, conf, group_id=group_id, needs_approval=flagged,
                              defects=defects[pid])
        elif pid in md5_copy_of:  # byte-identical copy: certain, and never a question (C09/C22)
            d = PhotoDecision(pid, "reject", [duplicate_of(md5_copy_of[pid])], MD5_DUPLICATE_CONF, group_id=group_id,
                              needs_approval=False, defects=defects[pid])
        elif pid == asked:
            codes = [RC.BURST_NOT_BEST.value, RC.REVIEW_LOW_CONFIDENCE.value]
            conf = max(0.0, min(0.79, 0.5 + 4.0 * (sk - score[pid])))
            d = PhotoDecision(pid, "review", codes, conf, group_id=group_id, needs_approval=True,
                              defects=defects[pid])
        else:
            own_v, own_codes, own_conf, _ = gated[pid]
            own = [c for c in own_codes if c != RC.REVIEW_LOW_CONFIDENCE.value]
            if is_dup_of_keeper(pid):
                codes = [duplicate_of(keeper), *own]
                conf = float(pol["duplicate_conf"])
            else:
                codes = [RC.BURST_NOT_BEST.value, *own]
                if near_identical(pid):
                    conf = float(pol.get("burst_conf_near", 0.95))
                else:
                    gap = max(0.0, sk - score[pid])
                    conf = min(float(pol["burst_conf_max"]),
                               max(burst_floor, float(pol["burst_conf_base"]) + float(pol["burst_conf_slope"]) * gap))
                if own_v == "reject":
                    conf = max(conf, own_conf)
            d = PhotoDecision(pid, "reject", codes, conf, group_id=group_id, needs_approval=flagged,
                              defects=defects[pid])
        d.reason_text = reason_text(d.verdict, d.reason_codes, scores, len(ranking), pol)
        out.append(d)
    return GroupDecision(group_id, keeper, str(best["best_photo_id"]), margin, tie, needs_approval, all_failed,
                         reasons, out, tied)

"""pick_best_in_cluster tool (docs/REPORT.md §3). Deterministic: same inputs -> same output, no model call.

score = 0.35*sharp_faces_norm + 0.25*eyes_open_mean + 0.15*exposure_ok + 0.15*fiqa_mean + 0.10*composition

  sharp_faces_norm  sharpness (face-ROI lap_var if faces were scored for every member, else the most specific
                    ROI common to all) min-max normalised within the group (on log1p; lap_var spans orders of
                    magnitude). Extension to the plan: the spread is scaled by
                    min(1, ln(max/min) / ln(sharpness_full_ratio)), so frames of nearly equal sharpness all
                    score near 1 and eyes/face quality decide. Equal sharpness -> 1.0 for all.
  eyes_open_mean    mean over faces of mean(left_open_p, right_open_p); 0.5 (unknown) if no eye data.
  exposure_ok       ok=1, low_contrast=0.5, under/over=0 (thresholds.yaml best.exposure_ok_values).
  fiqa_mean         mean eDifFIQA over faces, clipped to [0,1]; 0.5 if not computed.
  composition       0.5 if not computed (plan), else subject_centrality / nima_mean/10.

Ordering ties are broken by photo_id so the ranking is total and reproducible.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from keepframe.config import section
from keepframe.schemas import BestResult, RankEntry

SHARP_KEYS = ("sharpness_faces_original", "sharpness_faces_processed", "sharpness_full_original",
              "sharpness_full_processed", "sharpness_saliency_processed", "sharpness")


def _sharp_value(r: Mapping[str, Any]) -> float | None:
    # Use a consistent ROI across the group: callers pass the same keys for every member.
    for k in SHARP_KEYS:
        v = r.get(k)
        if isinstance(v, Mapping) and v.get("lap_var") is not None:
            return float(v["lap_var"])
    return None


def _sharp_key_common(results: Sequence[Mapping[str, Any]]) -> str | None:
    """Most specific sharpness key present for EVERY member (so we compare like with like)."""
    for k in SHARP_KEYS:
        if results and all(isinstance(r.get(k), Mapping) and r[k].get("lap_var") is not None for r in results):
            return k
    return None


def _eyes(r: Mapping[str, Any], unknown: float) -> float:
    eyes = r.get("eyes") or {}
    vals = []
    for f in eyes.get("per_face") or []:
        ps = [p for p in (f.get("left_open_p"), f.get("right_open_p")) if p is not None]
        if ps and f.get("state") != "unknown":
            vals.append(sum(ps) / len(ps))
    return sum(vals) / len(vals) if vals else unknown


def _fiqa(r: Mapping[str, Any], unknown: float) -> float:
    fq = r.get("face_quality") or {}
    vals = [float(f["fiqa"]) for f in fq.get("per_face") or [] if f.get("fiqa") is not None]
    return min(1.0, max(0.0, sum(vals) / len(vals))) if vals else unknown


def _composition(r: Mapping[str, Any], unknown: float) -> float:
    c = r.get("composition") or {}
    if c.get("subject_centrality") is not None:
        return min(1.0, max(0.0, float(c["subject_centrality"])))
    if c.get("nima_mean") is not None:
        return min(1.0, max(0.0, float(c["nima_mean"]) / 10.0))
    return unknown


def rank_group(
    results: Mapping[str, Mapping[str, Any]], weights: Mapping[str, float] | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rank one group. `results` maps photo_id -> that photo's tool results:
    {sharpness_<roi>_<variant>: {...}, exposure: {...}, eyes: {...}, face_quality: {...}, composition: {...}}.
    """
    cfg = cfg or section("best")
    w = {**cfg["weights"], **(weights or {})}
    unknown = float(cfg["unknown_component"])
    exp_vals = cfg["exposure_ok_values"]
    ids = sorted(results)
    if not ids:
        raise ValueError("empty group")
    key = _sharp_key_common([results[p] for p in ids])
    raw = {p: (float(results[p][key]["lap_var"]) if key else _sharp_value(results[p])) for p in ids}
    log = cfg.get("sharpness_norm", "log_minmax") == "log_minmax"
    tv = {p: (math.log1p(max(v, 0.0)) if log else v) for p, v in raw.items() if v is not None}
    lo, hi = (min(tv.values()), max(tv.values())) if tv else (0.0, 0.0)
    # spread guard: scale the min-max spread by how far apart the group really is (1.0 at >= full_ratio)
    known = [v for v in raw.values() if v is not None]
    full_ratio = float(cfg.get("sharpness_full_ratio", 0) or 0)
    spread = 1.0
    if known and full_ratio > 1.0:
        ratio = (max(known) + 1.0) / (min(known) + 1.0)
        spread = min(1.0, math.log(ratio) / math.log(full_ratio))

    entries = []
    for p in ids:
        r = results[p]
        if p not in tv:
            sharp = unknown
        elif hi - lo <= 1e-12:
            sharp = 1.0
        else:
            sharp = 1.0 - spread * (1.0 - (tv[p] - lo) / (hi - lo))
        lab = (r.get("exposure") or {}).get("label")
        bd = {
            "sharp_faces_norm": round(sharp, 6),
            "eyes_open_mean": round(_eyes(r, unknown), 6),
            "exposure_ok": float(exp_vals.get(lab, unknown)) if lab else unknown,
            "fiqa_mean": round(_fiqa(r, unknown), 6),
            "composition": round(_composition(r, unknown), 6),
        }
        score = sum(float(w.get(k, 0.0)) * v for k, v in bd.items())
        entries.append(RankEntry(photo_id=p, score=round(score, 6), breakdown=bd))
    entries.sort(key=lambda e: (-e.score, e.photo_id))
    margin = entries[0].score - entries[1].score if len(entries) > 1 else 1.0
    res = BestResult(
        best_photo_id=entries[0].photo_id,
        ranking=entries,
        tie=bool(len(entries) > 1 and margin < float(cfg["tie_margin"])),
        margin=round(margin, 6),
    ).model_dump()
    res["sharpness_key"] = key
    return res


def pick_best_in_cluster(group_id: str, weights: dict[str, float] | None = None,
                         event_id: str | None = None) -> dict[str, Any]:
    """Weighted, deterministic best-frame pick over a group's recorded tool results.

    Returns {best_photo_id, ranking:[{photo_id, score, breakdown}], tie, margin}.
    """
    from keepframe.tools.ingest import _contexts

    ctxs = [_contexts[event_id]] if event_id else list(_contexts.values())
    for ctx in ctxs:
        if group_id in ctx.groups:
            members = ctx.groups[group_id]
            return rank_group({p: ctx.results.get(p, {}) for p in members}, weights)
    raise KeyError(f"unknown group {group_id}: run cluster_near_duplicates first")

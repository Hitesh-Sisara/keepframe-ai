"""score_exposure tool (docs/REPORT.md §3): Lab L-channel histogram statistics on the 1024-px proxy.

L is OpenCV's 8-bit Lab lightness (0-255). clip_lo_pct = hist[:6]/N, clip_hi_pct = hist[250:]/N,
fractions in [0, 1] (the "%" names follow the plan; thresholds in thresholds.yaml are fractions too).

Labels (first match): over (clip_hi > 5% and mean_L > 200), under (clip_lo > 40% and mean_L < 45),
low_contrast (std_L < 25), else ok. Confidence = distance from the deciding threshold, normalised.
"under" is a soft label: the EXPOSURE_UNDER reason needs `under_flag` (dark AND flat subject AND dark for
this event), because dim venues with a lit subject are normal at receptions and parties.
`no_subject` flags near-uniform frames (lens cap, blank wall) for the NO_SUBJECT reason code.
"""

from __future__ import annotations

import time
from typing import Any

import cv2
import numpy as np

from keepframe.config import section
from keepframe.schemas import ExposureResult


def _ratio_conf(value: float, thresh: float, cmin: float, cmax: float) -> float:
    """0.5 at the threshold rising to 1 as value moves away by 100% of the threshold; clipped."""
    if thresh == 0:
        return cmax
    d = abs(value - thresh) / abs(thresh)
    return float(np.clip(0.5 + 0.5 * d, cmin, cmax))


def exposure_stats(bgr: np.ndarray, cfg: dict[str, Any] | None = None) -> dict[str, float]:
    cfg = cfg or section("exposure")
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)
    L = lab[:, :, 0]
    hist = cv2.calcHist([L], [0], None, [256], [0, 256]).ravel()
    n = float(L.size)
    p = hist / n
    nz = p[p > 0]
    mean, std = cv2.meanStdDev(L)
    return {
        "clip_lo_pct": float(hist[: int(cfg["clip_lo_bins"])].sum() / n),
        "clip_hi_pct": float(hist[int(cfg["clip_hi_bin"]) :].sum() / n),
        "mean_L": float(mean[0][0]),
        "std_L": float(std[0][0]),
        "entropy": float(-(nz * np.log2(nz)).sum()),
    }


def label_exposure(s: dict[str, float], cfg: dict[str, Any] | None = None) -> tuple[str, float]:
    cfg = cfg or section("exposure")
    cmin, cmax = float(cfg["conf_min"]), float(cfg["conf_max"])
    over = s["clip_hi_pct"] > cfg["over_clip_hi_pct"] and s["mean_L"] > cfg["over_mean_l"]
    under = s["clip_lo_pct"] > cfg["under_clip_lo_pct"] and s["mean_L"] < cfg["under_mean_l"]
    if over:
        c = min(_ratio_conf(s["clip_hi_pct"], cfg["over_clip_hi_pct"], cmin, cmax),
                _ratio_conf(s["mean_L"], cfg["over_mean_l"], cmin, cmax) + 0.25)
        return "over", round(min(c, cmax), 4)
    if under:
        c = min(_ratio_conf(s["clip_lo_pct"], cfg["under_clip_lo_pct"], cmin, cmax),
                _ratio_conf(s["mean_L"], cfg["under_mean_l"], cmin, cmax))
        return "under", round(c, 4)
    if s["std_L"] < cfg["low_contrast_std_l"]:
        return "low_contrast", round(_ratio_conf(s["std_L"], cfg["low_contrast_std_l"], cmin, cmax), 4)
    # ok: confidence = how far the nearest "bad" rule is from firing
    margins = [
        _ratio_conf(s["std_L"], cfg["low_contrast_std_l"], cmin, cmax),
        _ratio_conf(s["mean_L"], cfg["under_mean_l"], cmin, cmax) if s["clip_lo_pct"] > cfg["under_clip_lo_pct"]
        else cmax,
        _ratio_conf(s["mean_L"], cfg["over_mean_l"], cmin, cmax) if s["clip_hi_pct"] > cfg["over_clip_hi_pct"]
        else cmax,
    ]
    return "ok", round(min(margins), 4)


def subject_stats(L: np.ndarray, face_boxes: list[list[float]] | None, cfg: dict[str, Any]) -> dict[str, Any]:
    """Lightness of the subject: mean/std of L inside face boxes ([l,t,w,h] ratios) when given, else the
    p-th percentile of L (the lit part of the frame) and the std of the pixels at or above it."""
    H, W = L.shape[:2]
    vals = []
    for b in face_boxes or []:
        x0, y0 = max(0, int(b[0] * W)), max(0, int(b[1] * H))
        x1, y1 = min(W, int(np.ceil((b[0] + b[2]) * W))), min(H, int(np.ceil((b[1] + b[3]) * H)))
        if x1 - x0 >= 4 and y1 - y0 >= 4:
            vals.append(L[y0:y1, x0:x1].reshape(-1))
    p5 = float(np.percentile(L, 5))
    if vals:
        # brightest face decides "dark" (one lit main subject is enough); most clipped face decides "blown"
        means = [(float(v.mean()), float(v.std())) for v in vals]
        m, sd = max(means)
        clip = max(float((v >= int(cfg["clip_hi_bin"])).mean()) for v in vals)
        return {"subject_L": m, "subject_std_L": sd, "subject_source": "faces", "subject_clip_hi": clip, "p5_L": p5}
    hi = float(np.percentile(L, float(cfg["subject_p_hi"])))
    top = L[L >= np.percentile(L, max(0.0, float(cfg["subject_p_hi"]) - 5.0))]
    return {"subject_L": hi, "subject_std_L": float(top.std()) if top.size else 0.0, "subject_source": "highlights",
            "subject_clip_hi": None, "p5_L": p5}


def under_flag(res: dict[str, Any], event_median_mean_l: float | None, cfg: dict[str, Any] | None = None) -> bool:
    """EXPOSURE_UNDER only when the frame is labelled under AND the subject itself is dark and flat AND the
    frame is dark relative to the event (thresholds.yaml exposure.*; rule 4 skipped when no baseline)."""
    cfg = cfg or section("exposure")
    if res.get("label") != "under":
        return False
    sl, sd = res.get("subject_L"), res.get("subject_std_L")
    if sl is None:
        return False
    if res.get("subject_source") == "faces":
        if sl >= float(cfg["face_under_max_l"]):
            return False
    elif sl >= float(cfg["subject_under_max_l"]) or (sd is not None and sd >= float(cfg["subject_under_max_std"])):
        return False
    if event_median_mean_l is not None:
        return float(res["mean_L"]) < float(cfg["event_rel_frac"]) * float(event_median_mean_l)
    return True


def over_flag(res: dict[str, Any], event_median_mean_l: float | None, cfg: dict[str, Any] | None = None) -> bool:
    """EXPOSURE_OVER only when the frame is labelled over AND the subject is clipped AND the frame is far brighter
    than its event (studio backdrops and flash are a venue property, not a defect)."""
    cfg = cfg or section("exposure")
    if res.get("label") != "over":
        return False
    if res.get("subject_source") == "faces" and res.get("subject_clip_hi") is not None:
        if float(res["subject_clip_hi"]) < float(cfg["face_over_clip_frac"]):
            return False
    else:
        p5 = res.get("p5_L")
        if float(res["clip_hi_pct"]) < float(cfg["over_noface_clip_hi"]) or p5 is None or \
                float(p5) < float(cfg["over_noface_p5_l"]):
            return False
    if event_median_mean_l is not None:
        return float(res["mean_L"]) > float(event_median_mean_l) + float(cfg["event_over_delta_l"])
    return True


def score_exposure_image(bgr: np.ndarray, cfg: dict[str, Any] | None = None,
                         face_boxes: list[list[float]] | None = None,
                         event_median_mean_l: float | None = None) -> dict[str, Any]:
    """Pure exposure scoring of a BGR image -> tool-result dict (+ subject_L / under_flag, see under_flag())."""
    cfg = cfg or section("exposure")
    s = exposure_stats(bgr, cfg)
    label, conf = label_exposure(s, cfg)
    no_subject = s["std_L"] < cfg["no_subject_std_l_max"] and s["entropy"] < cfg["no_subject_entropy_max"]
    L = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)[:, :, 0]
    subj = subject_stats(L, face_boxes, cfg)
    res = ExposureResult(
        clip_lo_pct=round(s["clip_lo_pct"], 5),
        clip_hi_pct=round(s["clip_hi_pct"], 5),
        mean_L=round(s["mean_L"], 3),
        std_L=round(s["std_L"], 3),
        entropy=round(s["entropy"], 4),
        label=label,  # type: ignore[arg-type]
        confidence=conf,
        no_subject=bool(no_subject),
        subject_L=round(subj["subject_L"], 3),
        subject_std_L=round(subj["subject_std_L"], 3),
        subject_source=subj["subject_source"],
        subject_clip_hi=None if subj["subject_clip_hi"] is None else round(subj["subject_clip_hi"], 5),
        p5_L=round(subj["p5_L"], 3),
    ).model_dump()
    res["under_flag"] = under_flag(res, event_median_mean_l, cfg)
    res["over_flag"] = over_flag(res, event_median_mean_l, cfg)
    return res


def score_exposure(photo_id: str, event_id: str | None = None,
                   face_boxes: list[list[float]] | None = None) -> dict[str, Any]:
    """Histogram clipping, mean/std of Lab L, entropy for one photo's 1024-px proxy.

    face_boxes ([l,t,w,h] ratios) let subject_L use the faces; otherwise the frame's highlights are used.
    Returns {clip_lo_pct, clip_hi_pct, mean_L, std_L, entropy, label: ok|under|over|low_contrast, confidence,
    subject_L, subject_std_L, under_flag}. Without an event baseline, under_flag skips the event-relative test.
    """
    from keepframe.io import proxies
    from keepframe.tools.ingest import get_context, resolve_event

    ev = resolve_event(photo_id, event_id)
    t0 = time.perf_counter()
    res = score_exposure_image(proxies.load_p1024(ev, photo_id), face_boxes=face_boxes)
    get_context(ev).record(photo_id, "exposure", res)
    res["latency_ms"] = round((time.perf_counter() - t0) * 1e3, 2)
    return res

"""Deterministic stand-ins for the OpenCV tools (``CULL_TOOLS=stub``), same signatures and result shapes.

Results come from a *scenario*: per-photo canned numbers keyed by tool/roi/variant. ``demo_scenario()``
builds the trace-schema example event (borderline -> faces re-score, main-subject eyes closed -> compare in group, small
margin -> human) plus filler groups/singletons, so the agent loop can be tested with no images, no models
and no network. pick_best_in_cluster delegates to the real deterministic ranker (keepframe.tools.best)
over the stub results, so the stub path exercises the real scoring formula.
"""

from __future__ import annotations

import copy
from typing import Any

from keepframe.tools.best import rank_group


def _sharp(lap: float, label: str, conf: float, roi: str = "full", variant: str = "processed",
           blur_kind: str | None = None) -> dict[str, Any]:
    return {"lap_var": lap, "tenengrad": round(lap * 15.2, 1), "fft_hf": round(6 + lap / 20, 1),
            "brisque": 41.3 if roi == "full" else None, "label": label, "confidence": conf,
            "blur_kind": blur_kind, "motion_ratio": 3.1 if blur_kind == "motion" else 1.3,
            "roi_used": roi, "variant": variant, "variant_fallback": False}


def _exp(mean_l: float = 120.0, label: str = "ok", clip_lo: float = 0.02) -> dict[str, Any]:
    return {"clip_lo_pct": clip_lo, "clip_hi_pct": 0.004, "mean_L": mean_l, "std_L": 52.0, "entropy": 7.1,
            "label": label, "confidence": 0.9, "no_subject": False}


def _faces(*sizes: int) -> dict[str, Any]:
    fs = []
    x = 100
    for i, s in enumerate(sizes):
        fs.append({"face_id": f"f{i}", "bbox_px": [x, 200, s, int(s * 1.2)], "score": 0.97,
                   "landmarks5": [[x + s * 0.3, 240], [x + s * 0.7, 240], [x + s * 0.5, 270],
                                  [x + s * 0.35, 300], [x + s * 0.65, 300]], "source": "frsInfo"})
        x += s + 40
    return {"faces": fs, "n_faces": len(fs)}


def _eyes(states: list[tuple[float | None, float | None, str]], main: str | None = "f0") -> dict[str, Any]:
    per = [{"face_id": f"f{i}", "left_open_p": lp, "right_open_p": rp, "state": st,
            "confidence": 0.9 if st in ("open", "closed") else 0.5, "is_main": f"f{i}" == main}
           for i, (lp, rp, st) in enumerate(states)]
    main_closed = any(p["is_main"] and p["state"] == "closed" for p in per)
    return {"per_face": per, "any_main_subject_closed": main_closed, "main_subject_face_id": main,
            "confidence": 0.9}


def _fq(*fiqa: float) -> dict[str, Any]:
    return {"per_face": [{"face_id": f"f{i}", "fiqa": q, "lap_var": 900.0, "minPx": 120, "good": q > 0.5}
                         for i, q in enumerate(fiqa)], "main_subject_face_id": "f0"}


def demo_scenario(event_id: str = "ev_demo") -> dict[str, Any]:
    """The docs/AGENT.md trace-schema story on synthetic ids. Group g_412 drives the three decision-change moments."""
    P: dict[str, dict[str, Any]] = {}
    # g_412: 4 frames. p_8831 borderline full / sharp faces; p_8833 main subject eyes closed; tight margin.
    P["p_8830"] = {"sharp": {"full:processed": _sharp(126.0, "sharp", 0.6),
                             "faces:processed": _sharp(58.0, "blurry", 0.86, "faces", blur_kind="focus")},
                   "exposure": _exp(), "faces": _faces(180, 90),
                   "eyes": _eyes([(0.96, 0.94, "open"), (0.9, 0.88, "open")]), "fq": _fq(0.41, 0.4)}
    P["p_8831"] = {"sharp": {"full:processed": _sharp(74.2, "borderline", 0.55),
                             "faces:processed": _sharp(188.9, "sharp", 0.92, "faces")},
                   "exposure": _exp(), "faces": _faces(212, 96),
                   "eyes": _eyes([(0.97, 0.95, "open"), (0.12, 0.09, "closed")]), "fq": _fq(0.71, 0.66)}
    P["p_8832"] = {"sharp": {"full:processed": _sharp(131.0, "sharp", 0.62),
                             "faces:processed": _sharp(160.0, "sharp", 0.8, "faces")},
                   "exposure": _exp(), "faces": _faces(205, 92),
                   "eyes": _eyes([(0.91, 0.9, "open"), (0.85, 0.8, "open")]), "fq": _fq(0.52, 0.5)}
    P["p_8833"] = {"sharp": {"full:processed": _sharp(240.0, "sharp", 0.95),
                             "faces:processed": _sharp(205.0, "sharp", 0.93, "faces")},
                   "exposure": _exp(), "faces": _faces(220, 98),
                   "eyes": _eyes([(0.06, 0.04, "closed"), (0.95, 0.93, "open")]), "fq": _fq(0.74, 0.69)}
    # g_413: clear winner, the duller frame is rejected as BURST_NOT_BEST without a human
    P["p_8840"] = {"sharp": {"full:processed": _sharp(420.0, "sharp", 0.95),
                             "faces:processed": _sharp(510.0, "sharp", 0.95, "faces")},
                   "exposure": _exp(), "faces": _faces(260),
                   "eyes": _eyes([(0.98, 0.97, "open")]), "fq": _fq(0.8)}
    P["p_8841"] = {"sharp": {"full:processed": _sharp(39.0, "blurry", 0.85, blur_kind="motion"),
                             "faces:processed": _sharp(41.0, "blurry", 0.86, "faces", blur_kind="motion")},
                   "exposure": _exp(), "faces": _faces(255),
                   "eyes": _eyes([(0.97, 0.96, "open")]), "fq": _fq(0.35)}
    # singletons: one sharp, one dark long-exposure motion blur (artistic-intent guard)
    P["p_9001"] = {"sharp": {"saliency:processed": _sharp(300.0, "sharp", 0.95, "saliency"),
                             "full:processed": _sharp(280.0, "sharp", 0.95)},
                   "exposure": _exp(), "faces": _faces(150), "eyes": _eyes([(0.95, 0.95, "open")]),
                   "fq": _fq(0.7)}
    P["p_9002"] = {"sharp": {"saliency:processed": _sharp(35.0, "blurry", 0.88, "saliency", blur_kind="motion"),
                             "full:processed": _sharp(33.0, "blurry", 0.9, blur_kind="motion")},
                   "exposure": _exp(38.0, "under", 0.34), "faces": {"faces": [], "n_faces": 0},
                   "exposure_time_s": 0.25, "hour": 22}
    records: list[dict[str, Any]] = []
    for pid in P:
        n = len(P[pid]["faces"]["faces"])
        records.append({"photo_id": pid, "event_id": event_id, "name": f"IMG_{pid[2:]}.JPG",
                        "frs_status": "indexed" if n else "no_faces", "frs_faces": [{}] * n,
                        "original_key": f"{event_id}/{pid}.jpg" if pid != "p_9002" else None,
                        "width": 2560, "height": 1707})
    groups = [
        {"group_id": "g_412", "members": ["p_8830", "p_8831", "p_8832", "p_8833"], "size": 4, "span_s": 2.1,
         "needs_approval": True},
        {"group_id": "g_413", "members": ["p_8840", "p_8841"], "size": 2, "span_s": 0.4, "needs_approval": False},
    ]
    # the worker's baseline verdicts (what results/<e>/decisions.jsonl would hold before the agent)
    baseline = {
        "p_8830": ("reject", ["BURST_NOT_BEST", "BLUR_FOCUS"], 0.86, "g_412", False),
        "p_8831": ("review", ["BURST_NOT_BEST", "REVIEW_LOW_CONFIDENCE"], 0.6, "g_412", True),
        "p_8832": ("reject", ["BURST_NOT_BEST"], 0.8, "g_412", True),
        "p_8833": ("keep", ["KEEP_BEST_OF_GROUP"], 0.5, "g_412", True),
        "p_8840": ("keep", ["KEEP_BEST_OF_GROUP"], 0.95, "g_413", False),
        "p_8841": ("reject", ["BURST_NOT_BEST", "BLUR_MOTION"], 0.86, "g_413", False),
        "p_9001": ("keep", ["KEEP_SINGLETON"], 0.95, None, False),
        "p_9002": ("review", ["REVIEW_ARTISTIC_INTENT", "BLUR_MOTION"], 0.88, None, True),
    }
    return {"event_id": event_id, "photos": P, "records": records, "groups": groups,
            "singletons": ["p_9001", "p_9002"],
            "decisions": [{"photo_id": p, "verdict": v, "reason_codes": c, "confidence": cf, "group_id": g,
                           "needs_approval": na, "decided_by": "worker"} for p, (v, c, cf, g, na) in baseline.items()]}


class StubTools:
    """Same call surface as RealTools (keepframe.agent.toolset); results from a scenario dict."""

    flavor = "stub"

    def __init__(self, scenario: dict[str, Any], fail: dict[str, int] | None = None) -> None:
        self.s = scenario
        self.fail = dict(fail or {})  # tool name -> number of times to raise before succeeding

    def _maybe_fail(self, tool: str) -> None:
        if self.fail.get(tool, 0) > 0:
            self.fail[tool] -= 1
            raise RuntimeError(f"stub failure injected for {tool}")

    def _p(self, pid: str) -> dict[str, Any]:
        if pid not in self.s["photos"]:
            raise KeyError(f"unknown photo {pid}")
        photo: dict[str, Any] = self.s["photos"][pid]
        return photo

    def score_sharpness(self, photo_id: str, roi: str, variant: str, face_boxes: Any = None) -> dict[str, Any]:
        self._maybe_fail("score_sharpness")
        sh = self._p(photo_id)["sharp"]
        key = f"{roi}:{variant}"
        if key in sh:
            return dict(copy.deepcopy(sh[key]))
        r: dict[str, Any]
        if variant == "original" and f"{roi}:processed" in sh:
            r = dict(copy.deepcopy(sh[f"{roi}:processed"]))
            r["variant"] = "original"
            return r
        r = dict(copy.deepcopy(sh.get("full:processed") or next(iter(sh.values()))))
        r["roi_used"] = "full"
        return r

    def score_exposure(self, photo_id: str) -> dict[str, Any]:
        self._maybe_fail("score_exposure")
        return dict(copy.deepcopy(self._p(photo_id)["exposure"]))

    def detect_faces(self, photo_id: str) -> dict[str, Any]:
        self._maybe_fail("detect_faces")
        return dict(copy.deepcopy(self._p(photo_id)["faces"]))

    def score_eyes_open(self, photo_id: str, faces: dict[str, Any]) -> dict[str, Any]:
        self._maybe_fail("score_eyes_open")
        p = self._p(photo_id)
        return copy.deepcopy(p.get("eyes") or {"per_face": [], "any_main_subject_closed": False,
                                                "main_subject_face_id": None, "confidence": 0.0})

    def score_face_quality(self, photo_id: str, faces: dict[str, Any]) -> dict[str, Any]:
        self._maybe_fail("score_face_quality")
        return copy.deepcopy(self._p(photo_id).get("fq") or {"per_face": [], "main_subject_face_id": None})

    def pick_best_in_cluster(self, group_id: str, results: dict[str, dict[str, Any]],
                             weights: dict[str, float] | None = None) -> dict[str, Any]:
        self._maybe_fail("pick_best_in_cluster")
        return rank_group(results, weights)

    def score_composition(self, photo_id: str) -> dict[str, Any]:
        return {"nima_mean": None, "nima_std": None, "nima_available": False, "subject_centrality": 0.6}

    def enhance_keeper(self, event_id: str, photo_id: str, ops: list[str] | None) -> dict[str, Any]:
        return {"s3_key": f"preview/{event_id}/{photo_id}.jpg", "ops_applied": ops or ["wb", "clahe"],
                "delta_brisque": -2.1}

    def make_contact_sheet(self, event_id: str, group_id: str, photo_ids: list[str],
                           captions: dict[str, str], best_photo_id: str | None) -> dict[str, Any]:
        return {"key": f"sheets/{event_id}/{group_id}.jpg", "n_tiles": len(photo_ids), "width": 1024,
                "height": 300}

    def exif_facts(self, photo_id: str) -> dict[str, Any]:
        p = self._p(photo_id)
        return {"exposure_time_s": p.get("exposure_time_s"), "hour": p.get("hour")}

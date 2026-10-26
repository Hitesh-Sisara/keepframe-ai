#!/usr/bin/env python3
"""Regenerate the measured evidence behind docs/FAILURE_CASES.md (no images, no ids in the output).

Each catalogued case has a detector: a pure function over one event's worker/agent outputs that
returns the numbers the doc quotes. Cases that happened on a run whose outputs were later replaced
(the pre-fix E07 run, the untuned E09/E03 runs, the AgentCore probe) are `historical`: their numbers
come from committed summaries or from the dated entry named in `source`.

Inputs, per event alias (E03, E07, E09), from the work bucket's `results/<eventId>/`:
  decisions.jsonl  groups.json  hashes.json  exif_index.json  summary.json  trace.jsonl
Lay them out as `<root>/<alias>/...` (or `<root>/<eventId>/...`, mapped through eval/events.py).

    aws s3 cp --recursive s3://<work-bucket>/results/<eventId>/ <root>/E07/ \
        --exclude '*' --include '*.json' --include '*.jsonl'          # once per event
    python eval/failure_cases.py --root <root> --event E03 --event E07 --event E09
    -> eval/results/failure_cases.json, eval/results/failure_cases.md

The output never contains event, photo, group or run ids: events are aliases, example photos are
per-case aliases (C08-p1, ...) and any UUID left in a run id is replaced by the event alias.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from events import load_aliases  # noqa: E402

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
Row = dict[str, Any]

# Same values as config/thresholds.yaml at the time of the runs (hashes.phash_max, cluster.time_window_s,
# sharpness.lap_sharp_min, policy.reception_hour_*, policy.default_tz_offset_min).
PHASH_MAX = 10
WINDOW_S = 10.0
LAP_SHARP_MIN = 120.0
RECEPTION = (18, 4)
DEFAULT_TZ_MIN = 330


# --------------------------------------------------------------------------- loading


def read_jsonl(path: Path) -> list[Row]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _json(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else {}


@dataclass
class Event:
    """One event's run outputs. `rows` holds every decision line; `worker`/`final` are per photo."""

    alias: str
    rows: list[Row]
    groups: list[Row]
    exact: dict[str, str]
    hashes: dict[str, Row]
    exif: dict[str, Row]
    summary: Row
    trace: list[Row]
    worker: dict[str, Row] = field(default_factory=dict)
    final: dict[str, Row] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for r in self.rows:
            pid = r.get("photo_id")
            if not isinstance(pid, str):
                continue
            if r.get("decided_by") == "worker" and pid not in self.worker:
                self.worker[pid] = r
            self.final[pid] = r  # append-only: last line per photo wins
        for pid, r in self.final.items():
            self.worker.setdefault(pid, r)


def load_event(d: Path, alias: str) -> Event:
    g = _json(d / "groups.json") or {}
    return Event(
        alias=alias,
        rows=read_jsonl(d / "decisions.jsonl"),
        groups=list(g.get("groups") or []),
        exact=dict(g.get("exact_duplicates") or {}),
        hashes=dict(_json(d / "hashes.json") or {}),
        exif=dict((_json(d / "exif_index.json") or {}).get("photos") or {}),
        summary=dict(_json(d / "summary.json") or {}),
        trace=read_jsonl(d / "trace.jsonl"),
    )


# --------------------------------------------------------------------------- helpers


def ham(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _phash(ev: Event, pid: str) -> str | None:
    return (ev.hashes.get(pid) or {}).get("phash")


def per_face(r: Row) -> list[Row]:
    return list(((r.get("scores") or {}).get("eyes") or {}).get("per_face") or [])


def main_face(r: Row) -> Row | None:
    return next((f for f in per_face(r) if f.get("is_main")), None)


def codes(r: Row) -> list[str]:
    return [str(c).split(":")[0] for c in r.get("reason_codes") or []]


def canon(ev: Event, pid: str) -> str:
    return ev.exact.get(pid, pid)


def _median(xs: Iterable[float]) -> float | None:
    v = list(xs)
    return round(float(statistics.median(v)), 1) if v else None


class Aliaser:
    """Per-case photo aliases (C08-p1, C08-p2, ...). Never emits the real id."""

    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        self.seen: dict[str, str] = {}

    def __call__(self, pid: str) -> str:
        if pid not in self.seen:
            sep = "" if self.case_id.endswith("-g") else "-p"
            self.seen[pid] = f"{self.case_id}{sep}{len(self.seen) + 1}"
        return self.seen[pid]


def _ts(r: Row) -> datetime:
    return datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00"))


def _in_reception(h: int) -> bool:
    start, end = RECEPTION
    return h >= start or h < end


# --------------------------------------------------------------------------- detectors
# Each returns a flat dict of measured numbers (+ optional "examples" with per-case photo aliases).


def d_exposure_soft_vs_flag(ev: Event) -> Row:
    """Soft exposure label vs the venue-relative flag that actually drives EXPOSURE_* codes."""
    ex = [(r.get("scores") or {}).get("exposure") or {} for r in ev.worker.values()]
    return {
        "photos": len(ex),
        "label_under": sum(1 for e in ex if e.get("label") == "under"),
        "label_over": sum(1 for e in ex if e.get("label") == "over"),
        "under_flag": sum(1 for e in ex if e.get("under_flag")),
        "over_flag": sum(1 for e in ex if e.get("over_flag")),
        "EXPOSURE_UNDER_worker": sum(1 for r in ev.worker.values() if "EXPOSURE_UNDER" in codes(r)),
        "EXPOSURE_OVER_worker": sum(1 for r in ev.worker.values() if "EXPOSURE_OVER" in codes(r)),
        **_soft_label_in_best_pick(ev),
    }


def _soft_label_in_best_pick(ev: Event, weight: float = 0.15) -> Row:
    """Groups whose members carry different soft exposure labels, and those where removing the
    exposure_ok term (weight 0.15, a 0/1 step on the soft label) changes the top-ranked frame."""
    mixed = flipped = 0
    for g in ev.groups:
        rk = [r for r in g.get("ranking") or [] if "breakdown" in r]
        if len({r["breakdown"].get("exposure_ok") for r in rk}) <= 1:
            continue
        mixed += 1
        top = max(rk, key=lambda r: float(r["score"]))
        alt = max(rk, key=lambda r: float(r["score"]) - weight * float(r["breakdown"].get("exposure_ok") or 0))
        flipped += top["photo_id"] != alt["photo_id"]
    return {"groups_mixed_soft_label": mixed, "groups_top_decided_by_soft_label": flipped}


def d_agent_exposure_without_baseline(ev: Event) -> Row:
    """Agent/scripted rows that add EXPOSURE_UNDER to a photo whose worker under_flag was false."""
    al = Aliaser("C04")
    hits = []
    for r in ev.rows:
        if r.get("decided_by") == "worker" or "EXPOSURE_UNDER" not in codes(r):
            continue
        w = ev.worker.get(str(r["photo_id"])) or {}
        we = (w.get("scores") or {}).get("exposure") or {}
        if not we.get("under_flag"):
            hits.append({"photo": al(str(r["photo_id"])), "decided_by": r.get("decided_by"),
                         "mean_L": we.get("mean_L"), "subject_L": we.get("subject_L"),
                         "worker_codes": codes(w)})
    return {"rows_adding_under": len(hits), "examples": hits[:5]}


def d_all_closed_groups(ev: Event) -> Row:
    """Groups where every frame failed (P4: main subject's eyes closed in all) and their auto-rejects."""
    al = Aliaser("C05")
    groups = [g for g in ev.groups if "every frame failed" in (g.get("approval_reasons") or [])]
    rejects = [ev.final[m] for g in groups for m in g["members"]
               if ev.final.get(m, {}).get("verdict") == "reject"]
    ex = []
    for g in groups[:3]:
        ex.append({"size": g["size"], "frames": [
            {"photo": al(m), "verdict": ev.final[m]["verdict"], "codes": codes(ev.final[m]),
             "confidence": ev.final[m].get("confidence"),
             "main_p_open": [(main_face(ev.final[m]) or {}).get(k) for k in ("left_open_p", "right_open_p")]}
            for m in g["members"][:3]]})
    return {"groups": len(groups), "sizes": dict(Counter(g["size"] for g in groups)),
            "frames": sum(g["size"] for g in groups), "auto_rejects": len(rejects),
            "auto_reject_conf_min": min((float(r["confidence"]) for r in rejects), default=None),
            "examples": ex}


def d_unknown_eyes(ev: Event) -> Row:
    faces = [f for r in ev.worker.values() for f in per_face(r)]
    unk = [f for f in faces if f.get("state") == "unknown"]
    main_unk = [f for f in unk if f.get("is_main")]
    big = [f for f in unk if float(f.get("min_px") or 0) >= 100]
    return {
        "faces_scored": len(faces), "unknown": len(unk),
        "main_faces": sum(1 for f in faces if f.get("is_main")), "main_unknown": len(main_unk),
        "main_unknown_min_px_median": _median(float(f["min_px"]) for f in main_unk),
        "unknown_ge_100px": len(big),
        "unknown_ge_100px_max": max((float(f["min_px"]) for f in big), default=None),
        "main_unknown_lt_80px": sum(1 for f in main_unk if float(f.get("min_px") or 0) < 80),
        "frame_mean_L_median_unknown_ge_100px": _median(
            float(((r.get("scores") or {}).get("exposure") or {}).get("mean_L") or 0)
            for r in ev.worker.values() for f in per_face(r)
            if f.get("state") == "unknown" and float(f.get("min_px") or 0) >= 100),
        "frame_mean_L_median_read_ge_100px": _median(
            float(((r.get("scores") or {}).get("exposure") or {}).get("mean_L") or 0)
            for r in ev.worker.values() for f in per_face(r)
            if f.get("state") != "unknown" and float(f.get("min_px") or 0) >= 100),
    }


def d_multi_closed(ev: Event) -> Row:
    """Frames with >= 3 faces where >= 2 non-main faces are 'closed' and no eye code was emitted."""
    al = Aliaser("C08")
    hits: list[Row] = []
    for r in ev.final.values():
        pf = per_face(r)
        closed = [f for f in pf if not f.get("is_main") and f.get("state") == "closed"]
        if len(pf) >= 3 and len(closed) >= 2 and not any(
                c in ("EYES_CLOSED", "REVIEW_POLICY_GROUP_EYES") for c in codes(r)):
            hits.append({"photo": al(str(r["photo_id"])), "faces": len(pf), "non_main_closed": len(closed),
                         "verdict": r.get("verdict"), "codes": codes(r)})
    return {"frames": len(hits), "verdicts": dict(Counter(h["verdict"] for h in hits)),
            "max_closed": max((int(h["non_main_closed"]) for h in hits), default=0),
            "examples": sorted(hits, key=lambda h: -int(h["non_main_closed"]))[:3]}


def d_policy_code_misuse(ev: Event) -> Row:
    """REVIEW_POLICY_GROUP_EYES (defined for one NON-main closed face) written for a closed MAIN face."""
    al = Aliaser("C16")
    hits = []
    for r in ev.rows:
        mf = main_face(ev.worker.get(str(r.get("photo_id")), {}))
        if "REVIEW_POLICY_GROUP_EYES" in codes(r) and mf and mf.get("state") == "closed":
            w = ev.worker[str(r["photo_id"])]
            hits.append({"photo": al(str(r["photo_id"])), "decided_by": r.get("decided_by"),
                         "verdict": r.get("verdict"), "worker_verdict": w.get("verdict"),
                         "worker_codes": codes(w), "worker_conf": w.get("confidence")})
    return {"rows": len(hits), "examples": hits[:3]}


def md5_sets(ev: Event) -> list[set[str]]:
    """Sets of byte-identical files (same md5), from groups.json exact_duplicates (copy -> first file)."""
    sets: dict[str, set[str]] = {}
    for d, c in ev.exact.items():
        sets.setdefault(c, {c}).add(d)
    return list(sets.values())


def d_exact_dup_coding(ev: Event) -> Row:
    """Per md5 set: every file beyond the set's one judged file should be DUPLICATE_OF, and the judged
    files of one set should not disagree. (Counted per set, not per copy -> first-file pair.)"""
    def is_dup(p: str) -> bool:
        return "DUPLICATE_OF" in codes(ev.final.get(p, {}))

    sets = [{p for p in st if p in ev.final} for st in md5_sets(ev)]
    sets = [st for st in sets if len(st) > 1]
    judged = [[p for p in st if not is_dup(p)] for st in sets]
    return {
        "md5_sets": len(sets),
        "files_in_sets": sum(len(st) for st in sets),
        "copies_not_DUPLICATE_OF": sum(max(0, len(j) - 1) for j in judged),
        "sets_with_conflicting_verdicts": sum(1 for j in judged
                                              if len({ev.final[p].get("verdict") for p in j}) > 1),
    }


def d_identical_alternatives(ev: Event) -> Row:
    """Groups whose tied_candidates (the frames a human is asked to choose between) hold identical files."""
    hits = []
    for g in ev.groups:
        tc = [t if isinstance(t, str) else t.get("photo_id") for t in g.get("tied_candidates") or []]
        tc = [t for t in tc if t]
        if len(tc) > 1 and len({canon(ev, t) for t in tc}) < len(tc):
            hits.append({"size": g["size"], "candidates": len(tc), "distinct_files": len({canon(ev, t) for t in tc}),
                         "needs_approval": bool(g.get("needs_approval"))})
    pending_identical = 0
    for r in ev.trace:
        if r.get("type") == "tool_call" and r.get("tool") == "request_human_approval":
            for it in (r.get("args") or {}).get("items") or []:
                ids = [p for v in (it.get("proposed") or {}).values() for p in (v or [])]
                if len(ids) > 1 and len({canon(ev, p) for p in ids}) == 1:
                    pending_identical += 1
    return {"groups": len(hits), "asked_with_identical_files": sum(1 for h in hits if h["needs_approval"]),
            "agent_questions_all_identical": pending_identical, "examples": hits[:4]}


def d_near_singletons(ev: Event, slack: int = 2) -> Row:
    """Singleton pairs, same camera, within the time window, pHash just above the merge threshold."""
    in_group = {m for g in ev.groups for m in g["members"]}
    ids = sorted((p for p in ev.final if p in ev.exif and ev.exif[p].get("ts") is not None),
                 key=lambda p: (str(ev.exif[p].get("model")), float(ev.exif[p]["ts"])))
    pairs: list[tuple[int, float]] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if ev.exif[a].get("model") != ev.exif[b].get("model"):
                break
            dt = float(ev.exif[b]["ts"]) - float(ev.exif[a]["ts"])
            if dt > WINDOW_S:
                break
            if a in in_group or b in in_group:
                continue
            d = ham(_phash(ev, a), _phash(ev, b))
            if d is not None and PHASH_MAX < d <= PHASH_MAX + slack:
                pairs.append((d, round(dt, 1)))
    return {"pairs": len(pairs), "phash": dict(Counter(d for d, _ in pairs)),
            "dt_s_median": _median(dt for _, dt in pairs)}


def d_adjacent_groups(ev: Event, slack: int = 2) -> Row:
    """Consecutive groups (same camera) less than the window apart whose closest frames are near-duplicates."""
    def span(g: Row) -> tuple[float, float]:
        t = [float(ev.exif[m]["ts"]) for m in g["members"] if ev.exif.get(m, {}).get("ts") is not None]
        return (min(t), max(t)) if t else (0.0, 0.0)

    gs = sorted(ev.groups, key=lambda g: span(g)[0])
    near = within = 0
    for a, b in zip(gs, gs[1:], strict=False):
        if span(b)[0] - span(a)[1] > WINDOW_S:
            continue
        within += 1
        ds = [ham(_phash(ev, p), _phash(ev, q)) for p in a["members"] for q in b["members"]]
        dmin = min((d for d in ds if d is not None), default=99)
        near += dmin <= PHASH_MAX + slack
    sizes = Counter(g["size"] for g in ev.groups)
    return {"groups": len(ev.groups), "size2": sizes.get(2, 0), "adjacent_within_window": within,
            "adjacent_min_link_le_12": near}


def d_wide_groups(ev: Event, bits: int = 20) -> Row:
    """Groups chained far apart by single-link union-find: rejects that are > `bits` pHash from the keeper."""
    al, ag = Aliaser("C12"), Aliaser("C12-g")
    far: list[Row] = []
    diam = []
    for g in ev.groups:
        m = g["members"]
        ds = [ham(_phash(ev, a), _phash(ev, b)) for i, a in enumerate(m) for b in m[i + 1:]]
        dv = [d for d in ds if d is not None]
        if not dv:
            continue
        diam.append((max(dv), g["size"], sum(1 for d in dv if d >= 30), len(dv)))
        k = g.get("best_photo_id")
        for p in m:
            r = ev.final.get(p, {})
            d = ham(_phash(ev, k), _phash(ev, p)) if k else None
            if p != k and r.get("verdict") == "reject" and d is not None and d > bits:
                far.append({"photo": al(p), "group": ag(str(g["group_id"])), "group_size": g["size"], "phash_to_keeper": d,
                            "confidence": r.get("confidence"), "codes": codes(r)})
    top = max(diam, default=(0, 0, 0, 0))
    return {"rejects_far_from_keeper": len(far),
            "groups_with_far_rejects": len({f["group"] for f in far}),
            "far_reject_conf_min": min((float(f["confidence"]) for f in far), default=None),
            "widest_group": {"size": top[1], "phash_diameter": top[0], "pairs_ge_30": top[2], "pairs": top[3]},
            "examples": sorted(far, key=lambda f: -int(f["phash_to_keeper"]))[:3]}


def d_group_questions(ev: Event) -> Row:
    """After the once-per-group fix: share of groups that still need a human, and why."""
    asked = [g for g in ev.groups if g.get("needs_approval")]
    why: Counter[str] = Counter()
    for g in asked:
        for r in g.get("approval_reasons") or ["(none recorded)"]:
            why[re.sub(r"\d+", "N", re.sub(r"top-scored \S+", "top-scored <photo>", str(r)))] += 1
    return {"groups": len(ev.groups), "groups_asked": len(asked),
            "groups_asked_pct": round(100 * len(asked) / len(ev.groups), 1) if ev.groups else None,
            "worker_escalations": ev.summary.get("escalation_count"),
            "review_verdicts": sum(1 for r in ev.worker.values() if r.get("verdict") == "review"),
            "reasons": dict(why.most_common(5))}


def d_keeper_not_top(ev: Event) -> Row:
    hits = [g for g in ev.groups if g.get("ranking") and g["best_photo_id"] != g["ranking"][0]["photo_id"]]
    texts = [str(ev.final.get(g["best_photo_id"], {}).get("reason_text", "")) for g in hits]
    return {"groups": len(hits), "margin_min": min((round(float(g["margin"]), 4) for g in hits), default=None),
            "reason_text_negative_margin": sum(1 for t in texts if "margin -" in t)}


def d_borderline_face_roi(ev: Event) -> Row:
    sh = [((r.get("scores") or {}).get("sharpness") or {}) for r in ev.worker.values()]
    b = [s for s in sh if s.get("label") == "borderline"]
    bw = [r for r in ev.worker.values() if ((r.get("scores") or {}).get("sharpness") or {}).get("label") == "borderline"]
    return {
        "photos": len(sh), "brisque_null": sum(1 for s in sh if s.get("brisque") is None),
        "borderline": len(b), "borderline_roi": dict(Counter(s.get("roi_used") for s in b)),
        "borderline_lap_var_min": round(min((float(s["lap_var"]) for s in b), default=0.0), 1) if b else None,
        "borderline_fft_hf_max": round(max((float(s["fft_hf"]) for s in b), default=0.0), 2) if b else None,
        "lap_sharp_min": LAP_SHARP_MIN,
        "borderline_verdicts": dict(Counter(r.get("verdict") for r in bw)),
    }


def d_originals_used(ev: Event) -> Row:
    sh = [((r.get("scores") or {}).get("sharpness") or {}) for r in ev.worker.values()]
    chains = [r.get("scores", {}).get("sharpness_chain") or [] for r in ev.worker.values()]
    return {"photos": len(sh), "variant_original": sum(1 for s in sh if s.get("variant") == "original"),
            "chains_with_original_step": sum(1 for c in chains if any("original" in str(x) for x in c)),
            "variant_fallback": sum(1 for s in sh if s.get("variant_fallback"))}


def d_tz_hour(ev: Event) -> Row:
    """Hour used by P6 when EXIF has no offset: clickedAt (naive wall clock stored as Z) + IST vs EXIF hour."""
    n = flips = 0
    shift: Counter[int] = Counter()
    for v in ev.exif.values():
        if v.get("tz_known") or v.get("ts") is None or v.get("local_hour") is None:
            continue
        n += 1
        fb = datetime.fromtimestamp(float(v["ts"]), tz=UTC).astimezone(
            timezone(timedelta(minutes=DEFAULT_TZ_MIN))).hour
        shift[(fb - int(v["local_hour"])) % 24] += 1
        flips += _in_reception(fb) != _in_reception(int(v["local_hour"]))
    return {"photos": len(ev.exif), "tz_unknown": n, "reception_window_flips": flips,
            "hour_shift": dict(shift)}


def d_serial_pauses(ev: Event) -> Row:
    sizes = [len(p.get("items") or []) for r in ev.trace if r.get("type") == "interrupt"
             for p in r.get("pending") or []]
    gaps, last = [], None
    for r in ev.trace:
        if r.get("type") == "run_header":
            last = None
        if r.get("type") == "interrupt_resumed":
            last = r
        elif r.get("type") == "interrupt" and last is not None:
            gaps.append((_ts(r) - _ts(last)).total_seconds())
            last = None
    return {"interrupts": len(sizes), "items_per_interrupt": dict(Counter(sizes)),
            "resume_to_next_pause_s_median": _median(gaps),
            "resume_to_next_pause_s_max": round(max(gaps), 1) if gaps else None,
            "worker_escalations": ev.summary.get("escalation_count")}


def d_answer_sources(ev: Event) -> Row:
    rs = [r for r in ev.rows if r.get("decided_by") not in (None, "worker")]
    return {"answered_rows": len(rs), "decided_by": dict(Counter(str(r.get("decided_by")) for r in rs)),
            "human_source": dict(Counter(str(r.get("human_source")) for r in rs)),
            "needs_approval_rows": sum(1 for r in ev.worker.values() if r.get("needs_approval"))}


def d_precull(ev: Event) -> Row:
    sh = [((r.get("scores") or {}).get("sharpness") or {}) for r in ev.worker.values()]
    lap = [float(s["lap_var"]) for s in sh if s.get("lap_var") is not None]
    return {"photos": len(lap), "lap_var_below_sharp_min": sum(1 for v in lap if v < LAP_SHARP_MIN),
            "blur_codes": sum(1 for r in ev.worker.values() if any(c.startswith("BLUR_") for c in codes(r))),
            "lap_var_p5": round(sorted(lap)[len(lap) // 20], 1) if lap else None}


def d_cross_camera(ev: Event, max_bits: int = 8) -> Row:
    """docs/EVALUATION.md §2 'burst split across cameras': near-identical frames from DIFFERENT cameras, any time offset."""
    by: dict[str, list[str]] = {}
    for p, v in ev.exif.items():
        by.setdefault(str(v.get("model")), []).append(p)
    cams = sorted(by)
    pairs = 0
    for i, a in enumerate(cams):
        for b in cams[i + 1:]:
            for p in by[a]:
                for q in by[b]:
                    d = ham(_phash(ev, p), _phash(ev, q))
                    pairs += d is not None and d <= max_bits
    return {"cameras": len(cams), "cross_camera_pairs_le_8": pairs}


def d_exif_missing(ev: Event) -> Row:
    """docs/EVALUATION.md §2 'missing EXIF': photos whose EXIF has no capture time (grouping falls back to hashes)."""
    return {"photos": len(ev.final), "exif_rows": len(ev.exif),
            "no_capture_time": sum(1 for p in ev.final if (ev.exif.get(p) or {}).get("ts") is None)}


def h_untuned(summary_file: str) -> Callable[[Path], Row]:
    """Historical: verdict/reason counts from a committed first-run summary."""
    def f(root: Path) -> Row:
        d = _json(root / "eval" / "results" / "first-run" / summary_file)
        return {"n_photos": d.get("n_photos"), "verdicts": d.get("verdict_counts"),
                "EXPOSURE_UNDER": (d.get("reason_counts") or {}).get("EXPOSURE_UNDER")}
    return f


# --------------------------------------------------------------------------- registry


@dataclass
class Case:
    id: str
    category: str
    event: str
    title: str
    severity: str
    status: str
    detector: Callable[[Event], Row] | None = None
    historical: Callable[[Path], Row] | None = None
    source: str = ""
    after: str = ""  # cited post-fix numbers when the measured run predates the fix


CATEGORIES = (
    "false review (over-escalation)", "exposure misjudgement", "false reject (risk)", "face/eye misread",
    "missed duplicate / over-split burst", "under-split burst", "sharpness misjudgement",
    "agent tool loop", "data/pipeline", "HITL ergonomics",
)

CASES: list[Case] = [
    Case("C01", CATEGORIES[0], "E07", "P5 applied per runner-up: one human question per near-identical frame",
         "high", "fixed", source="2026-10-05 (E07 diagnosis); RUN# worker_escalations 686"),
    Case("C27", CATEGORIES[0], "E07", "After the fix, P5 still asks a human on most E07 groups", "medium",
         "open (needs labels)", detector=d_group_questions),
    Case("C02", CATEGORIES[1], "E09", "Absolute under-exposure rule fires on a dim indoor venue",
         "high", "fixed", detector=d_exposure_soft_vs_flag, historical=h_untuned("summary_e09_x86_untuned.json"),
         source="eval/results/first-run/summary_e09_x86_untuned.json"),
    Case("C03", CATEGORIES[1], "E07", "Studio flash labelled over-exposed; the soft label still steers best-pick",
         "medium", "flag fixed; best-pick term open", detector=d_exposure_soft_vs_flag,
         source="2026-10-05 (159 EXPOSURE_OVER pre-fix)"),
    Case("C04", CATEGORIES[1], "E09", "Agent's single-photo exposure call has no venue baseline",
         "medium", "open", detector=d_agent_exposure_without_baseline),
    Case("C05", CATEGORIES[2], "E07", "Whole burst with the main subject's eyes closed: runners-up auto-rejected",
         "medium", "open", detector=d_all_closed_groups),
    Case("C06", CATEGORIES[3], "E07", "Small couple-portrait faces give eye state unknown", "medium",
         "by design", detector=d_unknown_eyes),
    Case("C07", CATEGORIES[3], "E09", "Eye state unknown on large, well-lit faces (>= 100 px)", "medium", "open",
         detector=d_unknown_eyes),
    Case("C08", CATEGORIES[3], "E03", "Group photo with several non-main faces closed passes as keep",
         "medium", "partly fixed (group keepers open)", detector=d_multi_closed,
         source="2026-10-09 (commit bd3a04b; team decision on the remainder)",
         after="commit bd3a04b, E07 local rerun on the same inputs: the group-eyes rule now fires on >= 2 closed "
               "non-main faces or >= 1/3 of detected non-main faces (review, never reject; unknown is not closed). "
               "A group keeper still gets no review code: reason_codes.yaml forbids review codes on keep rows, "
               "and the team decided not to change the schema before submission. The E03 9-face keeper still passes."),
    Case("C09", CATEGORIES[4], "E07", "Byte-identical copies coded as BURST_NOT_BEST; copies disagree",
         "low", "fixed", detector=d_exact_dup_coding,
         source="2026-10-09 (C09 76f62b5)",
         after="commit 76f62b5, E07 local rerun on the same inputs (verdicts unchanged 301/624/75): copies not "
               "coded DUPLICATE_OF 139 -> 0, md5 sets with conflicting verdicts 13 -> 0, DUPLICATE_OF rows "
               "65 -> 204 (one judged file per set; copies at confidence 1.0, never asked)."),
    Case("C10", CATEGORIES[4], "E03", "Near-identical singletons just above the pHash threshold", "low",
         "open (not visually confirmed)", detector=d_near_singletons),
    Case("C11", CATEGORIES[4], "E07", "One moment possibly split into adjacent groups", "low",
         "open (not visually confirmed)", detector=d_adjacent_groups),
    Case("C12", CATEGORIES[5], "E07", "Single-link chaining builds a 29-frame group of distinct frames",
         "high", "open", detector=d_wide_groups),
    Case("C13", CATEGORIES[6], "E07", "Face-ROI sharpness ends 'borderline' on very sharp frames", "low",
         "open", detector=d_borderline_face_roi),
    Case("C14", CATEGORIES[7], "probe", "Malformed resume decision: write_decision retried until recursion limit",
         "high", "fixed", source="2026-10-08 (AgentCore probe; recursion root cause, 3b02852)"),
    Case("C15", CATEGORIES[7], "E09", "Pivot detector read 0 pivots from the real agent trace", "medium", "fixed",
         source="2026-10-05 (pivot GAP; commit e708b01: 20 pivots)"),
    Case("C16", CATEGORIES[7], "E09", "Agent wrote the group-eyes code for a closed main face", "medium",
         "open", detector=d_policy_code_misuse),
    Case("C17", CATEGORIES[8], "E07", "Originals never synced: 83 frames NoSuchKey", "high", "fixed",
         source="2026-10-05 (NoSuchKey root cause)"),
    Case("C18", CATEGORIES[8], "E07", "Originals arrive after the worker, so P2 never uses them", "medium",
         "open", detector=d_originals_used),
    Case("C19", CATEGORIES[8], "E09", "EXIF without offset: the reception-hour guard reads the wrong hour",
         "medium", "open", detector=d_tz_hour),
    Case("C20", CATEGORIES[8], "E07", "Worker and agent both wrote RUN# fields", "medium", "fixed",
         source="2026-10-05 (two-writer clash); RUN# run_note"),
    Case("C21", CATEGORIES[8], "E03", "Pre-culled galleries: no borderline-sharpness frames to test on",
         "medium (for claims)", "by design (data)", detector=d_precull),
    Case("C22", CATEGORIES[9], "E07", "Reviewer asked to choose between identical files", "medium", "fixed",
         detector=d_identical_alternatives, source="2026-10-09 (with C09)",
         after="commit 76f62b5, E07 local rerun: groups asking a human about identical files 13 -> 0. The "
               "agent-side duplicate proposals on E09 (3) were on the old E09 local run and are not re-measured."),
    Case("C23", CATEGORIES[9], "E09", "One question per pause; the reviewer waits for the next group",
         "low", "open", detector=d_serial_pauses),
    Case("C24", CATEGORIES[9], "E07", "Kept frame is not the top-ranked frame; text shows a negative margin",
         "low", "open", detector=d_keeper_not_top),
    Case("C25", CATEGORIES[9], "E09", "No real human answer yet: approvals are scripted or team tests",
         "high (for claims)", "open", detector=d_answer_sources),
    Case("C26", CATEGORIES[9], "E09", "Scripted approvals shown as a person; 0.5 decision shown as 100 %",
         "high (for claims)", "fixed", source="2026-10-05 (human_source; commit e708b01)"),
]


# --------------------------------------------------------------------------- run


def _scrub(obj: Any, aliases: dict[str, str]) -> Any:
    """Replace any UUID-looking substring (event ids inside run ids, stray photo ids) by an alias."""
    if isinstance(obj, dict):
        return {k: _scrub(v, aliases) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v, aliases) for v in obj]
    if isinstance(obj, str):
        inv = {v: k for k, v in aliases.items()}
        return UUID.sub(lambda m: inv.get(m.group(0), "<id>"), obj)
    return obj


def run(events: dict[str, Event], repo: Path, aliases: dict[str, str] | None = None,
        event_overrides: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Evaluate every case. `event_overrides` lets a case run on more events (id -> aliases)."""
    out: list[Row] = []
    for c in CASES:
        targets = (event_overrides or {}).get(c.id, [c.event])
        measured: dict[str, Any] = {}
        if c.historical is not None:
            measured["historical"] = c.historical(repo)
        if c.detector is not None:
            for a in targets:
                if a in events:
                    measured[a] = c.detector(events[a])
        out.append({"id": c.id, "category": c.category, "event": c.event, "title": c.title,
                    "severity": c.severity, "status": c.status, "source": c.source,
                    "measured": measured, "kind": "measured" if c.detector else "historical",
                    "after": c.after})
    checks = {a: {"cross_camera": d_cross_camera(e), "exif": d_exif_missing(e), "two_frame_groups": d_adjacent_groups(e)}
              for a, e in events.items()}
    runs = {a: str(e.summary.get("run_id") or "") for a, e in events.items()}
    counts = Counter(c["category"] for c in out)
    doc = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
           "runs": runs, "n_cases": len(out), "per_category": {k: counts.get(k, 0) for k in CATEGORIES},
           "cases": out, "absent_pattern_checks": checks}
    return _scrub(doc, aliases or {})  # type: ignore[no-any-return]


# What happened / why it is wrong or risky / fix or mitigation, per case. Numbers in {braces} are NOT
# templated: every number quoted here is either in the measured block rendered under it (regenerated)
# or cited to a dated source. Keep wording factual; no visual judgement was made (see doc header).
NARRATIVE: dict[str, tuple[str, str, str]] = {
    "C01": ("On the first deployed E07 run P5 (margin < 0.05 -> human) ran once per runner-up. Near-identical "
            "studio bursts (up to 75 frames) produced 686 human questions and 450 reviews out of 1,000; 448 of the "
            "450 were BURST_NOT_BEST + REVIEW_LOW_CONFIDENCE with margin 0.00.",
            "A reviewer would answer the same question once per frame. The review rate made the run useless.",
            "One question per group about the keep slot (keeper + at most 2 rivals), near-identical runners-up "
            "rejected at 0.95. Rerun: 98 questions, 75 reviews (commit 83cbb17)."),
    "C27": ("After the C01 fix the worker still flags more than half of E07's groups for a human, mostly "
            "'N distinct frames within 0.05 for the keep slot'.",
            "Reviewer load. Whether these are genuine near-ties or an over-tight margin cannot be told without the "
            "labelled set.",
            "Tune policy.best_margin_review and best.tie_margin on E07 labels; show all asked groups of an event "
            "on one approvals page instead of one pause each (see C23)."),
    "C02": ("The plan's absolute rule (clip_lo > 40 % and mean L < 45 -> under) put 307 of 661 E09 frames in "
            "review (EXPOSURE_UNDER 315). The venue is dim; faces are lit.",
            "Darkness of the venue is not a defect; half the event went to a human.",
            "under_flag needs the global label AND a dark subject AND frame L < 0.5 x event median (commit "
            "8da974f). The soft label still marks about half of E09 'under', the flag fires once."),
    "C03": ("Studio flash and white backdrops gave 159 EXPOSURE_OVER flags on the pre-fix E07 run. The flag is now "
            "venue-relative (0 flags), but best-pick still scores exposure_ok from the soft label "
            "(ok 1 / under 0 / over 0, weight 0.15).",
            "Inside a mixed group the frame with the 'ok' soft label gains 0.15, a step the venue baseline already "
            "discounted. On E09 one group's top frame is decided by that term alone.",
            "Feed exposure_ok from under_flag/over_flag or from a continuous subject-L score instead of the soft "
            "label. Partly fixed: the reason code is right, the ranking term is not."),
    "C04": ("The agent's score_exposure is a single-photo call with no event baseline (under_flag is null in "
            "its results). On the E09 local run, scripted-approved agent rows added EXPOSURE_UNDER to frames "
            "whose worker under_flag was false (frame L 29 and 42, subject L 91 and 124).",
            "Agent and worker disagree on the same pixels; the agent's code is the one the plan's rule rejected.",
            "Pass the event median to the agent's exposure tool, or return the worker's flag to the agent."),
    "C05": ("In 'every frame failed' groups (main subject's eyes 'closed' in every frame) the keeper "
            "is kept at confidence 0.5 and the group goes to a human, but the other frames are auto-rejected at "
            "0.88 or more with BURST_NOT_BEST + EYES_CLOSED.",
            "The human is asked only about the keep slot. If the closed eyes are intended (a kiss, laughter) or "
            "the classifier is wrong, the runners-up are already rejected. Reversible (nothing is deleted), "
            "but silent.",
            "In all-failed groups put runners-up in review, or show every frame on the approval sheet."),
    "C06": ("39 % of E07 main faces get eye state 'unknown'; two thirds are smaller than 80 px (median 72 px), "
            "where the small-crop and dark-small-face rules apply. The per-face cause is not recorded.",
            "The eyes term in best-pick becomes the neutral 0.5, so closed eyes cannot separate frames of a "
            "couple portrait shot wide.",
            "By design (never reject on unknown). Mitigation: rerun eye state on a full-resolution crop for "
            "unknown faces (faces.detect_retry_on_unknown, off by default: 255 ms per face-free frame)."),
    "C07": ("E09 has unknown eye state on 28 faces of 100 px or more (up to 244 px), including 17 main faces "
            "(median 186 px). Their frames are no darker than frames whose large faces were read.",
            "Size and darkness do not explain it. Decision rows do not keep landmarks, so the cause (missing "
            "YuNet landmarks on reused metadata boxes, or a failed eye crop) cannot be recovered from outputs.",
            "Record the unknown cause per face (no_landmarks | small | dark | crop) in the eyes result."),
    "C08": ("P3 fires REVIEW_POLICY_GROUP_EYES only when exactly one non-main face is closed. An E03 9-face frame "
            "with 5 non-main faces 'closed' was kept with no eye code; E07 has 30 such frames.",
            "A group photo with several blinks passes silently, the opposite of the intended direction.",
            "Fixed for singletons and runners-up: review on >= 2 closed non-main faces or >= 1/3 of them "
            "(bd3a04b). Open for group keepers (schema forbids a review code on a keep row; no change before "
            "submission). Eye states here are the classifier's, not visually confirmed."),
    "C09": ("Byte-identical copies (same md5) in E07 were mostly coded BURST_NOT_BEST, not DUPLICATE_OF, and in "
            "13 md5 sets the judged files disagreed (e.g. one rejected, its identical twin in review).",
            "Two verdicts for the same bytes; the audit trail says 'weaker frame' for an identical file. The "
            "DUPLICATE metric family merges the codes, so metrics are unaffected; the UI is not.",
            "Fixed (76f62b5): one judged file per md5 set, every other copy DUPLICATE_OF at confidence 1.0 "
            "and never asked."),
    "C10": ("Same-camera singleton pairs less than 10 s apart with pHash 11-12, just above phash_max 10 "
            "(blockMeanHash votes only within 3 s).",
            "Possibly missed near-duplicates that each stay 'keep'; possibly genuinely different frames.",
            "Not visually confirmed. Decide with the labelled set whether phash_max 12 or a wider blockMean "
            "window helps purity without merging distinct frames."),
    "C11": ("Consecutive E07 groups less than 10 s apart whose closest frames are within 12 bits.",
            "One moment may be split into two groups, each with its own keeper.",
            "Not visually confirmed. Same remedy as C10, measured by cluster completeness on labels."),
    "C12": ("Single-link union-find chained a 29-frame E07 group spanning 6 s whose pHash diameter is 42 bits "
            "(86 of 406 pairs at 30 bits or more). Across 5 groups, 30 rejects are more than 20 bits from their "
            "keeper, at confidence 0.8 or more.",
            "Visually distinct frames can be auto-rejected as BURST_NOT_BEST of a keeper they do not resemble.",
            "Turn on cluster.refine_enabled (ORB + USAC_MAGSAC + SSIM split for groups > 8, currently false) or cap "
            "the group diameter; until then, review rejects far from the keeper."),
    "C13": ("83 E07 frames are labelled 'borderline' on the faces ROI although lap_var is at least 696 (sharp "
            "threshold 120): fft_hf on small face crops stays below 10 and BRISQUE is not used on face ROIs, so "
            "the votes disagree.",
            "The label is wrong and lowers confidence; it did not cause a reject on its own (those 71 rejects are "
            "burst runners-up). The BRISQUE value is null in every decision row of all three runs.",
            "Scale fft_hf_blurry_max with ROI size or drop the FFT vote below a minimum ROI; ship the BRISQUE "
            "model files in the worker image or document 'BRISQUE off'."),
    "C14": ("AgentCore probe: a resume decision with `action` instead of `human_verdict` was accepted; "
            "write_decision([]) raised and the planner retried identically (no retry cap; tool errors reset the "
            "blocked-call counter) until Python's recursion limit (about 320 Strands turns), on AgentCore and "
            "the local HTTP runtime.",
            "A typo in a reviewer payload crashed the agent instead of returning an error.",
            "validate_decisions before every resume ({\"status\":\"error\"}, run stays paused), retry cap on "
            "write_decision, failures count toward the six-in-a-row stop, 80-turn chunks (commit 3b02852)."),
    "C15": ("The API's pivot detector found 0 pivots in the real E09 trace: tool results are per_photo lists "
            "and make_contact_sheet sits between the cause and request_human_approval.",
            "The UI's evidence for 'the agent changed course because of a tool result' was empty on real data.",
            "Detector reads per_photo results and skips helper tools; real run now shows 20 pivots (commit e708b01)."),
    "C16": ("On the E09 local run the agent re-coded 3 frames the worker had rejected as DUPLICATE_OF + "
            "EYES_CLOSED (main face closed, e.g. p_open 0.06/0.04) to review with REVIEW_POLICY_GROUP_EYES, a code "
            "defined for one closed NON-main face. The scripted reviewer approved them.",
            "The delivered reason code describes a different defect; reason metrics would bucket it wrongly.",
            "write_decision checks codes against per-face evidence (main closed -> EYES_CLOSED)."),
    "C17": ("83 E07 frames failed with NoSuchKey on the first deployed run: the worker fetched originals for "
            "borderline frames and originals/<E07>/ had never been synced.",
            "Partial run; the failed frames had no verdict.",
            "Missing original falls back to the processed image; ingest fails fast when raw/ < manifest; "
            "CullEvent runs PlanOriginals + SyncOriginals (commit 946d07c, commit 80b1410)."),
    "C18": ("No sharpness result in any of the three runs used the original: CullEvent copies originals after "
            "the worker (between worker and agent), and the agent's sharpness calls also ran on processed images.",
            "P2's last step (re-score on the original) never runs; the 870 originals copied for E07 are unused.",
            "Copy originals for group candidates before the worker's P2 pass, or let the agent request "
            "variant=original. Low impact today: these galleries have no borderline frames (C21)."),
    "C19": ("E09 and E07 EXIF has no OffsetTimeOriginal. local_hour() then takes clickedAt (camera wall time "
            "stored with a Z) and adds IST, so the hour is 5-6 h late: the reception-hour window flips for about "
            "half of both events. Seen directly in the agent trace: hour 1 for a photo whose EXIF says 20:00; the "
            "worker calls the same local_hour() but does not store the hour.",
            "P6 (artistic-intent guard) reads the wrong hour. No verdict changed on these runs (no motion-blur "
            "frames), so this is latent.",
            "When the offset is unknown, use the EXIF wall-clock hour as local time instead of shifting it."),
    "C20": ("Worker and agent both upserted RUN# started_at/ended_at/escalation_count on E07: ended_at < "
            "started_at and 686 'escalations' that were the worker's flags.",
            "Run banner and metrics showed a negative duration and a wrong escalation count.",
            "started_at via if_not_exists, worker_*/agent_* namespaced fields, ended_at only when terminal "
            "(commit 1597284, commit 946d07c)."),
    "C21": ("All three events are pre-culled by the photographer: no frame has lap_var below 120 and there are "
            "0 BLUR_* codes.",
            "Blur recall and the P2 face-ROI rescore cannot be measured or demonstrated on this data.",
            "By design of the data. Needs a team-owned test shoot or a labelled degraded copy "
            "(docs/EVALUATION.md §6)."),
    "C22": ("13 E07 groups ask the reviewer to choose between tied candidates of which two are the same file "
            "(same md5). On E09 the agent asked 3 questions whose keep and review proposals were identical "
            "files, and said so in its own reasoning.",
            "Wasted reviewer time and a confusing choice between two identical thumbnails.",
            "Fixed with C09 (76f62b5): copies are DUPLICATE_OF before ranking, so tied candidates are distinct "
            "files."),
    "C23": ("Every agent pause carries exactly one item. After a resume the next pause comes about 40-50 s later "
            "(10 pauses on the E09 local run, 7 across the E03 runs).",
            "The reviewer answers one group, then waits for the agent to reach the next one.",
            "Collect all approval items of a batch before interrupting, or let the API queue worker-flagged "
            "groups directly."),
    "C24": ("When the top-ranked frame has a defect (e.g. EYES_CLOSED) the next frame is kept; the margin is then "
            "negative and the reason text says 'margin -0.xx'.",
            "The behaviour is right; the text reads like an error to a photographer.",
            "Report the margin against the best non-defective rival and say 'top frame skipped: eyes closed'."),
    "C25": ("No escalation has a photographer's answer: E09's answers are scripted, earlier E03 answers were "
            "team mechanism tests (human_source lead_test), E07 is waiting.",
            "Any 'human agreed with the agent' figure would be invented.",
            "Agreement counts only human_source ui|file|http. Needs a real reviewer session."),
    "C26": ("Scripted approvals rendered as 'Decided by a person', and a 0.5 decision showed 100 % confidence.",
            "Misleading provenance in the judge-facing UI.",
            "human_source / decided_by=scripted|lead_test stamped and rendered distinctly; confidence shows the "
            "agent's number (2026-10-05)."),
}


STATUS_GROUPS = ("fixed", "partly fixed", "open", "by design")


def status_group(status: str) -> str:
    s = status.lower()
    if s.startswith("fixed"):
        return "fixed"
    if "fixed" in s:
        return "partly fixed"
    return "by design" if s.startswith("by design") else "open"


# Cases that are also measured on the other events (same detector), to show the pattern is not one-off.
DEFAULT_OVERRIDES: dict[str, list[str]] = {
    "C27": ["E07", "E03", "E09"], "C02": ["E09"], "C03": ["E07", "E09", "E03"], "C05": ["E07", "E03"], "C06": ["E07", "E03"],
    "C07": ["E09", "E07"], "C08": ["E03", "E07", "E09"], "C09": ["E07", "E09"], "C10": ["E03", "E07", "E09"],
    "C11": ["E07"], "C12": ["E07"], "C13": ["E07", "E03", "E09"], "C18": ["E07", "E03", "E09"],
    "C19": ["E09", "E07", "E03"], "C21": ["E03", "E07", "E09"], "C22": ["E07", "E09"],
    "C23": ["E09", "E03"], "C24": ["E07", "E03"], "C25": ["E09", "E03", "E07"],
}


def _fmt(v: Any) -> str:
    if isinstance(v, dict):
        return ", ".join(f"{k} {_fmt(x)}" for k, x in v.items())
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def to_markdown(doc: dict[str, Any]) -> str:
    L = ["# Failure-case evidence (generated)", "",
         f"Generated {doc['generated_at']} by `eval/failure_cases.py` from the runs: "
         + ", ".join(f"{a} `{r}`" for a, r in sorted(doc["runs"].items())) + ". No ids, no images.", "",
         "| Category | Cases |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in doc["per_category"].items()]
    L += ["", "| Case | Category | Event | Status | Measured (per event) |", "|---|---|---|---|---|"]
    for c in doc["cases"]:
        parts = []
        for ev, m in c["measured"].items():
            flat = {k: v for k, v in m.items() if k != "examples"}
            parts.append(f"**{ev}**: {_fmt(flat)}")
        if c["source"]:
            parts.append(f"source: {c['source']}")
        L.append(f"| {c['id']} {c['title']} | {c['category']} | {c['event']} | {c['status']} | "
                 + "<br>".join(parts).replace("|", "/") + " |")
    L += ["", "Absent-pattern checks (docs/EVALUATION.md §2 categories with no instance in these runs):", ""]
    L += [f"- **{a}**: {_fmt(v['cross_camera'])}; {_fmt(v['exif'])}; {_fmt(v['two_frame_groups'])}"
          for a, v in sorted(doc.get("absent_pattern_checks", {}).items())]
    return "\n".join(L) + "\n"


def _measured_lines(c: Row) -> list[str]:
    out = []
    for ev, m in c["measured"].items():
        flat = {k: v for k, v in m.items() if k != "examples"}
        out.append(f"  - {ev}: {_fmt(flat)}")
        for ex in (m.get("examples") or [])[:2]:
            out.append(f"    - example: {_fmt(ex)}")
    return out


def top_categories(doc: dict[str, Any], n: int = 3) -> list[tuple[str, int]]:
    """Categories by number of distinct cases (ties broken by category order), first n distinct counts."""
    order = {k: i for i, k in enumerate(CATEGORIES)}
    ranked = sorted(doc["per_category"].items(), key=lambda kv: (-kv[1], order[kv[0]]))
    return ranked[:n]


def top_sentence(doc: dict[str, Any]) -> str:
    """'a (5), b (5), then a 4-way tie at 3: c, d, e and f' - ties are named, never cut arbitrarily."""
    order = {k: i for i, k in enumerate(CATEGORIES)}
    by_n: dict[int, list[str]] = {}
    for k, v in doc["per_category"].items():
        by_n.setdefault(int(v), []).append(k)
    parts, shown = [], 0
    for n in sorted(by_n, reverse=True):
        names = sorted(by_n[n], key=order.__getitem__)
        if shown and len(names) > 1:
            parts.append(f"then a {len(names)}-way tie at {n}: " + ", ".join(names[:-1]) + f" and {names[-1]}")
        else:
            parts.append(", ".join(f"{k} ({n})" for k in names))
        shown += len(names)
        if shown >= 3:
            break
    return ", ".join(parts)


def to_doc(doc: dict[str, Any]) -> str:
    """docs/FAILURE_CASES.md: the human-readable registry, generated from the same run as failure_cases.json."""
    st = Counter(status_group(c["status"]) for c in doc["cases"])
    L = [
        "# Failure-case catalogue",
        "",
        "<!-- GENERATED by eval/failure_cases.py --doc. Do not edit by hand: change CASES / NARRATIVE in that file",
        "     and regenerate. -->",
        "",
        f"{doc['n_cases']} real failure and limitation cases of KeepFrame, from the three real event runs. Regenerate "
        "(numbers, this file and `eval/results/failure_cases.{json,md}`) with:",
        "",
        "```bash",
        "# per event: aws s3 cp --recursive s3://<work-bucket>/results/<eventId>/ <root>/E07/ \\",
        "#     --exclude '*' --include '*.json' --include '*.jsonl'",
        "python eval/failure_cases.py --root <root> --event E03 --event E07 --event E09 --doc docs/FAILURE_CASES.md",
        "```",
        "",
        "Runs: " + ", ".join(f"{a} `{r}`" for a, r in sorted(doc["runs"].items())) + f". Generated {doc['generated_at']}.",
        "",
        "How to read it:",
        "",
        "- Event aliases only (E03 Ceremony, E07 Engagement, E09 Birthday). Example photos are per-case aliases "
        "(C08-p1, …); no event, photo or group ids and no images are in this file.",
        "- **measured** cases are recomputed from `decisions.jsonl`, `groups.json`, `hashes.json`, "
        "`exif_index.json`, `summary.json` and `trace.jsonl`. **historical** cases happened on runs whose outputs "
        "were later replaced; their numbers cite a committed summary or a dated, recorded decision.",
        "- Counts are cases found, not error rates. Rates need the labelled set (`docs/EVALUATION.md`).",
        "- Fixes newer than these runs: where a case was fixed after the run in S3 (C08, C09, C22), the measured "
        "block shows the run before the fix and an 'After the fix' line cites the fix's own before/after numbers. "
        "Regenerating after the next deployed run replaces the measured block.",
        "- No visual judgement. The proxies and contact sheets could not be viewed in the session that built this "
        "catalogue, so every case rests on scores, hashes, md5 identity and traces. Cases marked "
        "'not visually confirmed' are numeric candidates only. Eye states are the classifier's output.",
        "- Thumbnails: none. Per the publication rule (`docs/RESPONSIBLE_USE.md`) any thumbnail added later must be "
        "face-blurred with `blur_faces_for_publication`.",
        "",
        "## Summary",
        "",
        "| Category | Cases | Fixed | Partly fixed | Open | By design |",
        "|---|---|---|---|---|---|",
    ]
    for cat in CATEGORIES:
        cs = [c for c in doc["cases"] if c["category"] == cat]
        g = Counter(status_group(c["status"]) for c in cs)
        L.append(f"| {cat} | {len(cs)} | " + " | ".join(str(g.get(x, 0)) for x in STATUS_GROUPS) + " |")
    L.append(f"| **total** | **{doc['n_cases']}** | " + " | ".join(f"**{st.get(x, 0)}**" for x in STATUS_GROUPS) + " |")
    L += ["", "Most frequent categories by number of distinct cases: " + top_sentence(doc) + ".", ""]
    L += ["| Case | Category | Event | Severity | Status |", "|---|---|---|---|---|"]
    L += [f"| {c['id']} {c['title']} | {c['category']} | {c['event']} | {c['severity']} | {c['status']} |"
          for c in doc["cases"]]
    for cat in CATEGORIES:
        cs = [c for c in doc["cases"] if c["category"] == cat]
        if not cs:
            continue
        L += ["", f"## {cat}"]
        for c in cs:
            what, why, fix = NARRATIVE.get(c["id"], ("", "", ""))
            L += ["", f"### {c['id']}: {c['title']}", "",
                  f"Event {c['event']} · severity {c['severity']} · status **{c['status']}** · {c['kind']}", "",
                  f"- **What the pipeline did:** {what}", f"- **Why it is wrong or risky:** {why}",
                  f"- **Fix or mitigation:** {fix}"]
            ml = _measured_lines(c)
            if ml:
                label = "Measured (run before the fix)" if c.get("after") else "Measured"
                L += [f"- **{label}:**", *ml]
            if c.get("after"):
                L.append(f"- **After the fix (cited, not yet in the S3 results):** {c['after']}")
            if c["source"]:
                L.append(f"- **Source:** {c['source']}")
    L += ["", "## Patterns checked and not found", "",
          "docs/EVALUATION.md §2 categories with no instance in these three runs (checked, so not catalogued as cases):", ""]
    for a, v in sorted(doc.get("absent_pattern_checks", {}).items()):
        L.append(f"- {a}: {_fmt(v['cross_camera'])}; {_fmt(v['exif'])}; {_fmt(v['two_frame_groups'])}")
    L += ["- Burst split across cameras: 0 cross-camera pairs within 8 pHash bits in E03 and E09 (2 cameras each); "
          "E07 has one camera.",
          "- Missing EXIF: every photo of the three events has a capture time.",
          "- HEIC originals: every manifest original_key is .jpg in all three events. Watermarked processed "
          "images: not checked (no watermark detector exists; file names carry no marker).",
          "- Two-frame groups on E09 and E03 (49 of 50 and 25 of 27 groups): no adjacent groups there come within "
          "12 pHash bits, so there is no sign of over-splitting; the team judged E09's sizes genuine "
          "(2026-10-05).",
          "- Intentional closed eyes, artistic motion blur, bokeh portraits, glasses glare: plausible, but they need "
          "visual review or labels to identify; none is claimed here.", ""]
    return "\n".join(L)


def _event_dirs(root: Path, names: list[str], aliases: dict[str, str]) -> dict[str, Path]:
    out = {}
    for a in names:
        for cand in (root / a, root / aliases.get(a, a)):
            if (cand / "decisions.jsonl").exists():
                out[a] = cand
                break
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True, help="dir with <alias>/ or <eventId>/ result folders")
    ap.add_argument("--event", action="append", default=[], help="event alias (repeatable)")
    ap.add_argument("--out", type=Path, default=HERE / "results")
    ap.add_argument("--doc", type=Path, default=None, help="also write the human-readable registry here")
    a = ap.parse_args(argv)
    aliases = load_aliases()
    dirs = _event_dirs(a.root, a.event or ["E03", "E07", "E09"], aliases)
    events = {al: load_event(d, al) for al, d in dirs.items()}
    doc = run(events, HERE.parent, aliases, DEFAULT_OVERRIDES)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "failure_cases.json").write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n")
    (a.out / "failure_cases.md").write_text(to_markdown(doc))
    if a.doc:
        a.doc.write_text(to_doc(doc))
    print(json.dumps(doc["per_category"]), f"-> {a.out}/failure_cases.{{json,md}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

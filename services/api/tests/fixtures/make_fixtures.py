"""Generate a synthetic LOCAL_MODE results folder: one fake event, 3 groups, 10 photos, a trace with the
three docs/AGENT.md "Trace schema" "vision changed the next action" moments, and one pending approval.

Images are abstract geometric scenes drawn with OpenCV 5 at generation time (no faces, no real photos),
so nothing customer-derived ever enters the repo. Usage:

    python -m tests.fixtures.make_fixtures [OUT_DIR]     # default tests/fixtures/out
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

EVENT_ID = "0b6f3c1e-5a7d-4e2b-9c11-5f0e2a7d9b01"  # synthetic uuid, not a real event
RUN_ID = "run_01J9XSYNTH0000000000000000"
STOCK_RUN_ID = "run_01J9WSYNTH0000000000000000"
OPENCV = "5.0.0"
CV2_FILE = "/usr/local/lib/python3.12/site-packages/cv2/cv2.abi3.so"
PLANNER = "claude-sonnet-5"

# group → members (capture order). One singleton completes the 10 photos.
GROUPS: dict[str, list[str]] = {
    "g_410": ["p_8810", "p_8811", "p_8812"],
    "g_411": ["p_8820", "p_8821"],
    "g_412": ["p_8830", "p_8831", "p_8832", "p_8833"],
}
SINGLETON = "p_8840"

# photo → (verdict, reason codes, confidence, reason text, blur sigma, exposure gain, hue)
PHOTOS: dict[str, tuple[str, list[str], float, str, float, float, int]] = {
    "p_8810": (
        "keep",
        ["KEEP_BEST_OF_GROUP"],
        0.93,
        "Sharpest of 3 (Laplacian 212); exposure clean",
        0.0,
        1.0,
        18,
    ),
    "p_8811": ("reject", ["BURST_NOT_BEST"], 0.88, "Burst frame 0.21 below the group best", 1.6, 1.0, 18),
    "p_8812": ("reject", ["BLUR_MOTION"], 0.91, "Motion blur: Laplacian 31, FFT high-freq 2.4", 5.0, 1.0, 18),
    "p_8820": ("keep", ["KEEP_BEST_OF_GROUP"], 0.86, "Best of 2; highlights 1.2% clipped", 0.0, 1.05, 104),
    "p_8821": ("reject", ["EXPOSURE_OVER"], 0.84, "Highlights 23% clipped (limit 8%)", 0.0, 1.9, 104),
    "p_8830": ("reject", ["BLUR_FOCUS"], 0.82, "Focus missed: face ROI Laplacian 44", 3.2, 1.0, 140),
    "p_8831": (
        "review",
        ["REVIEW_LOW_CONFIDENCE"],
        0.6,
        "Within 0.03 of the best; one face eyes closed",
        0.4,
        1.0,
        140,
    ),
    "p_8832": ("reject", ["BURST_NOT_BEST"], 0.8, "Burst frame 0.20 below the group best", 1.2, 0.8, 140),
    "p_8833": (
        "review",
        ["REVIEW_LOW_CONFIDENCE"],
        0.6,
        "Proposed best, margin 0.03 < 0.05: needs a human",
        0.0,
        1.0,
        140,
    ),
    "p_8840": (
        "keep",
        ["KEEP_SINGLETON"],
        0.9,
        "Singleton; sharp (saliency ROI) and well exposed",
        0.0,
        0.95,
        60,
    ),
}

RANKINGS = {
    "g_410": [("p_8810", 0.84), ("p_8811", 0.63), ("p_8812", 0.22)],
    "g_411": [("p_8820", 0.79), ("p_8821", 0.41)],
    "g_412": [("p_8833", 0.81), ("p_8831", 0.78), ("p_8832", 0.61), ("p_8830", 0.40)],
}


def scene(seed: int, hue: int, blur: float, gain: float, w: int = 1024, h: int = 683) -> np.ndarray:
    rng = np.random.default_rng(seed)
    hsv = np.zeros((h, w, 3), np.uint8)
    hsv[..., 0] = hue
    hsv[..., 1] = np.linspace(60, 150, h, dtype=np.uint8)[:, None]
    hsv[..., 2] = np.linspace(210, 120, h, dtype=np.uint8)[:, None]
    img = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    for _ in range(9):
        c = tuple(int(x) for x in rng.integers(30, 230, 3))
        center = (int(rng.integers(80, w - 80)), int(rng.integers(120, h - 60)))
        if rng.random() < 0.5:
            cv2.circle(img, center, int(rng.integers(30, 120)), c, -1, cv2.LINE_AA)
        else:
            dx, dy = int(rng.integers(40, 180)), int(rng.integers(40, 160))
            cv2.rectangle(img, (center[0] - dx, center[1] - dy), (center[0] + dx, center[1] + dy), c, -1)
    for y in range(0, h, 24):  # fine texture so sharpness differences are real
        cv2.line(img, (0, y), (w, y + 12), (240, 240, 240), 1, cv2.LINE_AA)
    if blur > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    return np.clip(img.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def contact_sheet(tiles: list[np.ndarray], labels: list[str]) -> np.ndarray:
    th = 256
    cells = []
    for img, label in zip(tiles, labels, strict=True):
        t = cv2.resize(img, (int(img.shape[1] * th / img.shape[0]), th), interpolation=cv2.INTER_AREA)
        t = cv2.copyMakeBorder(t, 6, 34, 6, 6, cv2.BORDER_CONSTANT, value=(250, 250, 248))
        cv2.putText(
            t, label, (10, t.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (30, 30, 30), 1, cv2.LINE_AA
        )
        cells.append(t)
    return cv2.hconcat(cells)


def ts(minute: int, second: float) -> str:
    return f"2026-10-12T09:{minute:02d}:{second:06.3f}Z"


def scores_for(pid: str) -> dict[str, Any]:
    verdict, codes, conf, _, blur, gain, _ = PHOTOS[pid]
    lap = round(max(18.0, 230.0 - blur * 42.0), 1)
    return {
        "lap_var": lap,
        "tenengrad": round(lap * 14.2, 1),
        "fft_hf": round(lap / 15.0, 1),
        "brisque": round(28 + blur * 6.5, 1),
        "clip_lo_pct": 0.4,
        "clip_hi_pct": round(max(0.0, (gain - 1.0) * 25), 1),
        "mean_L": round(58 * gain, 1),
        "sharpness_label": "sharp" if lap > 120 else ("borderline" if lap >= 60 else "blurry"),
        "exposure_label": "over" if gain > 1.5 else "ok",
        "n_faces": 3 if pid.startswith("p_883") else 0,
        "faces": faces_for(pid),
    }


def faces_for(pid: str) -> list[dict[str, Any]]:
    """Per-face scores in the real-data shape: source is only frsInfo or yunet; eye probabilities are null
    when state is unknown; fiqa is null when SFace alignment fails."""
    if not pid.startswith("p_883"):
        return []
    closed = pid == "p_8831"
    return [
        {
            "face_id": "f0",
            "source": "frsInfo",
            "min_px": 212,
            "det_score": 0.98,
            "state": "open",
            "left_open_p": 0.97,
            "right_open_p": 0.95,
            "fiqa": 0.71,
            "lap_var": 188.9,
            "main_subject": True,
        },
        {
            "face_id": "f1",
            "source": "frsInfo",
            "min_px": 148,
            "det_score": 0.95,
            "state": "closed" if closed else "open",
            "left_open_p": 0.12 if closed else 0.91,
            "right_open_p": 0.09 if closed else 0.88,
            "fiqa": 0.64,
            "lap_var": 151.2,
            "main_subject": False,
        },
        {
            "face_id": "f2",
            "source": "yunet",
            "min_px": 50,
            "det_score": 0.91,
            "state": "unknown",
            "left_open_p": None,
            "right_open_p": None,
            "fiqa": None,
            "lap_var": 61.0,
            "main_subject": False,
        },
    ]


def trace_lines() -> list[dict[str, Any]]:
    """seq 1–16: groups g_410/g_411 and the singleton; seq 17–28: docs/AGENT.md "Trace schema" verbatim for g_412."""
    r = RUN_ID
    sheet_412 = f"sheets/{EVENT_ID}/g_412.jpg"
    lines: list[dict[str, Any]] = [
        {"type": "tool_call", "tool": "ingest_event", "args": {"event_id": EVENT_ID}, "model": PLANNER},
        {
            "type": "tool_result",
            "tool": "ingest_event",
            "result": {
                "n_photos": 10,
                "n_with_original": 4,
                "n_with_frs": 4,
                "cameras": ["cam_a"],
                "time_span": "00:41",
            },
            "opencv": OPENCV,
            "cv2_file": CV2_FILE,
            "latency_ms": 54,
        },
        {
            "type": "tool_call",
            "tool": "cluster_near_duplicates",
            "args": {"event_id": EVENT_ID, "phash_max_distance": 10, "time_window_s": 10},
        },
        {
            "type": "tool_result",
            "tool": "cluster_near_duplicates",
            "result": {
                "n_groups": 3,
                "groups": [{"group_id": g, "photo_ids": m, "size": len(m)} for g, m in GROUPS.items()],
                "singletons": 1,
            },
            "latency_ms": 112,
        },
        {
            "type": "model_reasoning",
            "text": "3 groups and 1 singleton. Policy 1: singleton p_8840 gets saliency sharpness + exposure only.",
        },
        {
            "type": "tool_call",
            "tool": "score_sharpness",
            "args": {"photo_id": SINGLETON, "roi": "saliency", "variant": "processed"},
        },
        {
            "type": "tool_result",
            "tool": "score_sharpness",
            "result": {
                "lap_var": 230.0,
                "tenengrad": 3266.0,
                "fft_hf": 15.3,
                "brisque": 28.0,
                "label": "sharp",
                "confidence": 0.94,
            },
            "latency_ms": 33,
        },
        {"type": "tool_call", "tool": "pick_best_in_cluster", "args": {"group_id": "g_410"}},
        {
            "type": "tool_result",
            "tool": "pick_best_in_cluster",
            "result": {
                "best_photo_id": "p_8810",
                "tie": False,
                "margin": 0.21,
                "ranking": [{"photo_id": p, "score": s} for p, s in RANKINGS["g_410"]],
            },
            "latency_ms": 41,
        },
        {
            "type": "model_reasoning",
            "text": "g_410 margin 0.21 is clear. Keep p_8810; p_8812 shows motion blur (Laplacian 31).",
        },
        {"type": "tool_call", "tool": "score_exposure", "args": {"photo_id": "p_8821"}},
        {
            "type": "tool_result",
            "tool": "score_exposure",
            "result": {
                "clip_lo_pct": 0.4,
                "clip_hi_pct": 22.5,
                "mean_L": 110.2,
                "std_L": 31.0,
                "entropy": 6.1,
                "label": "over",
            },
            "latency_ms": 12,
        },
        {"type": "tool_call", "tool": "pick_best_in_cluster", "args": {"group_id": "g_411"}},
        {
            "type": "tool_result",
            "tool": "pick_best_in_cluster",
            "result": {
                "best_photo_id": "p_8820",
                "tie": False,
                "margin": 0.38,
                "ranking": [{"photo_id": p, "score": s} for p, s in RANKINGS["g_411"]],
            },
            "latency_ms": 18,
        },
        {
            "type": "tool_call",
            "tool": "write_decision",
            "args": {
                "event_id": EVENT_ID,
                "decisions": [
                    {
                        "photo_id": p,
                        "verdict": PHOTOS[p][0],
                        "reason_codes": PHOTOS[p][1],
                        "confidence": PHOTOS[p][2],
                        "group_id": g,
                        "decided_by": "agent",
                    }
                    for g in ("g_410", "g_411")
                    for p in GROUPS[g]
                ],
            },
        },
        {
            "type": "tool_result",
            "tool": "write_decision",
            "result": {"written": 5, "verdict_counts": {"keep": 2, "reject": 3}},
            "latency_ms": 22,
        },
        # ---- docs/AGENT.md "Trace schema", seq 17–28 (synthetic ids) ----
        {
            "type": "tool_call",
            "tool": "score_sharpness",
            "args": {"photo_id": "p_8831", "roi": "full", "variant": "processed"},
            "model": PLANNER,
        },
        {
            "type": "tool_result",
            "tool": "score_sharpness",
            "result": {
                "lap_var": 74.2,
                "tenengrad": 1130.5,
                "fft_hf": 9.1,
                "brisque": 41.3,
                "label": "borderline",
                "confidence": 0.55,
            },
            "opencv": OPENCV,
            "cv2_file": CV2_FILE,
            "latency_ms": 38,
        },
        {
            "type": "model_reasoning",
            "text": "Sharpness is borderline on the full frame and detect_faces found 2 faces (main face 212 px). Policy 2: re-score on the face ROI before labelling.",
        },
        {
            "type": "tool_call",
            "tool": "score_sharpness",
            "args": {"photo_id": "p_8831", "roi": "faces", "variant": "processed"},
        },
        {
            "type": "tool_result",
            "tool": "score_sharpness",
            "result": {
                "lap_var": 188.9,
                "tenengrad": 2890.0,
                "fft_hf": 14.6,
                "brisque": None,
                "label": "sharp",
                "confidence": 0.92,
            },
            "latency_ms": 21,
        },
        {
            "type": "tool_call",
            "tool": "score_eyes_open",
            "args": {"photo_id": "p_8831", "face_ids": ["f0", "f1", "f2"]},
        },
        {
            "type": "tool_result",
            "tool": "score_eyes_open",
            "result": {
                "per_face": [
                    {"face_id": "f0", "left_open_p": 0.97, "right_open_p": 0.95, "state": "open"},
                    {"face_id": "f1", "left_open_p": 0.12, "right_open_p": 0.09, "state": "closed"},
                    # Real-data shape: small faces (~50 px) come back unknown with null probabilities.
                    {"face_id": "f2", "left_open_p": None, "right_open_p": None, "state": "unknown"},
                ],
                "any_main_subject_closed": False,
                "confidence": 0.9,
            },
            "latency_ms": 9,
        },
        {
            "type": "model_reasoning",
            "text": "Main subject f0 open, secondary f1 closed; group g_412 has 4 frames. Policy 1/3: compare within the group rather than reject this frame outright.",
        },
        {"type": "tool_call", "tool": "pick_best_in_cluster", "args": {"group_id": "g_412"}},
        {
            "type": "tool_result",
            "tool": "pick_best_in_cluster",
            "result": {
                "best_photo_id": "p_8833",
                "tie": False,
                "margin": 0.03,
                "ranking": [{"photo_id": p, "score": s} for p, s in RANKINGS["g_412"]],
            },
            "latency_ms": 43,
        },
        {
            "type": "model_reasoning",
            "text": "Margin 0.03 < 0.05 (Policy 5). Escalate p_8833 vs p_8831 with a contact sheet.",
        },
        {
            "type": "tool_call",
            "tool": "request_human_approval",
            "args": {
                "items": [
                    {
                        "group_id": "g_412",
                        "proposed": {"keep": "p_8833", "reject": ["p_8830", "p_8832"], "review": ["p_8831"]},
                        "reason": "Top two frames within 0.03; f1 eyes closed in p_8831",
                        "confidence": 0.6,
                        "contact_sheet_key": sheet_412,
                    }
                ]
            },
        },
    ]
    out = []
    for seq, line in enumerate(lines, start=1):
        minute, second = (14, 1.0 + seq * 0.13) if seq < 17 else (14, 3.112 + (seq - 17) * 0.03)
        out.append({"run_id": r, "seq": seq, "ts": ts(minute, second), **line})
    return out


def build(out: Path) -> dict[str, Any]:
    s3 = out / "s3"
    ddb = out / "dynamodb"
    for d in (
        s3 / "manifests",
        s3 / "proxy" / EVENT_ID,
        s3 / "sheets" / EVENT_ID,
        s3 / "results" / EVENT_ID,
        s3 / "bench" / "results" / "latest",
        ddb,
    ):
        d.mkdir(parents=True, exist_ok=True)

    images: dict[str, np.ndarray] = {}
    for i, (pid, (_, _, _, _, blur, gain, hue)) in enumerate(PHOTOS.items()):
        group = next((g for g, m in GROUPS.items() if pid in m), pid)
        img = scene(seed=sum(map(ord, group)) * 31 + (i % 2), hue=hue, blur=blur, gain=gain)
        images[pid] = img
        cv2.imwrite(str(s3 / "proxy" / EVENT_ID / f"{pid}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    for g, members in GROUPS.items():
        sheet = contact_sheet([images[m] for m in members], members)
        cv2.imwrite(str(s3 / "sheets" / EVENT_ID / f"{g}.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])

    meta = {
        "event_id": EVENT_ID,
        "name": "Synthetic Garden Ceremony",
        "category": "Ceremony",
        "event_date": "2026-09-20",
        "whitelabel_id": "wl_synthetic_do_not_expose",
        "sort_by": "clickedAt",
        "n_photos": 10,
        "n_with_original": 4,
        "cameras": ["cam_a"],
        "exported_at": "2026-10-05T08:00:00Z",
        "user_id": "u_synthetic_do_not_expose",
    }
    (s3 / "manifests" / f"{EVENT_ID}.meta.json").write_text(json.dumps(meta, indent=2))
    # A second event with no results yet (exercises the empty state and the S3 groups fallback).
    meta2 = {
        **meta,
        "event_id": "7d2a9e44-1c3b-4f6a-8e55-0a9b8c7d6e02",
        "name": "Synthetic Birthday",
        "category": "Birthday",
        "n_photos": 4,
        "exported_at": "2026-10-05T08:30:00Z",
    }
    (s3 / "manifests" / f"{meta2['event_id']}.meta.json").write_text(json.dumps(meta2, indent=2))
    groups_doc = {
        "event_id": meta2["event_id"],
        "groups": [{"group_id": "g_900", "photo_ids": ["p_9001", "p_9002"], "size": 2, "span_s": 1.4}],
        "singletons": ["p_9003", "p_9004"],
    }
    (s3 / "results" / meta2["event_id"]).mkdir(parents=True, exist_ok=True)
    (s3 / "results" / meta2["event_id"] / "groups.json").write_text(json.dumps(groups_doc))

    pk = f"EVENT#{EVENT_ID}"
    decisions: list[dict[str, Any]] = []
    for pid, (verdict, codes, conf, text, *_rest) in PHOTOS.items():
        group = next((g for g, m in GROUPS.items() if pid in m), None)
        item: dict[str, Any] = {
            "pk": pk,
            "sk": f"PHOTO#{pid}",
            "photo_id": pid,
            "verdict": verdict,
            "reason_codes": codes,
            "reason_text": text,
            "confidence": conf,
            "agent_confidence": conf,
            "decided_by": "agent",
            "scores": scores_for(pid),
            "run_id": RUN_ID,
            "updated_at": ts(14, 3.5),
            "gsi1pk": f"EVENT#{EVENT_ID}#VERDICT#{verdict}",
        }
        if group:
            item["group_id"] = group
            item["gsi2pk"] = f"GROUP#{group}"
        if pid == SCRIPTED_PHOTO:
            # Shape of the relabelled 2026-10-05 real-run rows: an auto-approver answered, the row still holds
            # the old 1.0 placeholder in `confidence`, and `agent_confidence` carries the model's value.
            item.update(
                decided_by="scripted",
                human_source="scripted",
                confidence=1,
                agent_confidence=0.55,
                human_note="auto-approved as proposed (scripted smoke run)",
                reason_text="Auto-approved by a script (no human review)",
            )
        if pid == LEAD_TEST_PHOTO:
            item.update(
                decided_by="lead_test",
                human_source="lead_test",
                confidence=1,
                agent_confidence=0.41,
                human_note="resume-path mechanism test",
                reason_text="Decided by a person: resume-path mechanism test",
            )
        decisions.append(item)
    spans = {"g_410": 2.1, "g_411": 0.8, "g_412": 3.4}
    for g, members in GROUPS.items():
        ranking = RANKINGS[g]
        item = {
            "pk": pk,
            "sk": f"GROUP#{g}",
            "group_id": g,
            "members": members,
            "size": len(members),
            "best_photo_id": ranking[0][0],
            "margin": round(ranking[0][1] - ranking[1][1], 2),
            "tie": False,
            "span_s": spans[g],
            "ranking": [{"photo_id": p, "score": s} for p, s in ranking],
            "run_id": RUN_ID,
            "updated_at": ts(14, 3.46),
        }
        if g == "g_412":
            item.update(
                {
                    "gsi3pk": "PENDING",
                    "gsi3sk": f"{ts(14, 3.472)}#{EVENT_ID}#g_412",
                    "approval_status": "pending",
                    "proposed": {"keep": "p_8833", "reject": ["p_8830", "p_8832"], "review": ["p_8831"]},
                    "reason": "Top two frames within 0.03; f1 eyes closed in p_8831",
                    "confidence": 0.6,
                    "contact_sheet_key": f"sheets/{EVENT_ID}/g_412.jpg",
                    "interrupt_id": "intr_synthetic_0001",
                }
            )
        decisions.append(item)
    run_common = {
        "pk": pk,
        "event_id": EVENT_ID,
        "opencv_version": OPENCV,
        "cv2_file": CV2_FILE,
        "instance_type": "c8g.4xlarge",
        "thresholds_sha": "3f9a1c2",
        "gsi1pk": "RUNS",
        "llm_provider": "anthropic",
        "planner_model": "claude-sonnet-5",
        "reasons_model": "claude-haiku-4.5",
    }
    decisions.append(
        {
            **run_common,
            "sk": f"RUN#{RUN_ID}",
            "run_id": RUN_ID,
            "status": "awaiting_approval",
            "flavor": "stock",
            "started_at": "2026-10-12T09:13:40.000Z",
            "updated_at": ts(14, 3.472),
            "verdict_counts": {"keep": 3, "reject": 5, "review": 2},
            "tool_call_count": 14,
            "escalation_count": 1,
            "cost_estimate_usd": 0.042,
            "photos_processed": 10,
        }
    )
    decisions.append(
        {
            **run_common,
            "sk": f"RUN#{STOCK_RUN_ID}",
            "run_id": STOCK_RUN_ID,
            "status": "succeeded",
            "flavor": "stock",
            "cv2_file": "/usr/local/lib/python3.12/site-packages/cv2/cv2.abi3.so",
            "started_at": "2026-10-11T17:02:10.000Z",
            "ended_at": "2026-10-11T17:09:55.000Z",
            "updated_at": "2026-10-11T17:09:55.000Z",
            "verdict_counts": {"keep": 3, "reject": 6, "review": 1},
            "tool_call_count": 13,
            "escalation_count": 1,
            "cost_estimate_usd": 0.039,
            "photos_processed": 10,
        }
    )
    (ddb / "keepframe-decisions.jsonl").write_text("".join(json.dumps(d) + "\n" for d in decisions))

    trace = trace_lines()
    traces = [{"pk": f"RUN#{RUN_ID}", "sk": f"{t['seq']:06d}", "ttl": 1_800_000_000, **t} for t in trace]
    (ddb / "keepframe-traces.jsonl").write_text("".join(json.dumps(t) + "\n" for t in traces))
    (s3 / "results" / EVENT_ID / "trace.jsonl").write_text("".join(json.dumps(t) + "\n" for t in trace))

    summary = {
        "event_id": EVENT_ID,
        "run_id": RUN_ID,
        "verdict_counts": {"keep": 3, "reject": 5, "review": 2},
        "opencv_version": OPENCV,
        "cv2_file": CV2_FILE,
        "flavor": "stock",
        "wall_s": 41.3,
        "thresholds_sha": "3f9a1c2",
    }
    (s3 / "results" / EVENT_ID / "summary.json").write_text(json.dumps(summary))
    (s3 / "results" / EVENT_ID / "decisions.jsonl").write_text(
        "".join(
            json.dumps(
                {k: d[k] for k in ("photo_id", "verdict", "reason_codes", "confidence", "group_id") if k in d}
            )
            + "\n"
            for d in decisions
            if d["sk"].startswith("PHOTO#")
        )
    )
    (s3 / "results" / EVENT_ID / "groups.json").write_text(
        json.dumps(
            {
                "event_id": EVENT_ID,
                "groups": [{"group_id": g, "photo_ids": m, "size": len(m)} for g, m in GROUPS.items()],
                "singletons": [SINGLETON],
            }
        )
    )

    (s3 / "bench" / "results" / "latest" / "results.json").write_text(json.dumps(bench_doc(), indent=2))
    return {"event_id": EVENT_ID, "run_id": RUN_ID, "root": str(out)}


SCRIPTED_PHOTO = "p_8810"
LEAD_TEST_PHOTO = "p_8820"

EVENT3 = "00000000-0000-4000-8000-0000000000e7"
PAUSED_RUN_ID = "run_aws_e7_20261005T170642Z"
BURST = [f"p_e7_{i:02d}" for i in range(30)]


def event3_groups() -> dict[str, Any]:
    groups = [{"group_id": f"g_{EVENT3}_1", "photo_ids": ["p_e7_a", "p_e7_b"], "size": 2, "span_s": 0.0}]
    groups.append(
        {
            "group_id": f"g_{EVENT3}_2",
            "photo_ids": BURST,
            "size": 30,
            "span_s": 6.2,
            "best_photo_id": BURST[7],
        }
    )
    groups += [
        {"group_id": f"g_{EVENT3}_{n}", "photo_ids": [f"p_e7_{n}a", f"p_e7_{n}b"], "size": 2, "span_s": 0.4}
        for n in range(3, 31)
    ]
    return {"event_id": EVENT3, "n_groups": len(groups), "groups": groups, "singletons": ["p_e7_s1"]}


def add_event3(out: Path) -> None:
    """Add the E07-shaped event to a built fixture tree (kept out of build() so counts elsewhere hold)."""
    s3, ddb = out / "s3", out / "dynamodb"
    meta = json.loads((s3 / "manifests" / f"{EVENT_ID}.meta.json").read_text())
    meta3 = {
        **meta,
        "event_id": EVENT3,
        "name": "Synthetic Reception",
        "n_photos": 63,
        "exported_at": "2026-10-05T09:00:00Z",
    }
    (s3 / "manifests" / f"{EVENT3}.meta.json").write_text(json.dumps(meta3, indent=2))
    (s3 / "results" / EVENT3).mkdir(parents=True, exist_ok=True)
    (s3 / "results" / EVENT3 / "groups.json").write_text(json.dumps(event3_groups()))
    with (ddb / "keepframe-decisions.jsonl").open("a") as f:
        for row in event3_rows():
            f.write(json.dumps(row) + "\n")


def event3_rows() -> list[dict[str, Any]]:
    pk = f"EVENT#{EVENT3}"
    stub = {
        "pk": pk,
        "sk": f"GROUP#g_{EVENT3}_1",
        "group_id": f"g_{EVENT3}_1",
        "event_id": EVENT3,
        "run_id": PAUSED_RUN_ID,
        "approval_id": f"g_{EVENT3}_1",
        "approval_status": "pending",
        "gsi3pk": "PENDING",
        "gsi3sk": f"2026-10-05T17:11:17.529Z#{EVENT3}#g_{EVENT3}_1",
        "proposed": {"keep": ["p_e7_a"], "reject": ["p_e7_b"], "review": []},
        "reason": "pick_best_in_cluster tie (margin 0.0)",
        "confidence": 0.55,
        "updated_at": "2026-10-05T17:11:17.529Z",
    }
    # Two writers: the worker stamped ended_at at the end of its pass, then the agent overwrote started_at.
    run = {
        "pk": pk,
        "sk": f"RUN#{PAUSED_RUN_ID}",
        "gsi1pk": "RUNS",
        "run_id": PAUSED_RUN_ID,
        "event_id": EVENT3,
        "status": "awaiting_approval",
        "flavor": "stock",
        "started_at": "2026-10-05T17:10:28.000Z",
        "ended_at": "2026-10-05T17:09:11.000Z",
        "updated_at": "2026-10-05T17:11:17.529Z",
        "tool_call_count": 8,
        "llm_provider": "anthropic",
    }
    calibration = {
        **run,
        "sk": "RUN#run_e7_policy_rerun_2",
        "run_id": "run_e7_policy_rerun_2",
        "status": "succeeded",
        "started_at": "2026-10-05T16:40:00.000Z",
        "ended_at": "2026-10-05T16:41:02.000Z",
        "run_kind": "local_calibration",
        "run_note": "policy rerun 2; S3 results come from this run",
        "llm_provider": "none",
    }
    return [stub, run, calibration]


def bench_doc() -> dict[str, Any]:
    """Synthetic numbers in the published shape, clearly labelled — the real file comes from the benchmark.

    Two stock hosts, the shape published before the 2026-10-08 carotene/KleidiCV bench.
    """
    stages = {  # name: (c8g p50, c8g p95, c7g p50, c7g p95)
        "decode_webp": (30.0, 44.0, 34.5, 50.0),
        "resize_1024_area": (12.0, 12.6, 13.0, 13.4),
        "cvtcolor_gray": (0.21, 0.23, 0.25, 0.27),
        "clahe": (2.4, 2.7, 2.7, 3.0),
        "laplacian_sobel": (6.0, 7.1, 5.8, 7.0),
        "phash_blockmean": (0.28, 0.30, 0.32, 0.34),
        "yunet_1024": (55.0, 58.0, 62.0, 64.5),
        "brisque": (24.5, 26.5, 28.0, 30.0),
        "opencv_total": (131.0, 147.0, 147.0, 165.0),
    }

    def host(label: str, itype: str, ips: float, usd: float, col: int) -> dict[str, Any]:
        return {
            "label": label,
            "instance_type": itype,
            "opencv_version": "5.0.0",
            "cv2_file": "/opt/venv/lib/python3.12/site-packages/cv2/__init__.py",
            "images_per_sec": ips,
            "cost_per_1k_usd": usd,
            "cpu_util_mean": 99.9,
            "peak_rss_mb": 800.0,
            "stages": [{"name": n, "p50_ms": v[col], "p95_ms": v[col + 1]} for n, v in stages.items()],
        }

    return {
        "generated_at": "2026-10-12T09:00:00+00:00",
        "synthetic": True,
        "n_images": 5000,
        "hosts": [
            host("stock", "c8g.xlarge", 27.0, 0.0011, 0),
            host("c7g-stock", "c7g.xlarge", 24.0, 0.0011, 2),
        ],
    }


def legacy_bench_doc() -> dict[str, Any]:
    """The older flat shape (pre-contract) — kept only to test the API's normaliser."""
    return {
        "synthetic": True,
        "date": "2026-10-12",
        "instance_type": "c8g.4xlarge",
        "hosts": [
            {"host_label": "stock", "images_per_s": 212.0, "cpu_util_pct": 91.0, "usd_per_1k": 0.0009},
            {
                "host_label": "stock-kleidicv",
                "images_per_s": 248.0,
                "cpu_util_pct": 90.0,
                "usd_per_1k": 0.0011,
            },
        ],
        "stages": [
            {"stage": "clahe", "host_label": "stock", "p50_ms": 2.4, "p95_ms": 3.3},
            {"stage": "clahe", "host_label": "stock-kleidicv", "p50_ms": 1.3, "p95_ms": 1.9},
        ],
    }


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "out"
    print(json.dumps(build(target)))

"""eval/failure_cases.py on a hand-built synthetic event (ids are synthetic; answers worked out by hand)."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("failure_cases", ROOT / "eval" / "failure_cases.py")
assert spec and spec.loader
fc = importlib.util.module_from_spec(spec)
sys.modules["failure_cases"] = fc  # dataclasses resolve annotations through sys.modules
spec.loader.exec_module(fc)

EV = "00000000-0000-4000-8000-0000000000e1"


def pid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def face(fid: str, state: str, px: int, main: bool, lp: float | None = None, rp: float | None = None) -> dict:
    return {"face_id": fid, "state": state, "min_px": px, "is_main": main, "left_open_p": lp, "right_open_p": rp}


def row(n: int, verdict: str, codes: list[str], *, conf: float = 0.9, by: str = "worker", faces: list | None = None,
        lap: float = 2000.0, label: str = "sharp", roi: str = "faces", fft: float = 30.0,
        exp: dict | None = None, gid: str | None = None, src: str | None = None) -> dict[str, Any]:
    return {"photo_id": pid(n), "event_id": EV, "verdict": verdict, "reason_codes": codes, "confidence": conf,
            "group_id": gid, "decided_by": by, "human_source": src, "run_id": f"run_{EV}_20261009T000000Z",
            "reason_text": "Keep: best frame of its group [margin -0.01 of 2]" if n == 1 else "",
            "needs_approval": n in (1, 2),
            "scores": {"sharpness": {"lap_var": lap, "fft_hf": fft, "brisque": None, "label": label,
                                     "roi_used": roi, "variant": "processed", "variant_fallback": False},
                       "sharpness_chain": ["full:processed", f"{roi}:processed"],
                       "exposure": exp or {"label": "ok", "under_flag": False, "over_flag": False, "mean_L": 120.0},
                       "eyes": {"per_face": faces} if faces is not None else None}}


def build(tmp: Path) -> Path:
    d = tmp / "E99"
    d.mkdir()
    g1 = "g_e99_1"
    closed_main = [face("f0", "closed", 300, True, 0.01, 0.02), face("f1", "open", 140, False, 1.0, 1.0)]
    rows = [
        # group 1: every frame failed (main closed in all); keeper p1 is NOT the top-ranked frame
        row(1, "keep", ["KEEP_BEST_OF_GROUP"], conf=0.5, faces=closed_main, gid=g1),
        row(2, "reject", ["BURST_NOT_BEST", "EYES_CLOSED"], conf=0.95, faces=closed_main, gid=g1),
        row(3, "reject", ["BURST_NOT_BEST"], conf=0.95, faces=closed_main, gid=g1, label="borderline", fft=9.5),
        # singletons: 4 with two closed non-main faces and no eye code; 5 under-labelled but not flagged
        row(4, "keep", ["KEEP_SINGLETON"], faces=[face("f0", "open", 200, True, 1, 1), face("f1", "closed", 90, False, 0, 0),
                                                  face("f2", "closed", 80, False, 0, 0), face("f3", "unknown", 120, False)]),
        row(5, "keep", ["KEEP_SINGLETON"], roi="saliency",
            exp={"label": "under", "under_flag": False, "over_flag": False, "mean_L": 30.0, "subject_L": 110.0}),
        row(6, "keep", ["KEEP_SINGLETON"], roi="saliency"),
        # agent pass: re-writes 5 with EXPOSURE_UNDER (no baseline) and 2 with the group-eyes code
        row(5, "review", ["EXPOSURE_UNDER"], by="scripted", src="scripted"),
        row(2, "review", ["REVIEW_POLICY_GROUP_EYES"], by="agent"),
    ]
    (d / "decisions.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    groups = {"groups": [{"group_id": g1, "members": [pid(1), pid(2), pid(3)], "size": 3, "best_photo_id": pid(1),
                          "margin": -0.01, "tie": False, "needs_approval": True,
                          "approval_reasons": ["every frame failed"], "tied_candidates": [pid(1), pid(2)],
                          "ranking": [{"photo_id": pid(2), "score": 0.6}, {"photo_id": pid(1), "score": 0.59},
                                      {"photo_id": pid(3), "score": 0.4}]}],
              "exact_duplicates": {pid(2): pid(1)}}
    (d / "groups.json").write_text(json.dumps(groups))
    # p5 vs p6: xor = 0x7ff (11 bits) -> just above phash_max 10, both singletons, 1 s apart
    ph = {pid(1): "0" * 16, pid(2): "0" * 16, pid(3): "000000000000ffff", pid(4): "ffffffffffffffff",
          pid(5): "0000000000000000", pid(6): "00000000000007ff"}
    (d / "hashes.json").write_text(json.dumps({k: {"phash": v} for k, v in ph.items()}))
    # EXIF: no offset; naive 23:00 stored as Z -> +05:30 fallback reads 04:30 (outside reception window)
    ex = {pid(n): {"model": "CamA", "ts": 1784415600.0 + n, "local_hour": 23, "tz_known": False} for n in range(1, 7)}
    (d / "exif_index.json").write_text(json.dumps({"event_id": EV, "photos": ex}))
    (d / "summary.json").write_text(json.dumps({"run_id": f"run_{EV}_20261009T000000Z", "escalation_count": 2}))
    trace = [
        {"type": "run_header", "ts": "2026-10-09T00:00:00Z"},
        {"type": "tool_call", "tool": "request_human_approval", "ts": "2026-10-09T00:00:01Z",
         "args": {"items": [{"proposed": {"keep": [pid(1)], "review": [pid(2)]}}]}},
        {"type": "interrupt", "ts": "2026-10-09T00:00:02Z", "pending": [{"items": [{}]}]},
        {"type": "interrupt_resumed", "ts": "2026-10-09T00:01:00Z"},
        {"type": "interrupt", "ts": "2026-10-09T00:01:30Z", "pending": [{"items": [{}]}]},
    ]
    (d / "trace.jsonl").write_text("\n".join(json.dumps(t) for t in trace) + "\n")
    return d


def test_detectors_on_synthetic_event(tmp_path: Path) -> None:
    ev = fc.load_event(build(tmp_path), "E99")
    assert ev.worker[pid(2)]["decided_by"] == "worker" and ev.final[pid(2)]["decided_by"] == "agent"

    c05 = fc.d_all_closed_groups(ev)
    assert (c05["groups"], c05["frames"], c05["auto_rejects"]) == (1, 3, 1)  # p2 now review (agent), p3 reject
    assert c05["auto_reject_conf_min"] == 0.95

    c08 = fc.d_multi_closed(ev)
    assert c08["frames"] == 1 and c08["max_closed"] == 2 and c08["examples"][0]["photo"] == "C08-p1"

    assert fc.d_policy_code_misuse(ev)["rows"] == 1          # agent row for p2: main face closed
    assert fc.d_agent_exposure_without_baseline(ev)["rows_adding_under"] == 1

    c09 = fc.d_exact_dup_coding(ev)
    # md5 set {p1, p2}: neither is DUPLICATE_OF -> 1 copy beyond the judged file; keep vs review -> conflicting
    assert c09 == {"md5_sets": 1, "files_in_sets": 2, "copies_not_DUPLICATE_OF": 1,
                   "sets_with_conflicting_verdicts": 1}

    c22 = fc.d_identical_alternatives(ev)
    assert c22["groups"] == 1 and c22["asked_with_identical_files"] == 1
    assert c22["agent_questions_all_identical"] == 1        # keep p1 / review p2 are the same bytes

    c27 = fc.d_group_questions(ev)
    assert (c27["groups_asked"], c27["groups_asked_pct"], c27["reasons"]) == (1, 100.0, {"every frame failed": 1})

    c24 = fc.d_keeper_not_top(ev)
    assert c24 == {"groups": 1, "margin_min": -0.01, "reason_text_negative_margin": 1}

    c13 = fc.d_borderline_face_roi(ev)
    assert c13["borderline"] == 1 and c13["borderline_roi"] == {"faces": 1} and c13["brisque_null"] == 6

    c06 = fc.d_unknown_eyes(ev)
    assert (c06["unknown"], c06["unknown_ge_100px"], c06["main_unknown"]) == (1, 1, 0)
    assert c06["frame_mean_L_median_unknown_ge_100px"] == 120.0

    c03 = fc.d_exposure_soft_vs_flag(ev)
    assert (c03["label_under"], c03["under_flag"], c03["groups_mixed_soft_label"]) == (1, 0, 0)

    c20 = fc.d_tz_hour(ev)
    assert c20["tz_unknown"] == 6 and c20["reception_window_flips"] == 6 and c20["hour_shift"] == {5: 6}

    c23 = fc.d_serial_pauses(ev)
    assert c23["interrupts"] == 2 and c23["resume_to_next_pause_s_median"] == 30.0

    near = fc.d_near_singletons(ev)  # p4-p5 (64 bits) and p4-p6 too far; p5-p6 = 11 bits
    assert near["pairs"] == 1 and near["phash"] == {11: 1}

    c12 = fc.d_wide_groups(ev)  # p3 is 16 bits from keeper p1: not "far" at > 20 bits
    assert c12["rejects_far_from_keeper"] == 0 and c12["widest_group"]["phash_diameter"] == 16


def test_run_output_has_no_ids(tmp_path: Path) -> None:
    ev = fc.load_event(build(tmp_path), "E99")
    doc = fc.run({"E99": ev}, ROOT, aliases={"E99": EV}, event_overrides={c.id: ["E99"] for c in fc.CASES})
    text = json.dumps(doc) + fc.to_markdown(doc)
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)
    assert doc["runs"]["E99"] == "run_E99_20261009T000000Z"
    assert doc["n_cases"] == len(fc.CASES) >= 15
    assert sum(doc["per_category"].values()) == doc["n_cases"]
    assert set(doc["per_category"]) == set(fc.CATEGORIES)


def test_doc_registry(tmp_path: Path) -> None:
    ev = fc.load_event(build(tmp_path), "E99")
    doc = fc.run({"E99": ev}, ROOT, aliases={"E99": EV}, event_overrides={c.id: ["E99"] for c in fc.CASES})
    md = fc.to_doc(doc)
    assert "GENERATED by eval/failure_cases.py" in md
    assert md.count("\n### C") == len(fc.CASES) >= 15
    assert set(fc.NARRATIVE) == {c.id for c in fc.CASES}  # every case has what / why / fix
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", md)
    st = Counter(fc.status_group(c.status) for c in fc.CASES)
    assert sum(st.values()) == len(fc.CASES) and set(st) <= set(fc.STATUS_GROUPS)
    assert fc.status_group("flag fixed; best-pick term open") == "partly fixed"
    assert fc.status_group("partly fixed (group keepers open)") == "partly fixed"
    assert "After the fix (cited" in md and md.count("Measured (run before the fix)") == sum(1 for c in fc.CASES if c.after)
    assert fc.status_group("open (needs labels)") == "open" and fc.status_group("by design (data)") == "by design"


def test_top_sentence_names_ties() -> None:
    doc = {"per_category": dict.fromkeys(fc.CATEGORIES, 1) | {fc.CATEGORIES[8]: 5, fc.CATEGORIES[9]: 5,
                                                               fc.CATEGORIES[1]: 3, fc.CATEGORIES[3]: 3}}
    s = fc.top_sentence(doc)
    assert s.startswith(f"{fc.CATEGORIES[8]} (5), {fc.CATEGORIES[9]} (5), then a 2-way tie at 3: ")

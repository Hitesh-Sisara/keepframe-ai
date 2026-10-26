"""sample_for_labelling (whole bursts, never cut; paginated sheets) and dataset_characteristics."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "eval" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


sfl = _load("sample_for_labelling")
dsc = _load("dataset_characteristics")


def _event(root: Path, ev: str, sizes: list[int], n_single: int, faces: int = 2) -> list[str]:
    pids = [f"{ev}_{i:04d}" for i in range(sum(sizes) + n_single)]
    groups, k = [], 0
    for gi, s in enumerate(sizes):
        groups.append({"group_id": f"g_{ev}_{gi}", "members": pids[k:k + s], "size": s})
        k += s
    (root / "results" / ev).mkdir(parents=True)
    (root / "results" / ev / "groups.json").write_text(json.dumps(
        {"groups": groups, "singletons": pids[k:], "exact_duplicates": {}}))
    (root / "manifests").mkdir(exist_ok=True)
    (root / "manifests" / f"{ev}.jsonl").write_text("".join(json.dumps(
        {"photo_id": p, "event_id": ev, "clicked_at": f"2026-01-01T10:00:{i % 60:02d}Z", "frs_status": "indexed",
         "frs_faces": [{}] * faces}) + "\n" for i, p in enumerate(pids)))
    (root / "manifests" / f"{ev}.meta.json").write_text(json.dumps({"category": "Test", "cameras": ["a"]}))
    (root / "raw" / ev).mkdir(parents=True)
    ok, buf = cv2.imencode(".webp", np.full((64, 96, 3), 128, np.uint8))
    for p in pids:
        (root / "raw" / ev / f"{p}.webp").write_bytes(buf.tobytes())
    return pids


def test_whole_bursts_never_cut_and_pages(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "w"
    _event(root, "evA", [75, 30, 5, 3, 2, 2], 20)   # burst-heavy, few singletons
    _event(root, "evB", [2, 2, 2], 900)             # mostly singletons
    _event(root, "evC", [75, 2], 0)                 # the 75-frame burst must be taken whole
    labels, sheets = tmp_path / "labels", tmp_path / "sheets"
    monkeypatch.setattr("sys.argv", ["x", "--plan", "A=100:1.0", "B=850:0.2", "C=77:1.0",
                                     "--alias", "A=evA", "B=evB", "C=evC", "--root", str(root),
                                     "--labels-dir", str(labels), "--sheets-dir", str(sheets)])
    assert sfl.main() == 0
    plan = {e["event"]: e for e in json.loads((labels / "sample_plan.json").read_text())["events"]}

    rows = list(csv.DictReader((labels / "evA.todo.csv").open()))
    by_sheet: dict[str, int] = {}
    for r in rows:
        if r["sheet"].startswith("g"):  # burst sheets; singleton pages are "s...."
            by_sheet[r["sheet"]] = by_sheet.get(r["sheet"], 0) + 1
    remaining = [75, 30, 5, 3, 2, 2]  # every sampled burst is complete (never cut)
    for v in by_sheet.values():
        assert v in remaining
        remaining.remove(v)
    assert sum(by_sheet.values()) <= 100
    # bursts that do not fit are skipped, the rest is singletons; a shortfall is reported, not hidden
    assert plan["A"]["photos"] == len(rows) and plan["A"]["shortfall"] == 100 - len(rows)

    b = plan["B"]
    assert b["photos"] == 850 and b["in_bursts"] == 6 and b["singletons"] == 844 and b["shortfall"] == 0

    rows_c = list(csv.DictReader((labels / "evC.todo.csv").open()))
    big = [r for r in rows_c if r["sheet"] == "g0000"]
    assert len(big) == 75 and [int(r["tile"]) for r in big] == list(range(1, 76))
    assert {r["page"] for r in big} == {"1", "2", "3", "4"}  # 24 tiles per page
    assert len(list((sheets / "evC").glob("g0000_p*.jpg"))) == 4
    assert not any("ev" in json.dumps(e.get("largest_groups")) for e in plan.values())  # counts only


def test_plan_total_bounds(tmp_path: Path, monkeypatch) -> None:
    import pytest

    monkeypatch.setattr("sys.argv", ["x", "--plan", "E07=100", "--root", str(tmp_path)])
    with pytest.raises(SystemExit):
        sfl.main()


def test_dataset_characteristics(tmp_path: Path) -> None:
    root = tmp_path / "w"
    _event(root, "evA", [75, 3, 2], 20)
    c = dsc.characterise(root, "evA")
    assert c["bursts"] == 3 and c["photos_in_bursts"] == 80 and c["singletons"] == 20
    assert c["burst_size_histogram"] == {"2": 1, "3-5": 1, "6-10": 0, "11-20": 0, "21+": 1}
    assert c["largest_burst"] == 75 and c["bursts_ge_3"] == 2
    assert c["faces_per_frame_median"] == 2 and c["faces_histogram_pct"]["2"] == 100.0
    assert c["pct_in_bursts_of_processed"] == 80.0


def test_dataset_exif_availability(tmp_path: Path) -> None:
    import json

    root = tmp_path / "w"
    _event(root, "evA", [2], 2)
    man = root / "manifests" / "evA.jsonl"
    rows = [json.loads(x) for x in man.read_text().splitlines() if x.strip()]
    for i, r in enumerate(rows):
        r["exif_key"] = f"exif/evA/{r['photo_id']}.exif.json" if i < 3 else None
    man.write_text("".join(json.dumps(r) + "\n" for r in rows))
    idx = {"event_id": "evA", "photos": {
        rows[0]["photo_id"]: {"schema_kind": "small", "datetime_original": "2026-07-18T22:40:04",
                              "exposure_time_s": 0.004, "model": "Cam", "tz_known": False},
        rows[1]["photo_id"]: {"schema_kind": "rich", "datetime_original": "2026-07-18T22:40:05",
                              "exposure_time_s": None, "model": "Cam", "tz_known": True},
        rows[2]["photo_id"]: {}}}
    (root / "results" / "evA" / "exif_index.json").write_text(json.dumps(idx))
    c = dsc.characterise(root, "evA")
    assert c["exif_sidecar_pct"] == 75.0 and c["exif_parsed_pct"] == 75.0
    assert c["exif_capture_time_pct"] == 50.0 and c["exif_exposure_time_pct"] == 25.0
    assert c["exif_tz_known_pct"] == 25.0 and c["exif_schema"] == {"rich": 1, "small": 1, "unknown": 1}
    md = dsc.render_dataset_md({"E99": c | {"category": "Test"}}, "synthetic")
    assert "EXIF sidecar present" in md and "evA" not in md


def test_labeller_mode(tmp_path: Path, monkeypatch) -> None:
    import csv

    import pytest

    assert sfl.check_labeller("team:hs") == "team:hs" and sfl.check_labeller("photographer") == "photographer"
    for bad in ("hs", "Photographer", "team:", "lead-mechanism-test", "team:a b"):
        with pytest.raises(SystemExit):
            sfl.check_labeller(bad)
    f = tmp_path / "ev.csv"
    f.write_text("photo_id,verdict_human,labeller\np1,keep,HS\np2,,\n")
    assert sfl.stamp_csv(f, "team:hs") == 2
    rows = list(csv.DictReader(f.open()))
    assert {r["labeller"] for r in rows} == {"team:hs"} and rows[0]["verdict_human"] == "keep"

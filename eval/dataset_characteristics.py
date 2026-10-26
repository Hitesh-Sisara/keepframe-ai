#!/usr/bin/env python3
"""Dataset characteristics of the evaluation events (aggregates only; no ids in the output).

Per event, from results/<eventId>/groups.json (+ summary.json failures) and manifests/<eventId>.jsonl
(+ .meta.json): photos, photos processed, photos in multi-photo groups (bursts), burst-size histogram,
largest burst, singletons, exact duplicates, faces per frame from the existing face metadata
(frsInfo, else Rekognition boxes; frs_status == 'no_faces' counts as 0), cameras, and EXIF availability
(manifest exif_key + results/<eventId>/exif_index.json: capture time, exposure time, camera model, time zone,
rich vs small sidecar schema). This is what decides
which metrics are measurable on an event: best-of-burst agreement needs many multi-frame bursts.

    python eval/dataset_characteristics.py --root <work-bucket mirror> --event E07 --event E03 --event E09
    (aliases resolve via eval/events.py; LABEL=<eventId> also works for ad-hoc runs)
    -> eval/results/dataset_characteristics.{json,md} and eval/results/dataset.md (table + how to read it)
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

import yaml

_CATEGORIES = Path(__file__).resolve().parents[1] / "config" / "source_categories.yaml"
CATEGORY_DISPLAY: dict[str, str] = yaml.safe_load(_CATEGORIES.read_text()).get("display") or {}


def display_category(c: str | None) -> str | None:
    """Generic label for a source category string (config/source_categories.yaml `display`)."""
    return CATEGORY_DISPLAY.get(c, c) if c else c

HERE = Path(__file__).resolve().parent
BINS = (("2", 2, 2), ("3-5", 3, 5), ("6-10", 6, 10), ("11-20", 11, 20), ("21+", 21, 10**9))
FACE_BINS = ("0", "1", "2", "3-4", "5+")


def faces_of(r: dict) -> int | None:
    if r.get("frs_status") == "indexed":
        return len(r.get("frs_faces") or [])
    if r.get("frs_status") == "no_faces":
        return 0
    if r.get("rek_faces"):
        return len(r["rek_faces"])
    return None


def face_bin(n: int) -> str:
    return "0" if n == 0 else "1" if n == 1 else "2" if n == 2 else "3-4" if n <= 4 else "5+"


def characterise(root: Path, event_id: str) -> dict:
    meta_p = root / "manifests" / f"{event_id}.meta.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    rows = [json.loads(x) for x in (root / "manifests" / f"{event_id}.jsonl").read_text().splitlines() if x.strip()]
    g = json.loads((root / "results" / event_id / "groups.json").read_text())
    summ_p = root / "results" / event_id / "summary.json"
    summ = json.loads(summ_p.read_text()) if summ_p.exists() else {}
    n = len(rows)
    multi = [len(x.get("members") or x.get("photo_ids") or []) for x in g.get("groups") or []]
    sizes = [s for s in multi if s >= 2]
    singles = g.get("singletons")
    n_single = (len(singles) if isinstance(singles, list) else int(singles or 0)) + sum(1 for s in multi if s == 1)
    failed = len(summ.get("failures") or [])
    processed = n - failed
    in_bursts = sum(sizes)
    hist = {lab: sum(1 for s in sizes if lo <= s <= hi) for lab, lo, hi in BINS}
    photos_by_bin = {lab: sum(s for s in sizes if lo <= s <= hi) for lab, lo, hi in BINS}
    faces = [f for f in (faces_of(r) for r in rows) if f is not None]
    fh = Counter(face_bin(f) for f in faces)
    ed = g.get("exact_duplicates")
    exif = exif_availability(root, event_id, rows)
    return {
        "category": display_category(meta.get("category")),
        "photos": n,
        "photos_processed": processed,
        "photos_not_processed": failed,
        "not_processed_reason": sorted({(f.get("error") or "")[:40] for f in summ.get("failures") or []})[:3],
        "groups_run_id": g.get("run_id"),
        "bursts": len(sizes),
        "photos_in_bursts": in_bursts,
        "pct_in_bursts_of_processed": round(100 * in_bursts / processed, 1) if processed else None,
        "pct_in_bursts_of_event": round(100 * in_bursts / n, 1) if n else None,
        "singletons": n_single,
        "burst_size_histogram": hist,
        "photos_by_burst_size": photos_by_bin,
        "largest_burst": max(sizes) if sizes else 0,
        "median_burst": statistics.median(sizes) if sizes else None,
        "bursts_ge_3": sum(1 for s in sizes if s >= 3),
        "exact_duplicates": len(ed) if isinstance(ed, (dict, list)) else (ed or 0),
        "faces_known_pct": round(100 * len(faces) / n, 1) if n else None,
        "faces_per_frame_median": statistics.median(faces) if faces else None,
        "faces_per_frame_mean": round(statistics.fmean(faces), 2) if faces else None,
        "faces_histogram_pct": {b: round(100 * fh.get(b, 0) / len(faces), 1) for b in FACE_BINS} if faces else {},
        "cameras": len(meta.get("cameras") or []),
        **exif,
    }


def exif_availability(root: Path, event_id: str, rows: list[dict]) -> dict:
    """Share of manifest photos with an EXIF sidecar, and with each field the pipeline uses (aggregates only)."""
    n = len(rows)
    with_key = sum(1 for r in rows if r.get("exif_key"))
    idx_p = root / "results" / event_id / "exif_index.json"
    photos = (json.loads(idx_p.read_text()).get("photos") or {}) if idx_p.exists() else {}
    vals = [v or {} for v in photos.values()]

    def pct(k: int) -> float | None:
        return round(100 * k / n, 1) if n else None

    return {
        "exif_index_present": idx_p.exists(),
        "exif_sidecar_pct": pct(with_key),
        "exif_parsed_pct": pct(len(vals)) if idx_p.exists() else None,
        "exif_capture_time_pct": pct(sum(1 for v in vals if v.get("datetime_original"))) if idx_p.exists() else None,
        "exif_exposure_time_pct": pct(sum(1 for v in vals if v.get("exposure_time_s"))) if idx_p.exists() else None,
        "exif_camera_model_pct": pct(sum(1 for v in vals if v.get("model"))) if idx_p.exists() else None,
        "exif_tz_known_pct": pct(sum(1 for v in vals if v.get("tz_known"))) if idx_p.exists() else None,
        "exif_schema": dict(sorted(Counter(str(v.get("schema_kind") or "unknown") for v in vals).items())),
    }


def render(rows: dict[str, dict]) -> str:
    labs = list(rows)

    def row(title: str, fn) -> str:
        return f"| {title} | " + " | ".join(str(fn(rows[k])) for k in labs) + " |"

    L = ["| | " + " | ".join(f"{k} ({rows[k]['category']})" for k in labs) + " |",
         "|---|" + "---|" * len(labs),
         row("photos (processed)", lambda r: f"{r['photos']:,} ({r['photos_processed']:,})"),
         row("bursts (multi-photo groups)", lambda r: r["bursts"]),
         row("photos in bursts", lambda r: f"{r['photos_in_bursts']:,} ({r['pct_in_bursts_of_processed']} % of processed)"),
         row("singletons", lambda r: f"{r['singletons']:,}"),
         row("bursts of size 2 / 3-5 / 6-10 / 11-20 / 21+",
             lambda r: " / ".join(str(r["burst_size_histogram"][b]) for b, _, _ in BINS)),
         row("bursts with ≥ 3 frames", lambda r: r["bursts_ge_3"]),
         row("largest / median burst", lambda r: f"{r['largest_burst']} / {r['median_burst']}"),
         row("exact duplicates", lambda r: r["exact_duplicates"]),
         row("faces per frame, median (mean)", lambda r: f"{r['faces_per_frame_median']} ({r['faces_per_frame_mean']})"),
         row("frames with 0 / 1 / 2 / 3-4 / 5+ faces (%)",
             lambda r: " / ".join(str(r["faces_histogram_pct"].get(b, 0)) for b in FACE_BINS)),
         row("cameras", lambda r: r["cameras"]),
         row("EXIF sidecar present (% of photos)", lambda r: _p(r.get("exif_sidecar_pct"))),
         row("EXIF capture time / exposure time / camera model (%)",
             lambda r: " / ".join(_p(r.get(k)) for k in ("exif_capture_time_pct", "exif_exposure_time_pct",
                                                         "exif_camera_model_pct"))),
         row("EXIF time zone known (%)", lambda r: _p(r.get("exif_tz_known_pct"))),
         row("EXIF sidecar schema", lambda r: ", ".join(f"{k} {v}" for k, v in (r.get("exif_schema") or {}).items())
             or "n/a")]
    return "\n".join(L) + "\n"


def _p(x: float | None) -> str:
    return "n/a" if x is None else f"{x}"


DATASET_NOTES = """
How to read it:

- **Bursts %** is the share of processed frames that sit in a multi-photo group (worker grouping: pHash +
  time window + same camera). Only an event with many bursts of 3+ frames can measure best-of-burst agreement;
  with size-2 bursts top-2 agreement is always 100 % and says nothing.
- **Faces per frame** come from the faces already stored with the gallery (frsInfo, else Rekognition boxes),
  not from our own detector; `no_faces` counts as 0. They decide whether eye-state metrics have support.
- **EXIF**: the share of photos with a sidecar and with each field the pipeline reads. Capture time feeds
  burst grouping (without it, `clickedAt` is the upload time), exposure time and local hour feed the P6
  artistic-intent guard, camera model splits bursts per body. "Time zone known" is rare: local hour is then
  taken from the camera clock as is.
- Aggregates only: no event, photo or run ids. Events are E-index aliases (data/selected_events.json, gitignored).
"""


def render_dataset_md(rows: dict[str, dict], sources: str) -> str:
    return ("# Evaluation dataset characteristics\n\n"
            f"Generated by `eval/dataset_characteristics.py` from each event's latest worker `groups.json`, "
            f"`exif_index.json` and manifest face metadata. {sources}\n\n" + render(rows) + DATASET_NOTES)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True, help="local mirror with manifests/ and results/")
    ap.add_argument("--event", action="append", required=True,
                    help="alias (E07) resolved via eval/events.py, or LABEL=EVENT_ID; only the label is printed")
    ap.add_argument("--out-dir", type=Path, default=HERE / "results")
    ap.add_argument("--sources", default="", help="free-text note on which runs fed the table (no ids)")
    args = ap.parse_args()
    import sys

    sys.path.insert(0, str(HERE))
    from events import load_aliases

    aliases = load_aliases()
    rows = {}
    for item in args.event:
        lab, _, ev = item.partition("=")
        ev = ev or aliases.get(lab, "")
        if not ev:
            raise SystemExit(f"cannot resolve {lab}: no events file and no LABEL=EVENT_ID")
        rows[lab] = characterise(args.root, ev)
    for r in rows.values():
        r.pop("groups_run_id", None)  # run ids embed event-id prefixes; keep the committed file id-free
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "dataset_characteristics.json").write_text(json.dumps(rows, indent=2) + "\n")
    (args.out_dir / "dataset_characteristics.md").write_text(render(rows))
    (args.out_dir / "dataset.md").write_text(render_dataset_md(rows, args.sources))
    print(render(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

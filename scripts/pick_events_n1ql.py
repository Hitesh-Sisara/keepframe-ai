#!/usr/bin/env python3
"""Pick evaluation events from a source Couchbase database (READ ONLY N1QL), per docs/DATA.md §5.

1. Event-level filter (idx_event_consolidated): type=Event, not archived/disabled/expired/
   photographer-deleted, createdAt >= 2026-01-01, 500 <= currentPhotosCount <= 5000.
2. Covered pre-screen on idx_photo_event_consolidated_v2 (no document fetch): per-event counts of
   originalKey / clickedAt / camera / excluded albums, batched ``p.event IN $evs``.
3. Full data-quality stats (adds frsInfo / mfaces / md5, needs a fetch) only for events that pass the
   pre-screen, one event-scoped aggregate each (``p.event = $ev``).
4. Keep: originals >= 80 %, clickedAt >= 90 %, camera >= 50 %, some frsInfo or mfaces.
5. Select 12 per the quota in config/source_categories.yaml (5 ceremony, one of them >= 2,000 photos;
   2 engagement, 2 birthday/anniversary, 1 corporate, 2 other).

Requires SOURCE_DB_KEYSPACE and SOURCE_SELECTION_SEED (the original selection seed, operator-held): the
seed is never stored in the repo, and without the same seed the selection is not reproduced.

Writes data/candidates.json and data/selected_events.json (both gitignored: they hold real ids).
Prints only E-index, category and counts (no names, no ids) unless --show-ids.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from n1ql_ro import N1QL  # noqa: E402

from keepframe.io import source  # noqa: E402

EXCLUDED_ALBUM_SQL = (
    'p.album LIKE "generated-%" OR p.album LIKE "ftp-%" OR p.album LIKE "highlight-%" '
    'OR p.album LIKE "%-GUESTALBUM"'
)

EVENTS_Q_T = """
SELECT META(e).id AS id, e.category, e.currentPhotosCount AS current_photos_count,
       e.createdAt AS created_at, e.eventDate AS event_date, e.whitelabelId AS whitelabel_id,
       e.sortBy AS sort_by, e.facesEnabledAt AS faces_enabled_at,
       e.isWatermarkAllowed AS is_watermark_allowed
FROM `{ks}` e
WHERE e.type = "Event" AND e.whitelabelId IS NOT MISSING AND e.createdAt >= $since
  AND (e.isArchived IS MISSING OR e.isArchived = false)
  AND (e.isEventDisabled IS MISSING OR e.isEventDisabled IS NULL OR e.isEventDisabled = false)
  AND (e.isEventExpired IS MISSING OR e.isEventExpired IS NULL OR e.isEventExpired = false)
  AND (e.isPhotographerDeleted IS MISSING OR e.isPhotographerDeleted = false)
  AND e.currentPhotosCount BETWEEN $min_n AND $max_n
"""

STATS_Q_T = f"""
SELECT COUNT(1) AS n,
 SUM(CASE WHEN {EXCLUDED_ALBUM_SQL} THEN 1 ELSE 0 END) AS n_excluded,
 SUM(CASE WHEN NOT ({EXCLUDED_ALBUM_SQL}) AND p.originalKey IS VALUED AND p.originalKey != ""
          THEN 1 ELSE 0 END) AS n_orig,
 SUM(CASE WHEN NOT ({EXCLUDED_ALBUM_SQL}) AND p.clickedAt IS VALUED THEN 1 ELSE 0 END) AS n_clicked,
 SUM(CASE WHEN NOT ({EXCLUDED_ALBUM_SQL}) AND p.camera IS VALUED AND p.camera != ""
          THEN 1 ELSE 0 END) AS n_camera,
 SUM(CASE WHEN NOT ({EXCLUDED_ALBUM_SQL}) AND p.frsInfo.status IS VALUED THEN 1 ELSE 0 END) AS n_frs,
 SUM(CASE WHEN NOT ({EXCLUDED_ALBUM_SQL}) AND p.frsInfo.status = "indexed" THEN 1 ELSE 0 END)
     AS n_frs_indexed,
 SUM(CASE WHEN NOT ({EXCLUDED_ALBUM_SQL}) AND p.mfaces IS VALUED THEN 1 ELSE 0 END) AS n_mfaces,
 SUM(CASE WHEN NOT ({EXCLUDED_ALBUM_SQL}) AND p.md5 IS VALUED THEN 1 ELSE 0 END) AS n_md5,
 ARRAY_AGG(DISTINCT p.camera) AS cameras
FROM `{{ks}}` p WHERE p.type = "Photo" AND p.event = $ev
"""

PRESCREEN_Q_T = f"""
SELECT p.event AS id, COUNT(1) AS n,
 SUM(CASE WHEN {EXCLUDED_ALBUM_SQL} THEN 1 ELSE 0 END) AS n_excluded,
 SUM(CASE WHEN NOT ({EXCLUDED_ALBUM_SQL}) AND p.originalKey IS VALUED AND p.originalKey != ""
          THEN 1 ELSE 0 END) AS n_orig,
 SUM(CASE WHEN NOT ({EXCLUDED_ALBUM_SQL}) AND p.clickedAt IS VALUED THEN 1 ELSE 0 END) AS n_clicked,
 SUM(CASE WHEN NOT ({EXCLUDED_ALBUM_SQL}) AND p.camera IS VALUED AND p.camera != ""
          THEN 1 ELSE 0 END) AS n_camera
FROM `{{ks}}` p USE INDEX (idx_photo_event_consolidated_v2)
WHERE p.type = "Photo" AND p.event IN $evs
GROUP BY p.event
"""


def _q(template: str) -> str:
    """Fill the validated SOURCE_DB_KEYSPACE into a statement template's {ks} slot."""
    return template.replace("{ks}", source.keyspace())


def prescreen(q: N1QL, events: list[dict[str, Any]], batch: int = 25) -> list[dict[str, Any]]:
    """Covered-index counts for every event; returns those meeting the originals/clickedAt/camera bars."""
    by_id = {e["id"]: e for e in events}
    ids = list(by_id)
    chunks = [ids[i : i + batch] for i in range(0, len(ids), batch)]
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda c: q.query(_q(PRESCREEN_Q_T), evs=c), chunks))
    passed = []
    for r in (x for res in results for x in res):
        kept = (r["n"] or 0) - (r["n_excluded"] or 0)
        e = by_id[r["id"]]
        e["prescreen"] = {k: r[k] for k in ("n", "n_excluded", "n_orig", "n_clicked", "n_camera")}
        if (
            kept >= 500
            and pct(r["n_orig"], kept) >= 80
            and pct(r["n_clicked"], kept) >= 90
            and pct(r["n_camera"], kept) >= 50
        ):
            passed.append(e)
    return passed


# selection quota: (label, categories, count), from config/source_categories.yaml (the source database's own
# category strings live only there, so the docs can show generic labels).
_QCFG: list[dict[str, Any]] = yaml.safe_load((ROOT / "config" / "source_categories.yaml").read_text())["quota"]
QUOTA: list[tuple[str, set[str], int]] = [(q["label"], set(q["categories"]), int(q["count"])) for q in _QCFG]
BIG_POOL = {q["label"]: int(q["require_one_at_least"]) for q in _QCFG if q.get("require_one_at_least")}
DISTINCT = {q["label"] for q in _QCFG if q.get("distinct_categories")}
QUOTA_CATS = set().union(*(c for _, c, _ in QUOTA))


def bucket_of(category: str | None) -> str:
    for label, cats, _ in QUOTA:
        if category in cats:
            return label
    return "other"


def stable_rank(event_id: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}:{event_id}".encode()).hexdigest()


def pct(a: int | None, b: int | None) -> float:
    return round(100.0 * (a or 0) / b, 1) if b else 0.0


def enrich(q: N1QL, ev: dict[str, Any]) -> dict[str, Any]:
    s = q.query(_q(STATS_Q_T), ev=ev["id"])[0]
    n_kept = (s["n"] or 0) - (s["n_excluded"] or 0)
    cams = sorted(c for c in (s.pop("cameras") or []) if c)
    ev.update(s)
    ev.update(
        n_kept=n_kept,
        n_cameras=len(cams),
        cameras=cams,
        orig_pct=pct(s["n_orig"], n_kept),
        clicked_pct=pct(s["n_clicked"], n_kept),
        camera_pct=pct(s["n_camera"], n_kept),
        frs_pct=pct(s["n_frs"], n_kept),
        mfaces_pct=pct(s["n_mfaces"], n_kept),
    )
    ev["passes"] = bool(
        n_kept >= 500
        and ev["orig_pct"] >= 80
        and ev["clicked_pct"] >= 90
        and ev["camera_pct"] >= 50
        and (s["n_frs"] or s["n_mfaces"])
    )
    return ev


def select(cands: list[dict[str, Any]], seed: str) -> list[dict[str, Any]]:
    """Deterministic pick honouring QUOTA; a BIG_POOL label must include one event at or above its size."""
    ok = sorted((c for c in cands if c["passes"]), key=lambda c: stable_rank(c["id"], seed))
    chosen: list[dict[str, Any]] = []
    for label, _, k in QUOTA:
        pool = [c for c in ok if bucket_of(c.get("category")) == label]
        picked: list[dict[str, Any]] = []
        if label in BIG_POOL:
            big = [c for c in pool if c["n_kept"] >= BIG_POOL[label]]
            picked += big[:1]
        if label in DISTINCT:  # prefer distinct categories
            seen: set[str | None] = set()
            for c in pool:
                if c.get("category") not in seen and len(picked) < k:
                    picked.append(c)
                    seen.add(c.get("category"))
        for c in pool:
            if len(picked) >= k:
                break
            if c not in picked:
                picked.append(c)
        if len(picked) < k:
            print(f"WARN: only {len(picked)}/{k} passing candidates for {label}", file=sys.stderr)
        chosen += picked
    # whitelabel mix: report only
    for i, c in enumerate(chosen, 1):
        c["e_index"] = f"E{i:02d}"
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--min-photos", type=int, default=500)
    ap.add_argument("--max-photos", type=int, default=5000)
    ap.add_argument("--per-category", type=int, default=30, help="max pre-screened events per quota bucket to fully score")
    ap.add_argument("--seed", default=None, help="defaults to $SOURCE_SELECTION_SEED (required, operator-held)")
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--show-ids", action="store_true", help="print event ids (local terminal only)")
    args = ap.parse_args()
    args.seed = args.seed or source.require("SOURCE_SELECTION_SEED")

    q = N1QL()
    events = q.query(_q(EVENTS_Q_T), since=args.since, min_n=args.min_photos, max_n=args.max_photos)
    print(f"event-level candidates: {len(events)}", file=sys.stderr)
    pre = prescreen(q, events)
    print(f"pass covered pre-screen (orig/clickedAt/camera): {len(pre)}", file=sys.stderr)
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in pre:
        by_bucket[bucket_of(e.get("category"))].append(e)
    sample: list[dict[str, Any]] = []
    for _label, evs in by_bucket.items():
        evs.sort(key=lambda e: stable_rank(e["id"], args.seed))
        sample += evs[: args.per_category]
    print(f"computing full stats for {len(sample)} events", file=sys.stderr)
    with ThreadPoolExecutor(6) as ex:
        cands = list(ex.map(lambda e: enrich(q, e), sample))

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    (out / "candidates.json").write_text(json.dumps({"generated_at": now, "candidates": cands}, indent=1))
    chosen = select(cands, args.seed)
    (out / "selected_events.json").write_text(
        json.dumps({"generated_at": now, "seed": args.seed, "events": chosen}, indent=1)
    )

    passing = sum(c["passes"] for c in cands)
    print(f"event-level: {len(events)}  pre-screen pass: {len(pre)}  fully scored: {len(cands)}  pass all: {passing}")
    per_bucket = defaultdict(int)
    for c in cands:
        if c["passes"]:
            per_bucket[bucket_of(c.get("category"))] += 1
    print("passing per bucket:", dict(per_bucket))
    hdr = f"{'idx':4} {'category':20} {'n_kept':>6} {'orig':>6} {'click':>6} {'cam':>6} {'frs':>6} {'mfc':>6} {'ncam':>4} wl"
    print(hdr)
    for c in chosen:
        wl = "wl-0" if c.get("whitelabel_id") == "whitelabel-0" else "brand"
        print(
            f"{c['e_index']:4} {str(c.get('category')):20} {c['n_kept']:6d} {c['orig_pct']:6.1f} "
            f"{c['clicked_pct']:6.1f} {c['camera_pct']:6.1f} {c['frs_pct']:6.1f} {c['mfaces_pct']:6.1f} "
            f"{c['n_cameras']:4d} {wl}" + (f"  {c['id']}" if args.show_ids else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

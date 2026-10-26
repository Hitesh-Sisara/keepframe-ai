#!/usr/bin/env python3
"""Export one event's manifest from a source Couchbase database (READ ONLY N1QL). Optional tooling.

  python scripts/export_manifest_n1ql.py --event-id <uuid> --out s3://$WORK_BUCKET/manifests/
  python scripts/export_manifest_n1ql.py --event-id <uuid> --local data/manifests
  python scripts/export_manifest_n1ql.py --event-id <uuid> --local data/manifests --same-second-bursts

* Pages Photo docs with keyset pagination on META().id (``META(p).id > $after ... LIMIT n``), never
  a large OFFSET. Only event-scoped queries (``p.event = $ev``). exifData is never selected.
* Excludes albums generated-*, ftp-*, highlight-* (copies of photos already in a real album) and
  *-GUESTALBUM, by photo.album id and by Album doc name.
* Validates the row count against Event.currentPhotosCount minus excluded rows (warns on drift;
  Event.currentPhotosCount can be stale, by design).
* Optionally LISTs the source object store to measure EXIF sidecar coverage and picks the exif bucket
  (prefers $SOURCE_BUCKET_EXIF_RICH); the source store is used read only (LIST).
* Keyspace, bucket names and CDN hosts come from SOURCE_* env vars (keepframe/io/source.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from manifest_rows import (  # noqa: E402
    PHOTO_FIELDS,
    album_excluded,
    same_second_bursts,
    shape_meta,
    shape_photo,
    sort_rows,
)
from n1ql_ro import N1QL  # noqa: E402

from keepframe.io import source  # noqa: E402

PAGE = 1000

def event_q(ks: str) -> str:
    return f"""
SELECT META(e).id AS id, e.name, e.category, e.eventDate, e.createdAt, e.whitelabelId, e.sortBy,
       e.isArchived, e.isEventExpired, e.isEventDisabled, e.currentPhotosCount, e.isHighlightsEnabled,
       e.facesEnabledAt, e.isWatermarkAllowed, e.watermarkGuestUploads
FROM `{ks}` e USE KEYS $ev WHERE e.type = "Event"
"""


def album_q(ks: str) -> str:
    return f'SELECT META(a).id AS id, a.name FROM `{ks}` a WHERE a.type = "Album" AND a.event = $ev'


def photo_q(ks: str) -> str:
    return (
        "SELECT META(p).id AS id, "
        + ", ".join(f"p.`{f}`" for f in PHOTO_FIELDS)
        + f' FROM `{ks}` p WHERE p.type = "Photo" AND p.event = $ev AND META(p).id > $after'
        " ORDER BY META(p).id LIMIT $lim"
    )


def fetch_photos(q: N1QL, event_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    after = ""
    while True:
        page = q.query(photo_q(source.keyspace()), ev=event_id, after=after, lim=PAGE)
        out += page
        if len(page) < PAGE:
            return out
        after = page[-1]["id"]


def source_listing(event_id: str, bucket: str) -> set[str]:
    from keepframe.io.s3 import source_client

    keys: set[str] = set()
    pag = source_client().get_paginator("list_objects_v2")
    for page in pag.paginate(Bucket=bucket, Prefix=f"{event_id}/"):
        keys.update(o["Key"] for o in page.get("Contents", []))
    return keys


def export(event_id: str, check_source: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    q = N1QL()
    ks = source.keyspace()
    rich, small = source.exif_buckets()
    evs = q.query(event_q(ks), ev=[event_id])
    if not evs:
        raise SystemExit(f"event not found: {event_id}")
    ev = evs[0]
    albums = {a["id"]: a.get("name") for a in q.query(album_q(ks), ev=event_id)}
    docs = fetch_photos(q, event_id)

    excluded: dict[str, int] = {}
    kept_docs = []
    for d in docs:
        a = d.get("album")
        if album_excluded(a, albums.get(a) if a else None):
            tag = (a or "").split("-")[0] if not (a or "").endswith("-GUESTALBUM") else "GUESTALBUM"
            excluded[tag] = excluded.get(tag, 0) + 1
            continue
        kept_docs.append(d)

    exif_bucket: str | None = rich
    exif_cov: dict[str, Any] = {}
    sidecars: dict[str, set[str]] = {}
    if check_source:
        for b in (rich, small):
            sidecars[b] = source_listing(event_id, b)
        ids = {d["id"] for d in kept_docs}
        for b, ks in sidecars.items():
            exif_cov[b] = sum(1 for i in ids if f"{event_id}/{i}.exif.json" in ks)

    rows = []
    for d in kept_docs:
        pid = d.pop("id")
        b = exif_bucket
        if check_source:
            key = f"{event_id}/{pid}.exif.json"
            b = next((bk for bk in (rich, small) if key in sidecars[bk]), None)
        rows.append(shape_photo(pid, d, exif_bucket=b))
    rows = sort_rows(rows)

    n_excl = sum(excluded.values())
    cpc = ev.get("currentPhotosCount")
    expected = (cpc - n_excl) if isinstance(cpc, int) else None
    validation = {
        "n_docs": len(docs),
        "n_excluded": n_excl,
        "excluded_by_album": excluded,
        "current_photos_count": cpc,
        "expected_rows": expected,
        "n_rows": len(rows),
        "row_count_ok": expected is None or abs(len(rows) - expected) <= max(5, round(0.01 * expected)),
        "exif_coverage": exif_cov,
    }
    meta = shape_meta(event_id, ev, rows, validation=validation, source="couchbase-n1ql")
    return rows, meta, validation


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event-id", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--out", help="s3://bucket/manifests/ (or a work-bucket-relative prefix)")
    g.add_argument("--local", help="local directory")
    ap.add_argument("--no-source-check", action="store_true", help="skip the source LIST for EXIF coverage")
    ap.add_argument("--same-second-bursts", action="store_true", help="print the same-second burst report")
    args = ap.parse_args()

    from keepframe.io.manifest import dumps_jsonl

    rows, meta, val = export(args.event_id, check_source=not args.no_source_check)
    if args.same_second_bursts:
        meta["same_second_bursts"] = same_second_bursts(rows)

    if args.local or not args.out:
        d = Path(args.local or "data/manifests")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{args.event_id}.jsonl").write_bytes(dumps_jsonl(rows))
        (d / f"{args.event_id}.meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
        dest = str(d)
    else:
        from keepframe.io import s3

        prefix = args.out.rstrip("/")
        s3.put_bytes(f"{prefix}/{args.event_id}.jsonl", dumps_jsonl(rows), "application/x-ndjson")
        s3.put_json(f"{prefix}/{args.event_id}.meta.json", meta)
        dest = prefix

    summary = {k: v for k, v in val.items()}
    summary.update(dest=dest, n_with_original=meta["n_with_original"], n_cameras=len(meta["cameras"]))
    if args.same_second_bursts:
        summary["same_second_bursts"] = meta["same_second_bursts"]
    print(json.dumps(summary))
    if not val["row_count_ok"]:
        print("WARN: row count differs from currentPhotosCount - excluded", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

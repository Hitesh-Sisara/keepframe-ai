#!/usr/bin/env python3
"""Postgres variant of the manifest exporter (for a source database on Postgres).

STUB: there is no prod Postgres yet (no PG_DSN), so use scripts/export_manifest_n1ql.py today.
The row shaping is shared (scripts/manifest_rows.py), so the manifest format is identical.

  export PG_DSN=...   # from the source database's env file (operator-held, never committed); never echoed
  python scripts/export_manifest.py --event-id <uuid> --local data/manifests

Every session runs ``BEGIN TRANSACTION READ ONLY; SET LOCAL statement_timeout='120s'`` and ends in
ROLLBACK. Table: $SOURCE_DB_KEYSPACE(id text PK, type text, doc jsonb); counts from event_counters.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from manifest_rows import PHOTO_FIELDS, album_excluded, shape_meta, shape_photo, sort_rows  # noqa: E402

from keepframe.io import source  # noqa: E402

PAGE = 1000
COUNT_SQL = "SELECT current_photos_count FROM event_counters WHERE event_id = %s"


def statements(ks: str) -> tuple[str, str, str]:
    """(event, album, photo) SQL for the validated keyspace (table) name."""
    t = f'"{ks}"'
    event = f"SELECT doc FROM {t} WHERE id = %s AND type = 'Event'"
    album = f"SELECT id, doc->>'name' FROM {t} WHERE type = 'Album' AND doc->>'event' = %s"
    photo = (
        "SELECT id, jsonb_build_object("
        + ", ".join(f"'{f}', doc->'{f}'" for f in PHOTO_FIELDS)
        + f") FROM {t} WHERE type = 'Photo' AND doc->>'event' = %s AND id > %s ORDER BY id LIMIT %s"
    )
    return event, album, photo


def export(dsn: str, event_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import psycopg

    EVENT_SQL, ALBUM_SQL, PHOTO_SQL = statements(source.keyspace())  # noqa: N806
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("BEGIN TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = '120s'")
        try:
            cur.execute(EVENT_SQL, (event_id,))
            row = cur.fetchone()
            if not row:
                raise SystemExit(f"event not found: {event_id}")
            ev: dict[str, Any] = row[0]
            cur.execute(COUNT_SQL, (event_id,))
            c = cur.fetchone()
            cur.execute(ALBUM_SQL, (event_id,))
            albums = dict(cur.fetchall())
            rows, after = [], ""
            while True:
                cur.execute(PHOTO_SQL, (event_id, after, PAGE))
                page = cur.fetchall()
                for pid, doc in page:
                    a = doc.get("album")
                    if not album_excluded(a, albums.get(a) if a else None):
                        rows.append(shape_photo(pid, doc))
                if len(page) < PAGE:
                    break
                after = page[-1][0]
        finally:
            conn.rollback()
    rows = sort_rows(rows)
    meta = shape_meta(event_id, ev, rows, source="postgres", current_photos_count=c[0] if c else None)
    return rows, meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event-id", required=True)
    ap.add_argument("--local", default="data/manifests")
    args = ap.parse_args()
    dsn = os.environ.get("PG_DSN")
    if not dsn:
        print("PG_DSN not set; prod is still Couchbase - use scripts/export_manifest_n1ql.py", file=sys.stderr)
        return 2
    from keepframe.io.manifest import dumps_jsonl

    rows, meta = export(dsn, args.event_id)
    d = Path(args.local)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{args.event_id}.jsonl").write_bytes(dumps_jsonl(rows))
    (d / f"{args.event_id}.meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps({"n_rows": len(rows), "dest": str(d)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

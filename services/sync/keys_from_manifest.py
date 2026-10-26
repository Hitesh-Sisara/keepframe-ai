#!/usr/bin/env python3
"""Turn manifests/<eventId>.jsonl into rclone --files-from lists (one source key per line).

  keys_from_manifest.py --manifest m.jsonl --kind processed > keys.txt
  keys_from_manifest.py --manifest m.jsonl --kind exif --exif-bucket "$SOURCE_BUCKET_EXIF_RICH" > exif.txt
  keys_from_manifest.py --manifest m.jsonl --kind originals --photo-ids ids.txt > orig.txt

Source keys look like ``<eventId>/<photoId>.<ext>``; rclone copies them to ``<dest>/<eventId>/...``,
so the sync entrypoint copies from the bucket root into ``raw/`` (or ``exif/``, ``originals/``) and the
result is ``raw/<eventId>/<photoId>.<ext>`` exactly as docs/ARCHITECTURE.md "S3 layout". Rows whose processed_key does not
follow ``<eventId>/<photoId>.<ext>`` are reported on stderr (the python sync handles those).
Standard library only (runs in the slim rclone image).
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, help="local JSONL path or - for stdin")
    ap.add_argument("--kind", choices=["processed", "exif", "originals"], required=True)
    ap.add_argument("--exif-bucket", help="only rows whose exif_bucket equals this")
    ap.add_argument("--photo-ids", help="file with photo ids to include (originals second pass)")
    a = ap.parse_args()
    ids = None
    if a.photo_ids:
        with open(a.photo_ids) as f:
            ids = {x.strip() for x in f if x.strip()}
    src = sys.stdin if a.manifest == "-" else open(a.manifest)
    n = odd = 0
    for line in src:
        if not line.strip():
            continue
        r = json.loads(line)
        if ids is not None and r["photo_id"] not in ids:
            continue
        if a.kind == "processed":
            k = r.get("processed_key")
        elif a.kind == "exif":
            k = r.get("exif_key") if (not a.exif_bucket or r.get("exif_bucket") == a.exif_bucket) else None
        else:
            k = r.get("original_key")
        if not k:
            continue
        if not k.startswith(f"{r['event_id']}/{r['photo_id']}."):
            odd += 1
        print(k)
        n += 1
    print(f"{a.kind}: {n} keys ({odd} not <eventId>/<photoId>.<ext>)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

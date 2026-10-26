"""Pure-python source -> S3 import (fallback for the services/sync/ rclone task, SyncSource).

  python -m keepframe.io.sync --event-id <id>                      # processed + exif
  python -m keepframe.io.sync --event-id <id> --kinds originals --photo-ids-file ids.txt
  python -m keepframe.io.sync --keys-file bench/manifest_5k.jsonl --dest-prefix bench/5k

The source side is READ ONLY (GET / HEAD). Each object is streamed source GET -> S3 PUT (upload_fileobj, no temp
file) with 32 worker threads by default. Objects already present in S3 with the same size are skipped
(one LIST per destination prefix), so reruns are cheap and resumable.

Destination layout (docs/ARCHITECTURE.md "S3 layout"):
  processed  -> raw/<eventId>/<photoId>.<ext>        (ext kept from the source key)
  exif       -> exif/<eventId>/<photoId>.exif.json
  originals  -> originals/<eventId>/<photoId>.<ext>
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from keepframe.io import s3, source

# Source bucket names: SOURCE_BUCKET_PROCESSED / SOURCE_BUCKET_ORIGINALS (keepframe/io/source.py), read when a
# job list is built, with no default. EXIF jobs use the bucket recorded per row by the exporter.


@dataclass(frozen=True)
class Job:
    src_bucket: str
    src_key: str
    dest_key: str


@dataclass
class Stats:
    ok: int = 0
    skipped: int = 0
    missing: int = 0
    failed: int = 0
    bytes: int = 0
    started: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        dt = max(time.time() - self.started, 1e-6)
        return {
            "ok": self.ok, "skipped": self.skipped, "missing": self.missing, "failed": self.failed,
            "bytes": self.bytes, "gb": round(self.bytes / 1e9, 3), "seconds": round(dt, 1),
            "mb_per_s": round(self.bytes / 1e6 / dt, 1), "obj_per_s": round(self.ok / dt, 1),
            "errors_sample": self.errors[:5],
        }


def _ext(key: str) -> str:
    return key.rsplit(".", 1)[-1] if "." in key.rsplit("/", 1)[-1] else "bin"


def jobs_for_rows(
    rows: Iterable[dict[str, Any]],
    kinds: set[str],
    photo_ids: set[str] | None = None,
    dest_prefix: str | None = None,
) -> list[Job]:
    """Build copy jobs from manifest rows. ``dest_prefix`` overrides raw/ for processed (bench)."""
    out: list[Job] = []
    for r in rows:
        ev, pid = r["event_id"], r["photo_id"]
        if photo_ids is not None and pid not in photo_ids:
            continue
        if "processed" in kinds and r.get("processed_key"):
            base = f"{dest_prefix.rstrip('/')}/{ev}" if dest_prefix else f"raw/{ev}"
            out.append(Job(source.bucket("processed"), r["processed_key"], f"{base}/{pid}.{_ext(r['processed_key'])}"))
        if "exif" in kinds and r.get("exif_key") and r.get("exif_bucket"):
            out.append(Job(r["exif_bucket"], r["exif_key"], f"exif/{ev}/{pid}.exif.json"))
        if "originals" in kinds and r.get("original_key"):
            out.append(Job(source.bucket("originals"), r["original_key"], f"originals/{ev}/{pid}.{_ext(r['original_key'])}"))
    return out


def _existing_sizes(prefixes: set[str]) -> dict[str, int]:
    bucket = s3.work_bucket()
    sizes: dict[str, int] = {}
    if bucket is None:
        root = s3.local_root()
        for p in prefixes:
            d = root / p
            if d.exists():
                for f in d.rglob("*"):
                    if f.is_file():
                        sizes[f.relative_to(root).as_posix()] = f.stat().st_size
        return sizes
    pag = s3.s3_client().get_paginator("list_objects_v2")
    for p in prefixes:
        for page in pag.paginate(Bucket=bucket, Prefix=p):
            for o in page.get("Contents", []):
                sizes[o["Key"]] = o["Size"]
    return sizes


def _copy_one(job: Job, stats: Stats, existing: dict[str, int]) -> None:
    from botocore.exceptions import ClientError

    src = s3.source_client()
    try:
        obj = src.get_object(Bucket=job.src_bucket, Key=job.src_key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        with stats.lock:
            if code in ("NoSuchKey", "404", "NotFound"):
                stats.missing += 1
            else:
                stats.failed += 1
                stats.errors.append(f"GET {code}")
        return
    size = int(obj.get("ContentLength") or 0)
    if existing.get(job.dest_key) == size:
        obj["Body"].close()
        with stats.lock:
            stats.skipped += 1
        return
    ctype = obj.get("ContentType") or "application/octet-stream"
    try:
        bucket = s3.work_bucket()
        if bucket is None:
            s3.put_bytes(job.dest_key, obj["Body"].read(), ctype)
        else:
            s3.s3_client().upload_fileobj(
                obj["Body"], bucket, job.dest_key, ExtraArgs={"ContentType": ctype}
            )
    except Exception as e:  # noqa: BLE001 - count and continue
        with stats.lock:
            stats.failed += 1
            stats.errors.append(f"PUT {type(e).__name__}")
        return
    with stats.lock:
        stats.ok += 1
        stats.bytes += size


def run_jobs(jobs: list[Job], threads: int = 32, log_every_s: float = 30.0, label: str = "") -> Stats:
    prefixes = {j.dest_key.rsplit("/", 1)[0] + "/" for j in jobs}
    existing = _existing_sizes(prefixes)
    stats = Stats()
    last = time.time()
    with ThreadPoolExecutor(threads) as ex:
        futs = [ex.submit(_copy_one, j, stats, existing) for j in jobs]
        for i, _ in enumerate(as_completed(futs), 1):
            if time.time() - last >= log_every_s:
                last = time.time()
                print(json.dumps({"label": label, "done": i, "of": len(jobs), **stats.as_dict()}), flush=True)
    return stats


def _load_rows(event_id: str | None, manifest: str | None, prefix: str) -> list[dict[str, Any]]:
    if manifest:
        text = Path(manifest).read_text() if not manifest.startswith("s3://") else s3.get_bytes(manifest).decode()
        return [json.loads(x) for x in text.splitlines() if x.strip()]
    from keepframe.io.manifest import read_manifest_dicts

    assert event_id
    return read_manifest_dicts(event_id, prefix)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event-id")
    ap.add_argument("--keys-file", help="local or s3:// manifest-shaped JSONL (e.g. bench/manifest_5k.jsonl)")
    ap.add_argument("--manifest-prefix", default="manifests/")
    ap.add_argument("--kinds", default="processed,exif", help="comma list of processed,exif,originals")
    ap.add_argument("--photo-ids-file", help="restrict to these photo ids (one per line); for originals pass")
    ap.add_argument("--dest-prefix", help="override raw/ for processed (bench: bench/5k)")
    ap.add_argument("--threads", type=int, default=32)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    if not (args.event_id or args.keys_file):
        ap.error("--event-id or --keys-file required")

    rows = _load_rows(args.event_id, args.keys_file, args.manifest_prefix)
    ids = None
    if args.photo_ids_file:
        ids = {x.strip() for x in Path(args.photo_ids_file).read_text().splitlines() if x.strip()}
    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}
    jobs = jobs_for_rows(rows, kinds, ids, args.dest_prefix)
    if args.limit:
        jobs = jobs[: args.limit]
    label = args.event_id or Path(args.keys_file).name
    print(json.dumps({"label": label, "jobs": len(jobs), "kinds": sorted(kinds), "threads": args.threads,
                      "dest": s3.work_bucket() or str(s3.local_root())}), flush=True)
    stats = run_jobs(jobs, args.threads, label=label)
    print(json.dumps({"label": label, "final": True, "jobs": len(jobs), **stats.as_dict()}), flush=True)
    return 1 if stats.failed else 0


if __name__ == "__main__":
    sys.exit(main())

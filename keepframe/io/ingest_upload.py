"""Ingest a photographer's upload: ``raw/<eventId>/`` in the work bucket -> manifest + EXIF sidecars.

    python -m keepframe.io.ingest_upload --event-id <id>            # build manifests/<id>.jsonl if absent
    python -m keepframe.io.ingest_upload --event-id <id> --force    # rebuild it

The photographer (or their tooling) uploads an event's photos to ``s3://$WORK_BUCKET/raw/<eventId>/``. This is
the ``Ingest`` step of ``CullEvent``. For every image it:

* reads only the first 256 KiB (one ranged GET) and parses the JPEG EXIF block: capture time with sub-second
  and offset when present, camera model, exposure, ISO, aperture, focal length;
* writes ``exif/<eventId>/<photoId>.exif.json`` (flat sidecar, the schema ``keepframe.io.exif`` reads) unless
  one was uploaded already;
* writes ``manifests/<eventId>.jsonl`` (sorted by capture time, then photo id) and ``.meta.json``.

Photo ids are the file stems. A stem that is not a safe id (``[A-Za-z0-9_.-]``, max 100 chars) is copied
server-side to a safe key; the uploaded object is left in place (nothing is ever deleted).
When the manifest already exists (imported events, re-runs) the step does nothing unless ``--force``.
Pixels are never decoded here; the worker reads the full images.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from keepframe.io import manifest, s3

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
HEAD_BYTES = 256 * 1024
_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")

# EXIF tag ids -> sidecar field names (flat "small" schema, keepframe/io/exif.py aliases)
_TAGS = {
    0x0110: "Model", 0x0132: "DateTime", 0x829A: "ExposureTime", 0x829D: "FNumber", 0x8827: "ISOSpeedRatings",
    0x9003: "DateTimeOriginal", 0x9004: "DateTimeDigitized", 0x9011: "OffsetTimeOriginal",
    0x9291: "SubSecTimeOriginal", 0x920A: "FocalLength",
}
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


def _tiff_values(tiff: bytes) -> dict[str, Any]:
    """Parse IFD0 + the Exif sub-IFD of a TIFF block. Unknown or malformed entries are skipped."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return {}
    e = "<" if tiff[:2] == b"II" else ">"
    out: dict[str, Any] = {}

    def value(typ: int, count: int, raw: bytes, off_field: int) -> Any:
        size = _TYPE_SIZE.get(typ, 0) * count
        data = raw if size <= 4 else tiff[off_field:off_field + size]
        if len(data) < size or size == 0:
            return None
        if typ == 2:
            return data[:size].split(b"\0", 1)[0].decode("ascii", "replace").strip()
        if typ == 3:
            return struct.unpack(e + "H", data[:2])[0]
        if typ in (4, 9):
            return struct.unpack(e + ("I" if typ == 4 else "i"), data[:4])[0]
        if typ in (5, 10):
            n, d = struct.unpack(e + ("II" if typ == 5 else "ii"), data[:8])
            return n / d if d else None
        return None

    def ifd(offset: int, depth: int = 0) -> None:
        if depth > 2 or offset <= 0 or offset + 2 > len(tiff):
            return
        (n,) = struct.unpack(e + "H", tiff[offset:offset + 2])
        for i in range(min(n, 512)):
            p = offset + 2 + 12 * i
            if p + 12 > len(tiff):
                return
            tag, typ, count = struct.unpack(e + "HHI", tiff[p:p + 8])
            raw = tiff[p + 8:p + 12]
            (off_field,) = struct.unpack(e + "I", raw)
            if tag == 0x8769:  # Exif sub-IFD pointer
                ifd(off_field, depth + 1)
            elif tag in _TAGS:
                v = value(typ, count, raw, off_field)
                if v not in (None, ""):
                    out.setdefault(_TAGS[tag], v)

    (first,) = struct.unpack(e + "I", tiff[4:8])
    ifd(first)
    return out


def parse_jpeg_exif(head: bytes) -> dict[str, Any]:
    """EXIF fields from the start of a JPEG file ({} for other formats or no APP1 Exif block)."""
    if head[:2] != b"\xff\xd8":
        return {}
    i = 2
    while i + 4 <= len(head) and head[i] == 0xFF:
        marker = head[i + 1]
        if marker in (0xD9, 0xDA):  # end of image / start of scan: no metadata after this
            break
        (seg_len,) = struct.unpack(">H", head[i + 2:i + 4])
        seg = head[i + 4:i + 2 + seg_len]
        if marker == 0xE1 and seg[:6] == b"Exif\0\0":
            return _tiff_values(seg[6:])
        i += 2 + seg_len
    return {}


def _capture_iso(ex: dict[str, Any]) -> str | None:
    """Capture time as ISO-8601. With OffsetTimeOriginal it is exact UTC; without, the camera's local wall
    clock is written as if UTC (consistent within one event, which is all burst grouping needs)."""
    s = ex.get("DateTimeOriginal") or ex.get("DateTimeDigitized") or ex.get("DateTime")
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    sub = re.sub(r"\D", "", str(ex.get("SubSecTimeOriginal") or ""))
    if sub:
        dt = dt.replace(microsecond=int(round(float("0." + sub) * 1e6)) % 1_000_000)
    off = str(ex.get("OffsetTimeOriginal") or "")
    m = re.match(r"^([+-])(\d{2}):?(\d{2})$", off)
    if m:
        aware = datetime.fromisoformat(dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + f"{m.group(1)}{m.group(2)}:{m.group(3)}")
        dt = aware.astimezone(UTC).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def safe_id(stem: str, taken: set[str]) -> str:
    """The stem when it is a safe id, else a sanitised stem plus a short hash; unique within ``taken``."""
    pid = stem if _SAFE.match(stem) else (re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)[:80].strip("._") or "photo") \
        + "_" + hashlib.sha256(stem.encode()).hexdigest()[:8]
    base, n = pid, 1
    while pid in taken:
        n += 1
        pid = f"{base}_{n}"
    taken.add(pid)
    return pid


def _head(key: str) -> bytes:
    if s3.local_mode():
        with open(s3.local_root() / key, "rb") as f:
            return f.read(HEAD_BYTES)
    bucket, k = s3.work_bucket(), key
    body: bytes = s3.s3_client().get_object(Bucket=bucket, Key=k, Range=f"bytes=0-{HEAD_BYTES - 1}")["Body"].read()
    return body


def _copy(src: str, dst: str) -> None:
    if s3.local_mode():
        s3.put_bytes(dst, s3.get_bytes(src))
        return
    bucket = s3.work_bucket()
    s3.s3_client().copy_object(Bucket=bucket, Key=dst, CopySource={"Bucket": bucket, "Key": src})


def ingest_upload(event_id: str, force: bool = False, threads: int = 32) -> dict[str, Any]:
    if not _SAFE.match(event_id) or event_id in (".", ".."):
        raise ValueError("event_id must match [A-Za-z0-9_.-]{1,100}")
    mkey = manifest.manifest_key(event_id)
    if s3.exists(mkey) and not force:
        return {"event_id": event_id, "status": "manifest_present", "manifest": mkey}
    prefix = f"raw/{event_id}/"
    keys = sorted(k for k in s3.list_keys(prefix)
                  if "/" not in k[len(prefix):] and PurePosixPath(k).suffix.lower() in IMAGE_EXT)
    if not keys:
        raise FileNotFoundError(f"no images under {prefix} (upload the event's photos there first)")
    have_exif = set(s3.list_keys(f"exif/{event_id}/"))

    # A re-run (force) sees the safe copies an earlier run made next to unsafe uploads: count each photo once.
    copies = {f"{prefix}{safe_id(PurePosixPath(k).stem, set())}{PurePosixPath(k).suffix.lower()}"
              for k in keys if not _SAFE.match(PurePosixPath(k).stem)}
    keys = [k for k in keys if k not in copies]

    taken: set[str] = set()
    plan: list[tuple[str, str, str]] = []  # (uploaded key, photo id, raw key the worker reads)
    renamed = 0
    for k in keys:
        p = PurePosixPath(k)
        pid = safe_id(p.stem, taken)
        dst = f"{prefix}{pid}{p.suffix.lower() if pid != p.stem else p.suffix}"
        renamed += dst != k
        plan.append((k, pid, dst))

    def one(item: tuple[str, str, str]) -> dict[str, Any]:
        src, pid, dst = item
        if dst != src:
            _copy(src, dst)
        ex = parse_jpeg_exif(_head(src))
        ekey = f"exif/{event_id}/{pid}.exif.json"
        if ex and ekey not in have_exif:
            s3.put_json(ekey, ex)
        return {
            "photo_id": pid, "event_id": event_id, "name": PurePosixPath(src).name,
            "clicked_at": _capture_iso(ex), "camera": (str(ex["Model"]).strip() or None) if ex.get("Model") else None,
            "processed_key": f"{event_id}/{PurePosixPath(dst).name}",
            "exif_key": ekey if (ex or ekey in have_exif) else None,
        }

    with ThreadPoolExecutor(threads) as pool:
        rows = list(pool.map(one, plan))
    rows.sort(key=lambda r: (r["clicked_at"] is None, r["clicked_at"] or "", r["photo_id"]))
    manifest.write_manifest(event_id, rows)
    cameras = sorted({r["camera"] for r in rows if r["camera"]})
    manifest.write_meta(event_id, {
        "event_id": event_id, "n_photos": len(rows), "n_with_original": 0, "cameras": cameras,
        "exported_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "upload",
        "validation": {"with_capture_time": sum(1 for r in rows if r["clicked_at"]), "renamed": renamed},
    })
    return {"event_id": event_id, "status": "ingested", "manifest": mkey, "n_photos": len(rows),
            "with_capture_time": sum(1 for r in rows if r["clicked_at"]), "renamed": renamed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event-id", required=True)
    ap.add_argument("--force", action="store_true", help="rebuild the manifest even if one exists")
    ap.add_argument("--threads", type=int, default=32)
    a = ap.parse_args(argv)
    print(json.dumps(ingest_upload(a.event_id, a.force, a.threads)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

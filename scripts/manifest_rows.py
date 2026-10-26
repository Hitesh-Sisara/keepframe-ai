"""Shared row shaping for the N1QL (Couchbase) and Postgres manifest exporters.

Both stores hold the same source Photo document, so ``shape_photo(doc_id, doc)`` maps one raw
document (camelCase) to one manifest row (docs/DATA.md manifest fields).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from keepframe.io import source  # noqa: E402

# Bucket names and CDN hosts come from SOURCE_* env vars (keepframe/io/source.py); there are no defaults.
_RICH = object()  # sentinel: "use the configured rich EXIF bucket"

EXCLUDED_PREFIXES = ("generated-", "ftp-", "highlight-")
EXCLUDED_SUFFIXES = ("-GUESTALBUM",)

# Fields the exporters select (never exifData: large JSON string).
PHOTO_FIELDS = (
    "event", "album", "name", "clickedAt", "createdAt", "camera", "lens", "width", "height",
    "originalWidth", "originalHeight", "originalImageKb", "md5", "gcsKey", "originalKey",
    "s3FileName", "mfaces", "faces", "frsInfo",
)


def album_excluded(album_id: str | None, album_name: str | None = None) -> bool:
    for v in (album_id, album_name):
        if not v:
            continue
        if v.startswith(EXCLUDED_PREFIXES) or v.endswith(EXCLUDED_SUFFIXES):
            return True
    return False


def _num(v: Any, kind: type = int) -> Any:
    if v is None or v == "":
        return None
    try:
        return kind(float(v)) if kind is int else kind(v)
    except (TypeError, ValueError):
        return None


def _iso(v: Any) -> str | None:
    """Normalise ISO strings / epoch (s or ms) to ISO-8601 UTC 'YYYY-MM-DDTHH:MM:SS[.fff]Z'."""
    if v is None or v == "":
        return None
    if isinstance(v, int | float):
        x = float(v)
        x = x / 1000.0 if x > 1e11 else x
        return datetime.fromtimestamp(x, UTC).isoformat().replace("+00:00", "Z")
    return str(v)


def _bbox(b: Any) -> list[float] | None:
    if not isinstance(b, dict):
        return None
    try:
        return [round(float(b[k]), 6) for k in ("left", "top", "width", "height")]
    except (KeyError, TypeError, ValueError):
        return None


def processed_key(doc: dict[str, Any]) -> str | None:
    k = doc.get("gcsKey")
    if k:
        return str(k)
    if doc.get("s3FileName") and doc.get("event"):
        return f"{doc['event']}/{doc['s3FileName']}"
    return None


def shape_photo(photo_id: str, doc: dict[str, Any], exif_bucket: str | None | object = _RICH) -> dict[str, Any]:
    if exif_bucket is _RICH:
        exif_bucket = source.bucket("exif_rich")
    assert exif_bucket is None or isinstance(exif_bucket, str)
    event_id = str(doc.get("event"))
    pk = processed_key(doc)
    ok = doc.get("originalKey") or None
    frs = doc.get("frsInfo") or {}
    frs_faces = []
    for f in frs.get("faces") or []:
        bb = _bbox(f.get("boundingBox"))
        if bb:
            frs_faces.append({"bbox": bb, "det_score": _num(f.get("detScore"), float),
                              "min_px": _num(f.get("minPx"), float)})
    rek_faces = []
    for f in doc.get("faces") or []:
        bb = _bbox(f.get("boundingBox") or f.get("BoundingBox"))
        if bb:
            rek_faces.append({"bbox": bb, "confidence": _num(f.get("confidence") or f.get("Confidence"), float)})
    return {
        "photo_id": photo_id,
        "event_id": event_id,
        "album": doc.get("album"),
        "name": doc.get("name"),
        "clicked_at": _iso(doc.get("clickedAt")),
        "created_at": _iso(doc.get("createdAt")),
        "camera": doc.get("camera") or None,
        "lens": doc.get("lens") or None,
        "width": _num(doc.get("width")),
        "height": _num(doc.get("height")),
        "original_width": _num(doc.get("originalWidth")),
        "original_height": _num(doc.get("originalHeight")),
        "original_kb": _num(doc.get("originalImageKb"), float),
        "md5": doc.get("md5") or None,
        "processed_key": pk,
        "original_key": ok,
        "exif_key": f"{event_id}/{photo_id}.exif.json" if exif_bucket else None,
        "exif_bucket": exif_bucket,
        "mfaces": _num(doc.get("mfaces")),
        "frs_status": frs.get("status"),
        "frs_faces": frs_faces,
        "frs_src_w": _num(frs.get("sourceImageWidth")),
        "frs_src_h": _num(frs.get("sourceImageHeight")),
        "rek_faces": rek_faces,
        "processed_url": source.cdn_base("processed") + pk if pk else None,
        "original_url": source.cdn_base("originals") + ok if ok else None,
    }


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Manifest order: clicked_at (missing last), then photo_id; deterministic."""
    return sorted(rows, key=lambda r: (r["clicked_at"] is None, r["clicked_at"] or "", r["photo_id"]))


def shape_meta(event_id: str, ev: dict[str, Any], rows: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    cams = sorted({r["camera"] for r in rows if r.get("camera")})
    return {
        "event_id": event_id,
        "name": ev.get("name"),
        "category": ev.get("category"),
        "event_date": _iso(ev.get("eventDate")),
        "whitelabel_id": ev.get("whitelabelId"),
        "sort_by": ev.get("sortBy"),
        "n_photos": len(rows),
        "n_with_original": sum(1 for r in rows if r.get("original_key")),
        "cameras": cams,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        **extra,
    }


def same_second_bursts(rows: list[dict[str, Any]], min_size: int = 2) -> dict[str, Any]:
    """Sanity check: photos sharing (camera, clicked_at second)."""
    groups: dict[tuple[str, str], int] = {}
    for r in rows:
        if r.get("clicked_at"):
            k = (r.get("camera") or "?", str(r["clicked_at"])[:19])
            groups[k] = groups.get(k, 0) + 1
    sizes = sorted((n for n in groups.values() if n >= min_size), reverse=True)
    hist: dict[str, int] = {}
    for n in sizes:
        b = str(n) if n < 6 else "6+"
        hist[b] = hist.get(b, 0) + 1
    return {
        "n_rows": len(rows),
        "n_with_clicked_at": sum(1 for r in rows if r.get("clicked_at")),
        "n_bursts": len(sizes),
        "n_photos_in_bursts": sum(sizes),
        "largest": sizes[:5],
        "size_hist": hist,
    }

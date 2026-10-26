"""EXIF sidecar reader (exif/<eventId>/<photoId>.exif.json, copied verbatim from the source, or written by Ingest).

Two prod schemas (verified by the data agent, docs/DATA.md §4):
  small  $SOURCE_BUCKET_EXIF_SMALL, flat: DateTimeOriginal "YYYY:MM:DD HH:MM:SS" (local, no TZ), ExposureTime float s,
         ISOSpeedRatings int, FNumber float. No sub-second field.
  rich   $SOURCE_BUCKET_EXIF_RICH, nested (basicExif / exifToolData / cameraInfo / processingInfo ...):
         exifToolData.SubSecCreateDate "YYYY:MM:DD HH:MM:SS.sss+HH:MM" (best timestamp),
         exifToolData.SubSecTimeOriginal int / basicExif.SubSecTimeOriginal str, exifToolData.CreateDate,
         ExposureTime string "1/160", exifToolData.ISO / cameraInfo.ISO. DateTimeOriginal usually absent.
Detection is by shape ("basicExif"/"exifToolData" present -> rich); preferred sections are read first,
then a generic case-insensitive alias search over everything else (exiftool -json lists, "EXIF:" prefixes).

Privacy: sidecars contain camera/lens serial numbers. Only the fields of ExifInfo are extracted; the raw
document is never logged or stored by this module.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from fractions import Fraction
from typing import Any

from pydantic import BaseModel

ALIASES: dict[str, tuple[str, ...]] = {
    "ExposureTime": ("exposuretime", "exposure_time", "shutterspeed", "shutterspeedvalue_s"),
    "ISO": ("iso", "isospeedratings", "photographicsensitivity", "isospeed", "recommendedexposureindex"),
    "SubSecTimeOriginal": ("subsectimeoriginal", "subsectime", "subsec"),
    "SubSecDateTime": ("subseccreatedate", "subsecdatetimeoriginal"),
    "DateTimeOriginal": ("datetimeoriginal", "createdate", "datetimedigitized", "datetime"),
    "OffsetTimeOriginal": ("offsettimeoriginal", "offsettime", "timezoneoffset"),
    "FNumber": ("fnumber", "aperturevalue", "aperture"),
    "FocalLength": ("focallength",),
    "Model": ("model", "cameramodelname"),
}
RICH_SECTIONS = ("exifToolData", "basicExif", "cameraInfo")  # in priority order
SKIP_SECTIONS = ("processingInfo", "colorProfile", "extractionInfo")


class ExifInfo(BaseModel):
    schema_kind: str = "unknown"         # small | rich | unknown
    exposure_time_s: float | None = None
    iso: int | None = None
    subsec: float | None = None          # fractional seconds, e.g. "045" -> 0.045
    datetime_original: str | None = None # ISO 8601 (with offset if known)
    ts: float | None = None              # epoch seconds incl. subsec (UTC if offset known, else naive-as-UTC)
    local_hour: int | None = None        # wall-clock hour at capture (for the reception/dance-floor guard)
    tz_known: bool = False
    f_number: float | None = None
    focal_length_mm: float | None = None
    model: str | None = None


def _flatten(obj: Any, out: dict[str, Any]) -> None:
    if isinstance(obj, list):
        for item in obj[:1] if obj and isinstance(obj[0], dict) else []:
            _flatten(item, out)
        return
    if not isinstance(obj, dict):
        return
    for k, v in obj.items():
        if k in SKIP_SECTIONS:
            continue
        name = str(k).split(":")[-1].replace(" ", "").lower()
        if isinstance(v, dict) and not {"value", "val", "description"} & set(v):
            _flatten(v, out)
            continue
        if isinstance(v, dict):
            v = v.get("value", v.get("val", v.get("description")))
        out.setdefault(name, v)


def _find(flat: dict[str, Any], canonical: str) -> Any:
    for alias in ALIASES[canonical]:
        if alias in flat and flat[alias] not in (None, ""):
            return flat[alias]
    return None


def _num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, list) and v:
        return _num(v[0])
    s = str(v).strip().lower().removesuffix("s").removesuffix("sec").strip()
    s = s.removeprefix("f/").removeprefix("iso").replace("mm", "").strip()
    m = re.match(r"^\s*([0-9.]+)\s*/\s*([0-9.]+)", s)
    try:
        if m:
            return float(Fraction(m.group(1)) / Fraction(m.group(2)))
        return float(s.split()[0])
    except (ValueError, ZeroDivisionError, IndexError):
        return None


def _parse_offset(v: Any) -> timezone | None:
    if v is None:
        return None
    m = re.match(r"^([+-])(\d{1,2}):?(\d{2})$", str(v).strip())
    if not m:
        return None
    sign = 1 if m.group(1) == "+" else -1
    return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))


def _parse_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, int | float):
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=UTC)
    s = str(v).strip()
    m = re.match(r"^(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})(\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?", s)
    if not m:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    y, mo, d, h, mi, se = (int(m.group(i)) for i in range(1, 7))
    frac = float(m.group(7)) if m.group(7) else 0.0
    tz = UTC if m.group(8) == "Z" else _parse_offset(m.group(8))
    try:
        dt = datetime(y, mo, d, h, mi, se, int(round(frac * 1e6)) % 1_000_000, tzinfo=tz)
    except ValueError:
        return None
    return dt


def parse_exif(doc: Any) -> ExifInfo:
    """Parse an already-loaded sidecar (dict or list), small or rich schema."""
    flat: dict[str, Any] = {}
    kind = "unknown"
    if isinstance(doc, dict) and any(sec in doc for sec in RICH_SECTIONS):
        kind = "rich"
        for sec in RICH_SECTIONS:  # preferred sections first: setdefault keeps the first value seen
            _flatten(doc.get(sec), flat)
    elif isinstance(doc, dict) and doc:
        kind = "small"
    _flatten(doc, flat)
    info = ExifInfo(schema_kind=kind)
    info.exposure_time_s = _num(_find(flat, "ExposureTime"))
    iso = _num(_find(flat, "ISO"))
    info.iso = int(iso) if iso else None
    ss = _find(flat, "SubSecTimeOriginal")
    if ss is not None:
        digits = re.sub(r"\D", "", str(ss))
        info.subsec = float("0." + digits) if digits else None
    info.f_number = _num(_find(flat, "FNumber"))
    info.focal_length_mm = _num(_find(flat, "FocalLength"))
    model = _find(flat, "Model")
    info.model = str(model) if model is not None else None
    dt = _parse_dt(_find(flat, "SubSecDateTime")) or _parse_dt(_find(flat, "DateTimeOriginal"))
    if dt is not None:
        if dt.tzinfo is None:
            off = _parse_offset(_find(flat, "OffsetTimeOriginal"))
            if off is not None:
                dt = dt.replace(tzinfo=off)
        info.tz_known = dt.tzinfo is not None
        info.local_hour = dt.hour
        if info.subsec is not None and dt.microsecond == 0:
            dt = dt.replace(microsecond=int(round(info.subsec * 1e6)) % 1_000_000)
        info.datetime_original = dt.isoformat()
        info.ts = (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()
    return info


def parse_exif_bytes(data: bytes) -> ExifInfo:
    try:
        return parse_exif(json.loads(data))
    except (ValueError, UnicodeDecodeError):
        return ExifInfo()


def exif_key(event_id: str, photo_id: str) -> str:
    return f"exif/{event_id}/{photo_id}.exif.json"


def load_exif(event_id: str, photo_id: str) -> ExifInfo | None:
    """Parsed sidecar for one photo; None when absent.

    Order: the ingested event context (already parsed), the local disk cache, then one GET (no HEAD).
    """
    from keepframe.io import proxies

    try:
        from keepframe.tools.ingest import _contexts

        ctx = _contexts.get(event_id)
        if ctx is not None and photo_id in ctx.photos and ctx.photos[photo_id].exif is not None:
            return ctx.photos[photo_id].exif
    except ImportError:
        pass
    try:
        return parse_exif_bytes(proxies.cached_bytes(exif_key(event_id, photo_id)))
    except (FileNotFoundError, KeyError):
        return None
    except Exception as e:  # botocore NoSuchKey / ClientError 404 from the S3 module
        if "NoSuchKey" in type(e).__name__ or "404" in str(e) or "NoSuchKey" in str(e):
            return None
        raise

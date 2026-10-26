"""Manifest I/O: ``manifests/<eventId>.jsonl`` (one PhotoRecord per line) + ``.meta.json``.

``PhotoRecord`` / ``EventMeta`` come from ``keepframe.schemas``. Until that
module is merged, a field-identical fallback is defined here so the data tooling runs standalone.
Extra manifest fields written by the exporter (``exif_bucket``, ``ext``) pass through ``extra="allow"``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from keepframe.io import s3

try:  # pragma: no cover - exercised once schemas.py is merged
    from keepframe.schemas import EventMeta, FrsFace, PhotoRecord, RekFace
except ImportError:  # fallback with EXACTLY the contract fields

    class FrsFace(BaseModel):  # type: ignore[no-redef]
        model_config = ConfigDict(extra="allow")
        bbox: list[float]
        det_score: float | None = None
        min_px: float | None = None

    class RekFace(BaseModel):  # type: ignore[no-redef]
        model_config = ConfigDict(extra="allow")
        bbox: list[float]
        confidence: float | None = None

    class PhotoRecord(BaseModel):  # type: ignore[no-redef]
        model_config = ConfigDict(extra="allow")
        photo_id: str
        event_id: str
        album: str | None = None
        name: str | None = None
        clicked_at: str | float | int | None = None
        created_at: str | float | int | None = None
        camera: str | None = None
        lens: str | None = None
        width: int | None = None
        height: int | None = None
        original_width: int | None = None
        original_height: int | None = None
        original_kb: float | None = None
        md5: str | None = None
        processed_key: str | None = None
        original_key: str | None = None
        exif_key: str | None = None
        mfaces: int | None = None
        frs_status: str | None = None
        frs_faces: list[FrsFace] = Field(default_factory=list)
        frs_src_w: int | None = None
        frs_src_h: int | None = None
        rek_faces: list[RekFace] = Field(default_factory=list)
        processed_url: str | None = None
        original_url: str | None = None

    class EventMeta(BaseModel):  # type: ignore[no-redef]
        model_config = ConfigDict(extra="allow")
        event_id: str
        name: str | None = None
        category: str | None = None
        event_date: str | None = None
        whitelabel_id: str | None = None
        sort_by: str | None = None
        n_photos: int | None = None
        n_with_original: int | None = None
        cameras: list[str] = Field(default_factory=list)
        exported_at: str | None = None


__all__ = [
    "EventMeta",
    "FrsFace",
    "PhotoRecord",
    "RekFace",
    "manifest_key",
    "meta_key",
    "read_manifest",
    "read_manifest_dicts",
    "read_meta",
    "write_manifest",
    "write_meta",
    "dumps_jsonl",
]


def manifest_key(event_id: str, prefix: str = "manifests/") -> str:
    return f"{prefix.rstrip('/')}/{event_id}.jsonl"


def meta_key(event_id: str, prefix: str = "manifests/") -> str:
    return f"{prefix.rstrip('/')}/{event_id}.meta.json"


def read_manifest_dicts(event_id: str, prefix: str = "manifests/") -> list[dict[str, Any]]:
    raw = s3.get_bytes(manifest_key(event_id, prefix)).decode()
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def read_manifest(event_id: str, prefix: str = "manifests/") -> list[PhotoRecord]:
    """All photos of one event, in manifest order (clicked_at, then photo_id)."""
    return [PhotoRecord.model_validate(d) for d in read_manifest_dicts(event_id, prefix)]


def read_meta(event_id: str, prefix: str = "manifests/") -> EventMeta:
    return EventMeta.model_validate(s3.get_json(meta_key(event_id, prefix)))


def dumps_jsonl(rows: Iterable[dict[str, Any] | BaseModel]) -> bytes:
    out = []
    for r in rows:
        d = r.model_dump(mode="json") if isinstance(r, BaseModel) else r
        out.append(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
    return ("\n".join(out) + "\n").encode() if out else b""


def write_manifest(
    event_id: str, rows: Iterable[dict[str, Any] | BaseModel], prefix: str = "manifests/"
) -> str:
    key = manifest_key(event_id, prefix)
    s3.put_bytes(key, dumps_jsonl(rows), "application/x-ndjson")
    return key


def write_meta(event_id: str, meta: dict[str, Any] | BaseModel, prefix: str = "manifests/") -> str:
    key = meta_key(event_id, prefix)
    d = meta.model_dump(mode="json") if isinstance(meta, BaseModel) else meta
    s3.put_json(key, d)
    return key

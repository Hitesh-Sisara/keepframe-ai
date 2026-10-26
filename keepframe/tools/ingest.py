"""ingest_event tool + the in-process event context shared by the other tools.

The agent runtime calls tools one at a time in a single process, so per-event state (photo list,
hashes, groups, per-photo tool results) lives in an `EventContext` keyed by event_id. The batch
worker (keepframe.worker) uses the same context in the parent process and pure per-image
functions in its pool workers.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from keepframe.io import proxies
from keepframe.io.exif import ExifInfo, exif_key, parse_exif_bytes
from keepframe.schemas import IngestResult, PhotoRecord, TimeSpan


@dataclass
class PhotoInfo:
    record: PhotoRecord
    exif: ExifInfo | None = None
    has_raw: bool = True

    @property
    def photo_id(self) -> str:
        return self.record.photo_id

    @property
    def camera(self) -> str:
        return (self.record.camera or "").strip() or "unknown"

    @property
    def ts(self) -> float | None:
        """Capture time in epoch seconds (UTC).

        clickedAt is second-granular, so add the EXIF sub-second (rich sidecar SubSecCreateDate /
        SubSecTimeOriginal) when present (docs/REPORT.md §3). Small sidecars and most events have no sub-second
        field; then frames in the same second share a ts and `order_key` breaks the tie by filename.
        Without clickedAt: EXIF capture time (only if its timezone is known), then createdAt.
        """
        clicked = self.record.clicked_ts()
        sub = self.exif.subsec if self.exif and self.exif.subsec is not None else None
        if clicked is not None:
            if sub is not None and float(clicked).is_integer():
                return math.floor(clicked) + sub
            return float(clicked)
        if self.exif and self.exif.ts is not None and self.exif.tz_known:
            return self.exif.ts
        return self.record.created_ts()

    @property
    def has_original(self) -> bool:
        return bool(self.record.original_key)

    @property
    def order_key(self) -> tuple[float, tuple[Any, ...], str]:
        """Capture order: ts, then filename with numeric runs compared as numbers, then photo_id."""
        return (self.ts if self.ts is not None else float("inf"), natural_key(self.record.name or ""),
                self.photo_id)


def natural_key(name: str) -> tuple[Any, ...]:
    """'IMG_0099.jpg' < 'IMG_0100.jpg' < 'IMG_100a.jpg' (digits compared numerically)."""
    import re

    return tuple(int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name) if t)


@dataclass
class EventContext:
    event_id: str
    photos: dict[str, PhotoInfo] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    hashes: dict[str, dict[str, Any]] = field(default_factory=dict)
    groups: dict[str, list[str]] = field(default_factory=dict)
    group_of: dict[str, str] = field(default_factory=dict)
    exact_duplicates: dict[str, str] = field(default_factory=dict)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)  # photo_id -> {tool_key: result}

    def record(self, photo_id: str, key: str, result: dict[str, Any]) -> None:
        self.results.setdefault(photo_id, {})[key] = result


_contexts: dict[str, EventContext] = {}
_photo_event: dict[str, str] = {}
_lock = threading.Lock()


def get_context(event_id: str, create: bool = True) -> EventContext:
    with _lock:
        ctx = _contexts.get(event_id)
    if ctx is None:
        if not create:
            raise KeyError(f"event {event_id} not ingested")
        ingest_event(event_id)
        ctx = _contexts[event_id]
    return ctx


def resolve_event(photo_id: str, event_id: str | None = None) -> str:
    """Analysis tools take only photo_id; map it back to the ingested event."""
    if event_id:
        return event_id
    try:
        return _photo_event[photo_id]
    except KeyError as e:
        raise KeyError(f"photo {photo_id} unknown: call ingest_event first or pass event_id") from e


def reset_contexts() -> None:
    with _lock:
        _contexts.clear()
        _photo_event.clear()


def read_manifest_records(event_id: str) -> list[PhotoRecord]:
    """manifests/<eventId>.jsonl read through the work store (so the GET is counted in summary.json) and
    parsed into PhotoRecord, the same model keepframe.io.manifest.read_manifest returns."""
    data = proxies.get_store().get_bytes(f"manifests/{event_id}.jsonl")
    out: list[PhotoRecord] = []
    for line in data.decode("utf-8").splitlines():
        line = line.strip()
        if line:
            rec = PhotoRecord.model_validate(json.loads(line))
            out.append(rec)
    return out


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")


def exif_index_key(event_id: str) -> str:
    return f"results/{event_id}/exif_index.json"


def load_exif_index(event_id: str) -> dict[str, ExifInfo] | None:
    """Parsed EXIF of a previous ingest ({photo_id: ExifInfo}); None if absent or unreadable."""
    try:
        doc = json.loads(proxies.get_store().get_bytes(exif_index_key(event_id)))
        return {pid: ExifInfo.model_validate(v) for pid, v in doc.get("photos", {}).items()}
    except Exception:  # noqa: BLE001 - absent (NoSuchKey / FileNotFoundError) or stale schema: rebuild
        return None


def save_exif_index(event_id: str, exifs: dict[str, ExifInfo]) -> None:
    """Persist parsed EXIF (only the ExifInfo fields; no serial numbers) so later ingests do one GET."""
    doc = {"event_id": event_id, "photos": {pid: e.model_dump(exclude_none=True) for pid, e in sorted(exifs.items())}}
    try:
        proxies.get_store().put_bytes(exif_index_key(event_id), json.dumps(doc, separators=(",", ":")).encode(),
                                      "application/json")
    except Exception:  # noqa: BLE001 - an optimisation only
        pass


def ingest_event(event_id: str, max_photos: int | None = None, load_exif: bool = True) -> dict[str, Any]:
    """Read manifests/<event_id>.jsonl, verify raw/<event_id>/ objects exist, attach EXIF sidecars.

    All S3 calls overlap on one thread pool (manifest, LISTs, sidecar GETs). The parsed EXIF is saved as
    results/<event_id>/exif_index.json, so every later ingest (agent, re-runs) reads one object instead of one
    sidecar per photo: ~2 s on 661 photos from a remote dev box (first-ever ingest ~6-10 s here, GET-bound).
    load_exif=False skips sidecars (capture order then has no sub-second part).

    Returns {n_photos, n_with_original, n_with_frs, cameras[], time_span, n_missing_raw, missing_raw}.
    Photos without a processed image are excluded from the context and listed in missing_raw.
    """
    store = proxies.get_store()
    remote = not proxies.is_local_store()
    # Cold ingest is S3 round-trip bound: run the manifest GET, both LISTs and (once the exif/ LIST is back)
    # every sidecar GET on the shared I/O pool at the same time. Sidecars go through the disk cache, so a
    # repeat ingest or a later io.exif.load_exif() is a local read.
    pool = proxies.io_pool() if remote else None
    index = load_exif_index(event_id) if load_exif else None  # one GET instead of one per photo
    if pool is not None:
        f_manifest = pool.submit(read_manifest_records, event_id)
        f_raw = pool.submit(store.list, f"raw/{event_id}/")
        exif_listing = store.list(f"exif/{event_id}/") if load_exif else []
        todo = [k for k in exif_listing if index is None or k.rsplit("/", 1)[-1].removesuffix(".exif.json")
                not in index]
        exif_prefetch = proxies.prefetch_async(todo)
        all_records, raw_listing = f_manifest.result(), f_raw.result()
    else:
        all_records = read_manifest_records(event_id)
        raw_listing = store.list(f"raw/{event_id}/")
        exif_listing = store.list(f"exif/{event_id}/") if load_exif else []
        exif_prefetch = None
    records = [r for r in all_records if r.event_id == event_id or not r.event_id]
    records.sort(key=lambda r: (r.clicked_ts() or r.created_ts() or 0.0, natural_key(r.name or ""), r.photo_id))
    if max_photos:
        records = records[: int(max_photos)]
    exif_keys = set(exif_listing)
    raw_by_id = {k.rsplit("/", 1)[-1].rsplit(".", 1)[0]: k for k in raw_listing}

    ctx = EventContext(event_id=event_id)
    missing: list[str] = []
    present = []
    for r in records:
        rk = raw_by_id.get(r.photo_id)
        if rk is None:
            missing.append(r.photo_id)
            continue
        proxies.register_keys(event_id, r.photo_id, rk, r.original_key)
        present.append(r)
    want = [exif_key(event_id, r.photo_id) for r in present if exif_key(event_id, r.photo_id) in exif_keys]
    if exif_prefetch is not None:
        exif_prefetch.wait()
    exifs: dict[str, ExifInfo] = {}
    n_from_index = 0
    for k in want:
        pid = k.rsplit("/", 1)[-1].removesuffix(".exif.json")
        if index is not None and pid in index:
            exifs[k] = index[pid]
            n_from_index += 1
            continue
        try:
            exifs[k] = parse_exif_bytes(proxies.cached_bytes(k))
        except (FileNotFoundError, KeyError):
            pass
    if load_exif and exifs and n_from_index < len(exifs):
        save_exif_index(event_id, {k.rsplit("/", 1)[-1].removesuffix(".exif.json"): v for k, v in exifs.items()})
    for r in present:
        ctx.photos[r.photo_id] = PhotoInfo(record=r, exif=exifs.get(exif_key(event_id, r.photo_id)))
        ctx.order.append(r.photo_id)
    ctx.order.sort(key=lambda pid: ctx.photos[pid].order_key)  # sub-second EXIF can refine the order
    with _lock:
        _contexts[event_id] = ctx
        for pid in ctx.order:
            _photo_event[pid] = event_id

    infos = list(ctx.photos.values())
    tss = [t for p in infos if (t := p.ts) is not None]
    t0: float | None = min(tss) if tss else None
    t1: float | None = max(tss) if tss else None
    res = IngestResult(
        n_photos=len(infos),
        n_with_original=sum(1 for p in infos if p.has_original),
        n_with_frs=sum(1 for p in infos if (p.record.frs_status or "") == "indexed"),
        cameras=sorted({p.camera for p in infos}),
        time_span=TimeSpan(start=_iso(t0), end=_iso(t1), seconds=None if t0 is None or t1 is None else round(t1 - t0, 3)),
        n_missing_raw=len(missing),
        missing_raw=missing[:50],
    )
    return res.model_dump()


def install_context(event_id: str, photos: list[tuple[dict[str, Any], dict[str, Any] | None, str | None]]) -> EventContext:
    """Rebuild an EventContext without I/O (pool-worker initializer).

    photos = [(PhotoRecord dict, ExifInfo dict | None, raw_key | None)] in capture order.
    """
    ctx = EventContext(event_id=event_id)
    for rec_d, ex_d, rk in photos:
        rec = PhotoRecord.model_validate(rec_d)
        ctx.photos[rec.photo_id] = PhotoInfo(record=rec, exif=ExifInfo.model_validate(ex_d) if ex_d else None)
        ctx.order.append(rec.photo_id)
        proxies.register_keys(event_id, rec.photo_id, rk, rec.original_key)
    with _lock:
        _contexts[event_id] = ctx
        for pid in ctx.order:
            _photo_event[pid] = event_id
    return ctx


def export_context(event_id: str) -> list[tuple[dict[str, Any], dict[str, Any] | None, str | None]]:
    """Inverse of install_context, for shipping the ingested event to pool workers."""
    ctx = get_context(event_id, create=False)
    out = []
    for pid in ctx.order:
        info = ctx.photos[pid]
        rk = proxies.raw_key(event_id, pid)
        out.append((info.record.model_dump(mode="json"), info.exif.model_dump() if info.exif else None, rk))
    return out

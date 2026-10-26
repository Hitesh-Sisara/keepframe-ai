"""Image variants with a local disk cache and an S3 or LOCAL_MODE backing store.

Backing store
  * WORK_BUCKET set   -> S3 via keepframe.io.s3 (data agent); boto3 fallback if that module is absent.
  * WORK_BUCKET unset -> LOCAL_MODE: keys resolve under $CULL_LOCAL_ROOT (default ./data/local),
    using the same layout as the bucket (manifests/, raw/, originals/, exif/, proxy/, results/ ...).

Variants
  load_full        processed image (raw/<eventId>/<photoId>.<ext>, <=2560 long edge), BGR uint8
  load_p1024       1024-px proxy. Stored/cached as JPEG q90 at proxy/<eventId>/<photoId>.jpg and ALWAYS
                   returned as the decoded JPEG, so a cold run and a warm run see identical pixels
                   (determinism, docs/EVALUATION.md §2 robustness).
  load_p1024_gray  cv2.cvtColor(p1024, COLOR_BGR2GRAY)
  load_original    original (originals/<eventId>/<photoId>.<ext>); None if absent or HEIC.

Disk cache: $CULL_CACHE_DIR (default /tmp/cull)/{raw,originals,proxy}/<eventId>/<photoId>.<ext>.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from typing import Any

import cv2
import numpy as np

from keepframe.config import section
from keepframe.io import images
from keepframe.io._s3_stub import BotoStore, CountingStore, LocalStore, ModuleStore, Store

RAW_EXTS = (".webp", ".jpg", ".jpeg", ".png")
ORIG_EXTS = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".JPG", ".JPEG", ".PNG", ".HEIC")

_store: Store | None = None
_present_originals: set[str] | None = None  # originals/<ev>/ LIST (None = unknown: trust manifest hints)
_store_lock = threading.Lock()
_key_hints: dict[tuple[str, str], dict[str, str]] = {}
_mem: OrderedDict[tuple[str, str], np.ndarray] = OrderedDict()
_mem_lock = threading.Lock()


# --------------------------------------------------------------------------- store selection


def local_mode() -> bool:
    return not os.environ.get("WORK_BUCKET")


def local_root() -> Path:
    return Path(os.environ.get("CULL_LOCAL_ROOT", "data/local")).resolve()


def cache_dir() -> Path:
    return Path(os.environ.get("CULL_CACHE_DIR", "/tmp/cull"))


def get_store() -> Store:
    """Process-wide store; re-created if reset_store() was called (tests, CLI --local-root)."""
    global _store
    with _store_lock:
        if _store is None:
            if local_mode():
                _store = LocalStore(local_root())
            else:
                try:
                    import importlib

                    # import_module honours sys.modules (test doubles), unlike `from keepframe.io import s3`,
                    # which returns the package attribute once the real submodule has been imported
                    inner: Store = ModuleStore(importlib.import_module("keepframe.io.s3"))  # data agent
                except ImportError:
                    inner = BotoStore(os.environ["WORK_BUCKET"])
                _store = CountingStore(inner)
        return _store


def is_local_store() -> bool:
    return isinstance(get_store(), LocalStore)


def io_counts() -> dict[str, int]:
    """S3 call counts of this process ({} in LOCAL_MODE)."""
    st = get_store()
    return st.snapshot() if isinstance(st, CountingStore) else {}


# --------------------------------------------------------------------------- background writes / known proxies

_known_proxies: set[str] = set()
_known_proxies_set = False  # True once a LIST of proxy/<eventId>/ was installed (even if empty)
_writer: Any = None
_writer_lock = threading.Lock()
_pending: list[Any] = []


def set_known_proxies(keys: set[str]) -> None:
    """Proxy keys already in the store (from one LIST), so load_p1024 skips its per-photo HEAD."""
    global _known_proxies_set
    _known_proxies.clear()
    _known_proxies.update(keys)
    _known_proxies_set = True


def put_background(key: str, data: bytes, content_type: str | None = None) -> None:
    """Queue an upload on a small thread pool (inline for the local store). flush_writes() waits."""
    global _writer
    store = get_store()
    if isinstance(store, LocalStore):
        store.put_bytes(key, data, content_type=content_type)
        return
    from concurrent.futures import ThreadPoolExecutor

    with _writer_lock:
        if _writer is None:
            _writer = ThreadPoolExecutor(max_workers=int(os.environ.get("CULL_IO_THREADS_PUT", "32")),
                                         thread_name_prefix="cull-put")
        _pending.append(_writer.submit(store.put_bytes, key, data, content_type))


def flush_writes() -> int:
    """Wait for queued uploads; re-raises the first failure. Returns the number flushed."""
    with _writer_lock:
        futs = list(_pending)
        _pending.clear()
    for f in futs:
        f.result()
    return len(futs)


_io_pool: Any = None


def io_pool() -> Any:
    """Process-wide thread pool for S3 GETs (CULL_IO_THREADS, default 64 = the data agent's boto3
    max_pool_connections). boto3 clients are thread-safe, so one client serves every thread."""
    global _io_pool
    with _writer_lock:
        if _io_pool is None:
            from concurrent.futures import ThreadPoolExecutor

            _io_pool = ThreadPoolExecutor(max_workers=int(os.environ.get("CULL_IO_THREADS", "64")),
                                          thread_name_prefix="cull-get")
        return _io_pool


class Prefetch:
    """Handle for an asynchronous prefetch started by prefetch_async(); wait() -> {fetched, cached, failed}."""

    def __init__(self, futures: list[Any], cached: int):
        self.futures, self.cached = futures, cached

    def wait(self) -> dict[str, int]:
        ok = sum(1 for f in self.futures if f.result())
        return {"fetched": ok, "cached": self.cached, "failed": len(self.futures) - ok}


def _fetch_one(k: str) -> bool:
    try:
        _cached_bytes(k)
        return True
    except Exception:  # noqa: BLE001 - the stage that needs the key reports the error
        return False


def prefetch_async(keys: list[str]) -> Prefetch:
    """Start downloading keys into the disk cache on the shared I/O pool (no-op for the local store)."""
    store = get_store()
    if isinstance(store, LocalStore) or not keys:
        return Prefetch([], 0)
    todo = [k for k in dict.fromkeys(keys) if not (cache_dir() / k).is_file()]
    pool = io_pool()
    return Prefetch([pool.submit(_fetch_one, k) for k in todo], len(keys) - len(todo))


def prefetch(keys: list[str], threads: int | None = None) -> dict[str, int]:
    """Download keys into the disk cache concurrently and wait. Returns {fetched, cached, failed}."""
    return prefetch_async(keys).wait()


def reset_store(store: Store | None = None) -> None:
    """Drop the cached store/hints/memory (or install an explicit one)."""
    global _store
    with _store_lock:
        _store = store
    global _known_proxies_set, _present_originals
    _present_originals = None
    _key_hints.clear()
    _known_proxies.clear()
    _known_proxies_set = False
    with _mem_lock:
        _mem.clear()


# --------------------------------------------------------------------------- key resolution


def register_keys(event_id: str, photo_id: str, processed_key: str | None, original_key: str | None) -> None:
    """Record the source-key extensions from the manifest so loads avoid LIST/HEAD round trips."""
    hint: dict[str, str] = {}
    if processed_key:
        hint["raw"] = f"raw/{event_id}/{photo_id}{PurePosixPath(processed_key).suffix or '.webp'}"
    if original_key:
        hint["original"] = f"originals/{event_id}/{photo_id}{PurePosixPath(original_key).suffix or '.jpg'}"
    _key_hints[(event_id, photo_id)] = hint


def _resolve(kind: str, event_id: str, photo_id: str) -> str | None:
    hint = _key_hints.get((event_id, photo_id), {}).get(kind)
    if hint:
        return hint
    prefix = "raw" if kind == "raw" else "originals"
    exts = RAW_EXTS if kind == "raw" else ORIG_EXTS
    store = get_store()
    for ext in exts:
        key = f"{prefix}/{event_id}/{photo_id}{ext}"
        if store.exists(key):
            _key_hints.setdefault((event_id, photo_id), {})[kind] = key
            return key
    return None


def raw_key(event_id: str, photo_id: str) -> str | None:
    return _resolve("raw", event_id, photo_id)


def set_present_originals(keys: set[str] | None) -> None:
    """Install a LIST of originals/<eventId>/: manifest original_key hints for absent objects are ignored
    (the sync copies originals only for some photos)."""
    global _present_originals
    _present_originals = None if keys is None else set(keys)


def original_key(event_id: str, photo_id: str) -> str | None:
    if _present_originals is not None:  # answer from the LIST, never HEAD
        hint = _key_hints.get((event_id, photo_id), {}).get("original")
        if hint in _present_originals:
            return hint
        for ext in ORIG_EXTS:
            k = f"originals/{event_id}/{photo_id}{ext}"
            if k in _present_originals:
                return k
        return None
    return _resolve("original", event_id, photo_id)


def proxy_key(event_id: str, photo_id: str) -> str:
    return f"proxy/{event_id}/{photo_id}.jpg"


# --------------------------------------------------------------------------- byte access with disk cache


def cached_bytes(key: str) -> bytes:
    """Bytes of `key` through the disk cache (direct read for the local store)."""
    return _cached_bytes(key)


def _cached_bytes(key: str) -> bytes:
    """Read `key`, via the disk cache when the store is remote."""
    store = get_store()
    if isinstance(store, LocalStore):
        return store.get_bytes(key)
    p = cache_dir() / key
    if p.is_file():
        return p.read_bytes()
    data = store.get_bytes(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(p)
    return data


def _mem_get(k: tuple[str, str]) -> np.ndarray | None:
    with _mem_lock:
        v = _mem.get(k)
        if v is not None:
            _mem.move_to_end(k)
        return v


def _mem_put(k: tuple[str, str], v: np.ndarray) -> None:
    cap = int(section("proxy").get("memory_lru", 64))
    v.setflags(write=False)
    with _mem_lock:
        _mem[k] = v
        _mem.move_to_end(k)
        while len(_mem) > cap:
            _mem.popitem(last=False)


# --------------------------------------------------------------------------- public loaders


def load_full(event_id: str, photo_id: str) -> np.ndarray:
    """Decoded processed image (BGR uint8). Raises FileNotFoundError if absent or undecodable."""
    key = raw_key(event_id, photo_id)
    if key is None:
        raise FileNotFoundError(f"raw/{event_id}/{photo_id}.*")
    img = images.decode(_cached_bytes(key), key=key)
    if img is None:
        raise FileNotFoundError(f"undecodable processed image {key}")
    return img


_defer_uploads = False
_deferred: dict[str, bytes] = {}


def defer_uploads(on: bool) -> None:
    """Pool workers: keep new proxy JPEGs in memory (take_deferred()) instead of uploading them, so the parent
    uploads on its background pool and no worker blocks on S3 PUTs."""
    global _defer_uploads
    _defer_uploads = on


def take_deferred(key: str) -> bytes | None:
    return _deferred.pop(key, None)


def load_p1024(event_id: str, photo_id: str, timings: dict[str, float] | None = None) -> np.ndarray:
    """1024-px proxy (read-only BGR array; copy before mutating).

    `timings`, if given, receives decode/resize/encode/proxy-read milliseconds (worker stats).
    """
    import time

    t = time.perf_counter
    mk = (event_id, photo_id)
    hit = _mem_get(mk)
    if hit is not None:
        return hit
    cfg = section("proxy")
    key = proxy_key(event_id, photo_id)
    store = get_store()
    disk = cache_dir() / key
    data: bytes | None = None
    if disk.is_file():
        data = disk.read_bytes()
    elif (key in _known_proxies) if _known_proxies_set else store.exists(key):
        data = store.get_bytes(key)
    if data is None:
        t0 = t()
        full = load_full(event_id, photo_id)
        t1 = t()
        p = images.resize_long_edge(full, int(cfg["long_edge"]))
        t2 = t()
        data = images.encode(p, ".jpg", int(cfg["jpeg_quality"]))
        if cfg.get("write_to_store", True):
            if _defer_uploads and not isinstance(store, LocalStore):
                _deferred[key] = data
            else:
                put_background(key, data, content_type="image/jpeg")
        t3 = t()
        if timings is not None:
            timings.update(decode_full=(t1 - t0) * 1e3, resize=(t2 - t1) * 1e3, proxy_encode=(t3 - t2) * 1e3)
    if not disk.is_file():
        disk.parent.mkdir(parents=True, exist_ok=True)
        tmp = disk.with_name(f".{disk.name}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(disk)
    t4 = t()
    img = images.decode(data)
    if img is None:
        raise FileNotFoundError(f"corrupt proxy {key}")
    if timings is not None:
        timings["proxy_decode"] = (t() - t4) * 1e3
    _mem_put(mk, img)
    return img


def load_p1024_gray(event_id: str, photo_id: str) -> np.ndarray:
    return cv2.cvtColor(load_p1024(event_id, photo_id), cv2.COLOR_BGR2GRAY)


def load_original(event_id: str, photo_id: str) -> np.ndarray | None:
    """Original upload, or None when there is none or it is HEIC (caller sets variant_fallback)."""
    key = original_key(event_id, photo_id)
    if key is None or images.is_heic(key=key):
        return None
    try:
        return images.decode(_cached_bytes(key), key=key)
    except (FileNotFoundError, KeyError):
        return None


def original_is_heic(event_id: str, photo_id: str) -> bool:
    key = original_key(event_id, photo_id)
    return bool(key) and images.is_heic(key=key)

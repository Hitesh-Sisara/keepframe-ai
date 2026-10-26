"""Process-pool fan-out across photos.

Each worker process sets cv2.setNumThreads(1) so per-image latency is stable and the pool scales across
vCPUs. Workers get the ingested event via an initializer (no manifest re-read) and the parent's env
(WORK_BUCKET / CULL_LOCAL_ROOT / CULL_CACHE_DIR). workers=1 runs inline (debugging, tests, tiny events).
Results come back in submission order, so output is independent of scheduling.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import cv2


def default_workers() -> int:
    env = os.environ.get("CULL_WORKERS")
    if env:
        return max(1, int(env))
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:
        return max(1, os.cpu_count() or 1)


def _init_worker(env: dict[str, str], event_id: str, photos: list[Any], known_proxies: list[str],
                 originals: list[str] | None) -> None:
    os.environ.update(env)
    os.environ["KEEPFRAME_IMPORT_LOG"] = os.environ.get("KEEPFRAME_WORKER_IMPORT_LOG", "0")
    cv2.setNumThreads(1)
    from keepframe.io import proxies
    from keepframe.tools.ingest import install_context

    proxies.reset_store()
    install_context(event_id, photos)
    proxies.set_known_proxies(set(known_proxies))
    proxies.set_present_originals(None if originals is None else set(originals))
    proxies.defer_uploads(True)


def _env_snapshot() -> dict[str, str]:
    keys = ("WORK_BUCKET", "CULL_LOCAL_ROOT", "CULL_CACHE_DIR", "CULL_THRESHOLDS", "CULL_CONFIG_DIR",
            "CULL_MODELS_DIR", "OPENCV_FORCE_DNN_ENGINE", "AWS_REGION", "AWS_DEFAULT_REGION", "CULL_IO_THREADS",
            "CULL_RECOMPUTE_PROXIES")
    return {k: os.environ[k] for k in keys if k in os.environ}


class Pool:
    """Context manager wrapping a ProcessPoolExecutor bound to one ingested event."""

    def __init__(self, event_id: str, workers: int | None = None, known_proxies: set[str] | None = None,
                 originals: set[str] | None = None):
        from keepframe.tools.ingest import export_context

        self.event_id = event_id
        self.workers = workers or default_workers()
        self._ex: ProcessPoolExecutor | None = None
        if self.workers > 1:
            ctx = mp.get_context("forkserver" if "forkserver" in mp.get_all_start_methods() else "spawn")
            self._ex = ProcessPoolExecutor(
                max_workers=self.workers, mp_context=ctx, initializer=_init_worker,
                initargs=(_env_snapshot(), event_id, export_context(event_id), sorted(known_proxies or ()),
                          None if originals is None else sorted(originals)),
            )

    def __enter__(self) -> Pool:
        return self

    def __exit__(self, *exc: object) -> None:
        if self._ex is not None:
            self._ex.shutdown(wait=True, cancel_futures=True)

    def map(self, fn: Callable[..., dict[str, Any]], arg_tuples: Sequence[tuple[Any, ...]] | Iterable[tuple[Any, ...]],
            chunksize: int = 4) -> list[dict[str, Any]]:
        args = list(arg_tuples)
        if self._ex is None:
            prev = cv2.getNumThreads()
            cv2.setNumThreads(1)
            try:
                return [fn(*a) for a in args]
            finally:
                cv2.setNumThreads(prev)
        cols = list(zip(*args, strict=True)) if args else []
        return list(self._ex.map(fn, *cols, chunksize=max(1, chunksize)))


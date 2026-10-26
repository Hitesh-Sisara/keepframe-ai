"""compute_hashes tool: perceptual hashes on the 1024-px proxy (docs/REPORT.md §3).

pHash (8 bytes) and blockMeanHash mode 0 (32 bytes) on p1024_gray, colorMomentHash (42 float64)
on p1024. Hamming distances are computed with a vectorised XOR + np.unpackbits.
"""

from __future__ import annotations

import json
import time
from typing import Any

import cv2
import numpy as np

from keepframe.io import proxies
from keepframe.schemas import Failure, HashesResult

_PHASH = cv2.img_hash.PHash.create()


def hash_image(p1024: np.ndarray, gray: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """{'phash': uint8[8], 'block_mean': uint8[32], 'color_moment': float64[42]}."""
    if gray is None:
        gray = cv2.cvtColor(p1024, cv2.COLOR_BGR2GRAY)
    return {
        "phash": cv2.img_hash.pHash(gray).reshape(-1).astype(np.uint8),
        "block_mean": cv2.img_hash.blockMeanHash(gray, mode=cv2.img_hash.BLOCK_MEAN_HASH_MODE_0)
        .reshape(-1)
        .astype(np.uint8),
        "color_moment": cv2.img_hash.colorMomentHash(p1024).reshape(-1).astype(np.float64),
    }


def to_jsonable(h: dict[str, np.ndarray]) -> dict[str, Any]:
    return {
        "phash": h["phash"].tobytes().hex(),
        "block_mean": h["block_mean"].tobytes().hex(),
        "color_moment": [round(float(x), 6) for x in h["color_moment"]],
    }


def from_jsonable(d: dict[str, Any]) -> dict[str, np.ndarray]:
    return {
        "phash": np.frombuffer(bytes.fromhex(d["phash"]), dtype=np.uint8).copy(),
        "block_mean": np.frombuffer(bytes.fromhex(d["block_mean"]), dtype=np.uint8).copy(),
        "color_moment": np.asarray(d["color_moment"], dtype=np.float64),
    }


def hamming(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamming distance between packed uint8 hashes. a: (..., B), b: (..., B) broadcastable -> int array."""
    x = np.bitwise_xor(np.asarray(a, dtype=np.uint8), np.asarray(b, dtype=np.uint8))
    out: np.ndarray = np.unpackbits(x, axis=-1).sum(axis=-1, dtype=np.int32)
    return out


def hamming_matrix(a: np.ndarray, b: np.ndarray | None = None) -> np.ndarray:
    """Pairwise Hamming matrix for stacked hashes a: (N, B), b: (M, B) -> (N, M) int32."""
    b = a if b is None else b
    return hamming(a[:, None, :], b[None, :, :])


def hamming_one_to_many(h: np.ndarray, many: np.ndarray) -> np.ndarray:
    return hamming(h[None, :], many)


def color_moment_l2(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    out: np.ndarray = np.linalg.norm(np.asarray(a, np.float64) - np.asarray(b, np.float64), axis=-1)
    return out


def phash_distance_cv(h1: np.ndarray, h2: np.ndarray) -> float:
    """Reference distance through OpenCV's own comparator (used by tests to cross-check)."""
    return float(_PHASH.compare(h1.reshape(1, -1), h2.reshape(1, -1)))


def compute_hashes(event_id: str, photo_ids: list[str] | None = None) -> dict[str, Any]:
    """Compute pHash / blockMeanHash / colorMomentHash for the event's photos (or the given subset).

    Hashes are kept in the event context and persisted to results/<event_id>/hashes.json so a later
    process (or the agent after a resume) reuses them. Returns {computed, cached, failures[]}.
    """
    from keepframe.tools.ingest import get_context

    ctx = get_context(event_id)
    store = proxies.get_store()
    key = f"results/{event_id}/hashes.json"
    if not ctx.hashes and store.exists(key):
        try:
            ctx.hashes.update(json.loads(store.get_bytes(key)))
        except ValueError:
            pass
    ids = photo_ids if photo_ids is not None else list(ctx.order)
    computed = cached = 0
    failures: list[Failure] = []
    t0 = time.perf_counter()
    for pid in ids:
        if pid in ctx.hashes:
            cached += 1
            continue
        try:
            p = proxies.load_p1024(event_id, pid)
            ctx.hashes[pid] = to_jsonable(hash_image(p))
            computed += 1
        except Exception as e:  # noqa: BLE001 - one bad image must not stop the event
            failures.append(Failure(photo_id=pid, error=f"{type(e).__name__}: {e}"[:200]))
    if computed:
        store.put_bytes(key, json.dumps(ctx.hashes, sort_keys=True).encode(), content_type="application/json")
    res = HashesResult(computed=computed, cached=cached, failures=failures).model_dump()
    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1e3, 1)
    return res

"""cluster_near_duplicates tool (docs/REPORT.md §3).

1. Pre-bucket by camera; inside a camera, only pairs with |dt| <= time_window_s are compared
   (sorted sliding window, so cost is O(N * k), never O(N^2) across the event).
2. Edge if pHash <= phash_max OR (blockMean <= block_mean_max AND |dt| <= block_mean_dt_s).
   Optional colorMoment L2 <= color_moment_max as an extra required vote (off by default).
3. md5 exact duplicates are always joined (any camera / time) and reported in exact_duplicates.
4. Union-find -> groups; ids g_<event_id>_<n>, numbered by earliest member in capture order.
5. Optional refinement (cluster.refine_enabled) for groups > 8: ORB + BFMatcher + estimateAffine2D
   (USAC_MAGSAC; estimateAffinePartial2D rejects USAC in 5.0.0) alignment of consecutive frames, cv2.quality SSIM; cut where SSIM < refine_ssim_min.

Photos with no capture time are compared only with other untimed photos of the same camera, by pHash.
Same-second frames (clickedAt is second-granular; sub-second EXIF exists for few events) all fall inside
the window, so they are always compared; within-second order (span, refinement) uses the filename.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from keepframe.config import section
from keepframe.schemas import ClusterResult, Group
from keepframe.tools.hashes import color_moment_l2, from_jsonable, hamming


@dataclass(frozen=True)
class ClusterItem:
    photo_id: str
    camera: str
    ts: float | None
    phash: np.ndarray
    block_mean: np.ndarray
    color_moment: np.ndarray | None = None
    md5: str | None = None
    name: str | None = None  # filename: within-second tiebreak when clickedAt has no sub-second part


class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def _cm(it: ClusterItem) -> np.ndarray:
    if it.color_moment is None:
        raise ValueError(f"{it.photo_id}: color_moment vote enabled but hash missing")
    return it.color_moment


def _edges_for_bucket(idx: list[int], items: Sequence[ClusterItem], cfg: dict[str, Any], uf: UnionFind) -> int:
    """Union qualifying pairs within one camera bucket; returns number of edges added."""
    win = float(cfg["time_window_s"])
    p_max, b_max, b_dt = int(cfg["phash_max"]), int(cfg["block_mean_max"]), float(cfg["block_mean_dt_s"])
    use_cm, cm_max = bool(cfg.get("use_color_moment_vote")), float(cfg["color_moment_max"])
    from keepframe.tools.ingest import natural_key

    def k(i: int) -> tuple[Any, ...]:
        return (natural_key(items[i].name or ""), items[i].photo_id)

    timed = sorted((i for i in idx if items[i].ts is not None), key=lambda i: (items[i].ts, *k(i)))
    untimed = sorted((i for i in idx if items[i].ts is None), key=k)
    edges = 0
    if timed:
        ts = np.array([items[i].ts for i in timed], dtype=np.float64)
        ph = np.stack([items[i].phash for i in timed])
        bm = np.stack([items[i].block_mean for i in timed])
        cm = np.stack([_cm(items[i]) for i in timed]) if use_cm else None
        ends = np.searchsorted(ts, ts + win, side="right")
        for a in range(len(timed)):
            e = int(ends[a])
            if e <= a + 1:
                continue
            sl = slice(a + 1, e)
            dt = ts[sl] - ts[a]
            dp = hamming(ph[a][None, :], ph[sl])
            db = hamming(bm[a][None, :], bm[sl])
            ok = (dp <= p_max) | ((db <= b_max) & (dt <= b_dt))
            if cm is not None:
                ok &= color_moment_l2(cm[a][None, :], cm[sl]) <= cm_max
            for off in np.flatnonzero(ok):
                uf.union(timed[a], timed[a + 1 + int(off)])
                edges += 1
    if len(untimed) > 1:
        ph = np.stack([items[i].phash for i in untimed])
        for a in range(len(untimed) - 1):
            dp = hamming(ph[a][None, :], ph[a + 1 :])
            for off in np.flatnonzero(dp <= p_max):
                uf.union(untimed[a], untimed[a + 1 + int(off)])
                edges += 1
    return edges


def ssim_aligned(gray_a: np.ndarray, gray_b: np.ndarray, n_features: int = 500) -> float:
    """Align b onto a (ORB + BFMatcher + estimateAffinePartial2D/USAC_MAGSAC) and return SSIM."""
    orb = cv2.ORB.create(nfeatures=n_features)
    ka, da = orb.detectAndCompute(gray_a, None)
    kb, db = orb.detectAndCompute(gray_b, None)
    warped = gray_b
    if da is not None and db is not None and len(ka) >= 8 and len(kb) >= 8:
        matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(db, da)
        if len(matches) >= 8:
            src = np.array([kb[m.queryIdx].pt for m in matches], dtype=np.float32).reshape(-1, 1, 2)
            dst = np.array([ka[m.trainIdx].pt for m in matches], dtype=np.float32).reshape(-1, 1, 2)
            # OpenCV 5.0.0: estimateAffinePartial2D rejects every USAC_* method ("Unknown or unsupported
            # robust estimation method"); estimateAffine2D accepts USAC_MAGSAC. Fall back to RANSAC partial.
            try:
                m, _ = cv2.estimateAffine2D(src, dst, method=cv2.USAC_MAGSAC, ransacReprojThreshold=3.0)
            except cv2.error:
                m, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
            if m is not None:
                warped = cv2.warpAffine(gray_b, m, (gray_a.shape[1], gray_a.shape[0]), borderMode=cv2.BORDER_REFLECT)
    if warped.shape != gray_a.shape:
        warped = cv2.resize(warped, (gray_a.shape[1], gray_a.shape[0]), interpolation=cv2.INTER_AREA)
    return float(cv2.quality.QualitySSIM.create(gray_a).compute(warped)[0])


def refine_group(
    members: list[str], ts_of: dict[str, float | None], load_gray: Callable[[str], np.ndarray], cfg: dict[str, Any]
) -> list[list[str]]:
    """Split a large group at consecutive pairs whose aligned SSIM < refine_ssim_min."""
    order = members  # callers pass members in capture order (ts, filename, photo_id)
    le = int(cfg["refine_long_edge"])

    def small(pid: str) -> np.ndarray:
        g = load_gray(pid)
        s = le / max(g.shape[:2])
        return cv2.resize(g, (max(1, round(g.shape[1] * s)), max(1, round(g.shape[0] * s))), interpolation=cv2.INTER_AREA)

    parts: list[list[str]] = [[order[0]]]
    prev = small(order[0])
    for pid in order[1:]:
        cur = small(pid)
        if ssim_aligned(prev, cur, int(cfg["refine_orb_features"])) < float(cfg["refine_ssim_min"]):
            parts.append([pid])
        else:
            parts[-1].append(pid)
        prev = cur
    return parts


def cluster_items(
    items: Sequence[ClusterItem],
    event_id: str,
    cfg: dict[str, Any] | None = None,
    load_gray: Callable[[str], np.ndarray] | None = None,
) -> dict[str, Any]:
    """Pure clustering over precomputed hashes. `items` order defines group numbering (capture order)."""
    from keepframe.tools.ingest import natural_key

    if cfg is None:
        cfg = {**section("hashes"), **section("cluster")}
    n = len(items)
    uf = UnionFind(n)
    by_cam: dict[str, list[int]] = {}
    for i, it in enumerate(items):
        by_cam.setdefault(it.camera or "unknown", []).append(i)
    n_edges = sum(_edges_for_bucket(idx, items, cfg, uf) for idx in by_cam.values())

    first_md5: dict[str, int] = {}
    exact: dict[str, str] = {}
    for i, it in enumerate(items):
        if not it.md5:
            continue
        if it.md5 in first_md5:
            j = first_md5[it.md5]
            uf.union(i, j)
            exact[it.photo_id] = items[j].photo_id
        else:
            first_md5[it.md5] = i

    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(uf.find(i), []).append(i)
    member_lists = [sorted(c) for c in comps.values()]

    ts_of = {it.photo_id: it.ts for it in items}
    if cfg.get("refine_enabled") and load_gray is not None:
        refined: list[list[int]] = []
        pos = {it.photo_id: i for i, it in enumerate(items)}
        for c in member_lists:
            if len(c) >= int(cfg["refine_min_size"]):
                c = sorted(c, key=lambda i: (items[i].ts is None, items[i].ts or 0.0,
                                             natural_key(items[i].name or ""), items[i].photo_id))
                ids = [items[i].photo_id for i in c]
                for part in refine_group(ids, ts_of, load_gray, cfg):
                    refined.append(sorted(pos[p] for p in part))
            else:
                refined.append(c)
        # exact md5 twins must never be split apart
        member_lists = refined
        idx_group = {i: g for g, c in enumerate(member_lists) for i in c}
        for dup, orig in exact.items():
            gd, go = idx_group[pos[dup]], idx_group[pos[orig]]
            if gd != go:
                member_lists[go] = sorted(member_lists[go] + member_lists[gd])
                member_lists[gd] = []
                for i in member_lists[go]:
                    idx_group[i] = go
        member_lists = [c for c in member_lists if c]

    member_lists.sort(key=lambda c: c[0])
    groups: list[Group] = []
    singletons: list[str] = []
    for c in member_lists:
        if len(c) == 1:
            singletons.append(items[c[0]].photo_id)
            continue
        tss = [t for i in c if (t := items[i].ts) is not None]
        span = (max(tss) - min(tss)) if tss else 0.0
        groups.append(
            Group(
                group_id=f"g_{event_id}_{len(groups) + 1}",
                photo_ids=[items[i].photo_id for i in c],
                size=len(c),
                span_s=round(float(span), 3),
            )
        )
    res = ClusterResult(n_groups=len(groups), groups=groups, singletons=singletons, exact_duplicates=exact).model_dump()
    res["n_edges"] = n_edges
    return res


def cluster_near_duplicates(
    event_id: str, phash_max_distance: int | None = None, time_window_s: float | None = None
) -> dict[str, Any]:
    """Group bursts / near-duplicates for an ingested event whose hashes are computed.

    Returns {n_groups, groups:[{group_id, photo_ids[], size, span_s}], singletons, exact_duplicates}.
    """
    from keepframe.io import proxies
    from keepframe.tools.hashes import compute_hashes
    from keepframe.tools.ingest import get_context

    ctx = get_context(event_id)
    missing = [p for p in ctx.order if p not in ctx.hashes]
    if missing:
        compute_hashes(event_id, missing)
    cfg = {**section("hashes"), **section("cluster")}
    if phash_max_distance is not None:
        cfg["phash_max"] = int(phash_max_distance)
    if time_window_s is not None:
        cfg["time_window_s"] = float(time_window_s)
    items = []
    for pid in ctx.order:
        if pid not in ctx.hashes:
            continue
        h = from_jsonable(ctx.hashes[pid])
        info = ctx.photos[pid]
        items.append(ClusterItem(pid, info.camera, info.ts, h["phash"], h["block_mean"], h["color_moment"],
                                 info.record.md5, info.record.name))
    t0 = time.perf_counter()
    res = cluster_items(items, event_id, cfg, load_gray=lambda p: proxies.load_p1024_gray(event_id, p))
    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1e3, 2)
    ctx.groups = {g["group_id"]: list(g["photo_ids"]) for g in res["groups"]}
    ctx.group_of = {p: gid for gid, ps in ctx.groups.items() for p in ps}
    ctx.exact_duplicates = dict(res["exact_duplicates"])
    return res

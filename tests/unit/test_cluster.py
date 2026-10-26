"""cluster_near_duplicates: camera/time bucketing, edges, md5 twins, union-find, refinement (docs/REPORT.md §3)."""

from __future__ import annotations

import numpy as np

from keepframe.config import section
from keepframe.io.images import resize_long_edge
from keepframe.tools.cluster import ClusterItem, UnionFind, cluster_items, ssim_aligned
from keepframe.tools.hashes import hash_image
from keepframe.worker.synth import checkerboard, jitter, scene

CFG = {**section("hashes"), **section("cluster")}
H_CACHE: dict[int, dict[str, np.ndarray]] = {}


def _hashes(seed: int, j: int | None = None) -> dict[str, np.ndarray]:
    key = seed * 100 + (j or 0)
    if key not in H_CACHE:
        img = scene(seed, 683, 1024)
        if j is not None:
            img = jitter(img, j)
        H_CACHE[key] = hash_image(resize_long_edge(img, 1024))
    return H_CACHE[key]


def _item(pid, seed, ts, cam="A", j=None, md5=None):
    h = _hashes(seed, j)
    return ClusterItem(pid, cam, ts, h["phash"], h["block_mean"], h["color_moment"], md5)


def test_union_find():
    uf = UnionFind(5)
    uf.union(0, 1)
    uf.union(3, 4)
    uf.union(1, 4)
    assert uf.find(0) == uf.find(3) and uf.find(2) != uf.find(0)


def test_synthetic_burst_groups_and_singletons():
    items = [_item("a1", 1, 0.0), _item("a2", 1, 0.4, j=1), _item("a3", 1, 0.8, j=2),
             _item("s1", 2, 30.0), _item("b1", 3, 60.0), _item("b2", 3, 60.5, j=3)]
    r = cluster_items(items, "ev", CFG)
    groups = {tuple(g["photo_ids"]) for g in r["groups"]}
    assert groups == {("a1", "a2", "a3"), ("b1", "b2")}
    assert r["singletons"] == ["s1"]
    g1 = next(g for g in r["groups"] if g["photo_ids"][0] == "a1")
    assert g1["group_id"] == "g_ev_1" and g1["size"] == 3 and abs(g1["span_s"] - 0.8) < 1e-6


def test_time_window_and_camera_separate_identical_frames():
    items = [_item("x1", 4, 0.0), _item("x2", 4, 25.0, j=1),  # outside 10 s window
             _item("y1", 5, 0.0, cam="A"), _item("y2", 5, 0.2, cam="B", j=1)]  # other camera
    r = cluster_items(items, "ev", CFG)
    assert r["n_groups"] == 0 and len(r["singletons"]) == 4
    wide = cluster_items(items, "ev", {**CFG, "time_window_s": 30.0})
    assert {tuple(g["photo_ids"]) for g in wide["groups"]} == {("x1", "x2")}


def test_md5_exact_duplicates_join_across_buckets():
    items = [_item("m1", 6, 0.0, md5="abc"), _item("m2", 7, 500.0, cam="B", md5="abc")]
    r = cluster_items(items, "ev", CFG)
    assert r["groups"][0]["photo_ids"] == ["m1", "m2"]
    assert r["exact_duplicates"] == {"m2": "m1"}


def test_block_mean_vote_needs_small_dt():
    """An item pair failing pHash but passing blockMean joins only when |dt| <= 3 s."""
    a, b = _hashes(8), _hashes(9)
    far_ph = a["phash"] ^ np.uint8(0xFF)  # force pHash distance 64
    i1 = ClusterItem("p1", "A", 0.0, a["phash"], a["block_mean"], a["color_moment"])
    i2 = ClusterItem("p2", "A", 2.0, far_ph, a["block_mean"], b["color_moment"])
    i3 = ClusterItem("p3", "A", 7.0, far_ph, a["block_mean"], b["color_moment"])
    r = cluster_items([i1, i2], "ev", CFG)
    assert r["n_groups"] == 1
    r = cluster_items([i1, i3], "ev", {**CFG, "phash_max": 10})
    # p3 has the same block hash as p2 but is 7 s from p1; p1-p3 is not an edge
    assert r["n_groups"] == 0


def test_untimed_photos_compared_by_phash_only():
    items = [_item("u1", 10, None), _item("u2", 10, None, j=1), _item("u3", 11, None)]
    r = cluster_items(items, "ev", CFG)
    assert [g["photo_ids"] for g in r["groups"]] == [["u1", "u2"]] and r["singletons"] == ["u3"]


def test_deterministic_ids_independent_of_input_hash_order():
    items = [_item("a1", 1, 0.0), _item("a2", 1, 0.4, j=1), _item("b1", 3, 60.0), _item("b2", 3, 60.5, j=3)]
    assert cluster_items(items, "ev", CFG) == cluster_items(list(items), "ev", CFG)


def test_ssim_refinement_splits_large_group():
    import cv2

    board = cv2.cvtColor(checkerboard(256, 384, 24), cv2.COLOR_BGR2GRAY)
    assert ssim_aligned(board, board) > 0.99
    imgs = {f"r{i}": cv2.cvtColor(scene(40 if i < 5 else 41, 341, 512), cv2.COLOR_BGR2GRAY) for i in range(10)}
    h = {p: hash_image(cv2.cvtColor(g, cv2.COLOR_GRAY2BGR), g) for p, g in imgs.items()}
    # force one pHash-connected component of 10 frames, then let SSIM split it
    ph = h["r0"]["phash"]
    items = [ClusterItem(p, "A", i * 0.3, ph, h[p]["block_mean"], h[p]["color_moment"]) for i, p in enumerate(imgs)]
    cfg = {**CFG, "refine_enabled": True, "refine_min_size": 9}
    r = cluster_items(items, "ev", cfg, load_gray=lambda p: imgs[p])
    assert sorted(tuple(g["photo_ids"]) for g in r["groups"]) == [
        ("r0", "r1", "r2", "r3", "r4"), ("r5", "r6", "r7", "r8", "r9")]
    off = cluster_items(items, "ev", {**cfg, "refine_enabled": False}, load_gray=lambda p: imgs[p])
    assert off["n_groups"] == 1

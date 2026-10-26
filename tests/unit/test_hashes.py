"""compute_hashes: pHash / blockMeanHash / colorMomentHash on synthetic images."""

from __future__ import annotations

import cv2
import numpy as np

from keepframe.io.images import resize_long_edge
from keepframe.tools.hashes import (
    color_moment_l2,
    from_jsonable,
    hamming,
    hamming_matrix,
    hash_image,
    phash_distance_cv,
    to_jsonable,
)
from keepframe.worker.synth import exposure_shift, jitter, scene


def _h(img):
    return hash_image(resize_long_edge(img, 1024))


def test_shapes_and_dtypes():
    h = _h(scene(1))
    assert h["phash"].shape == (8,) and h["phash"].dtype == np.uint8
    assert h["block_mean"].shape == (32,) and h["block_mean"].dtype == np.uint8
    assert h["color_moment"].shape == (42,) and h["color_moment"].dtype == np.float64


def test_hamming_matches_opencv_comparator():
    a, b = _h(scene(1)), _h(scene(2))
    assert int(hamming(a["phash"], b["phash"])) == int(phash_distance_cv(a["phash"], b["phash"]))
    assert int(hamming(a["phash"], a["phash"])) == 0


def test_resized_and_jpeg_copies_are_near():
    base = scene(7)
    ref = _h(base)
    small = cv2.resize(base, (800, 533), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", base, [cv2.IMWRITE_JPEG_QUALITY, 70])
    recoded = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    for variant in (small, recoded, jitter(base, 3)):
        h = _h(variant)
        assert hamming(ref["phash"], h["phash"]) <= 10
        assert hamming(ref["block_mean"], h["block_mean"]) <= 25


def test_small_crop_is_near_but_different_scene_is_far():
    """pHash tolerates ~2% border crops (it is not crop-invariant; >=3% can exceed 10 bits)."""
    base = scene(9)
    hh, ww = base.shape[:2]
    crop = base[int(hh * 0.02) : int(hh * 0.98), int(ww * 0.02) : int(ww * 0.98)]
    ref = _h(base)
    assert hamming(ref["phash"], _h(crop)["phash"]) <= 10
    other = _h(scene(10))
    assert hamming(ref["phash"], other["phash"]) > 10
    assert hamming(ref["block_mean"], other["block_mean"]) > 25


def test_color_moment_separates_exposure_change():
    base = scene(11)
    near = color_moment_l2(_h(base)["color_moment"], _h(jitter(base, 1))["color_moment"])
    far = color_moment_l2(_h(base)["color_moment"], _h(exposure_shift(base, 0.3))["color_moment"])
    assert near < far


def test_vectorised_matrix_and_json_roundtrip():
    hs = [_h(scene(i)) for i in range(5)]
    stack = np.stack([h["phash"] for h in hs])
    m = hamming_matrix(stack)
    assert m.shape == (5, 5) and np.all(np.diag(m) == 0) and np.array_equal(m, m.T)
    for i in range(5):
        for j in range(5):
            assert m[i, j] == hamming(hs[i]["phash"], hs[j]["phash"])
    rt = from_jsonable(to_jsonable(hs[0]))
    assert np.array_equal(rt["phash"], hs[0]["phash"]) and np.array_equal(rt["block_mean"], hs[0]["block_mean"])
    assert np.allclose(rt["color_moment"], hs[0]["color_moment"], atol=1e-5)

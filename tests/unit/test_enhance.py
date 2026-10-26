from __future__ import annotations

import cv2
import numpy as np
import pytest
from _faces_fixtures import load, needs_models

from keepframe.tools.composition import score_composition
from keepframe.tools.enhance import clahe_l, enhance_keeper, grayworld
from keepframe.tools.faces import detect_faces
from keepframe.tools.publish_blur import blur_faces_for_publication

pytestmark = needs_models


def test_default_ops_and_shape(tmp_path) -> None:  # noqa: ANN001
    res = enhance_keeper(load("open_a"))
    assert set(res) >= {"s3_key", "ops_applied", "ops_skipped", "delta_brisque", "jpeg", "written"}
    assert res["ops_applied"] == ["wb", "clahe"] and res["ops_skipped"] == ["denoise"]
    assert res["s3_key"] is None and not res["written"]
    dec = cv2.imdecode(np.frombuffer(res["jpeg"], np.uint8), cv2.IMREAD_COLOR)
    assert dec.shape == (512, 512, 3)
    assert res["delta_brisque"] == pytest.approx(res["brisque_after"] - res["brisque_before"], abs=0.02)


def test_writer_gets_plan_key_and_preview_is_capped_at_1024() -> None:
    big = cv2.resize(load("open_b"), (2560, 1707))
    got: dict[str, bytes] = {}
    res = enhance_keeper(
        big, ["clahe"], event_id="ev_1", photo_id="p_1", writer=lambda k, b: got.update({k: b})
    )
    assert res["s3_key"] == "preview/ev_1/p_1.jpg" and res["written"] and "jpeg" not in res
    assert max(res["width"], res["height"]) == 1024
    assert list(got) == ["preview/ev_1/p_1.jpg"] and len(got["preview/ev_1/p_1.jpg"]) == res["bytes"]


def test_out_path(tmp_path) -> None:  # noqa: ANN001
    p = tmp_path / "x.jpg"
    res = enhance_keeper(load("open_c"), ["wb"], out_path=p)
    assert res["written"] and cv2.imread(str(p)) is not None


def test_denoise_gated_by_iso() -> None:
    img = load("open_c")
    assert "denoise" in enhance_keeper(img, ["denoise"], iso=6400)["ops_applied"]
    assert enhance_keeper(img, ["denoise"], iso=200)["ops_skipped"] == ["denoise"]


def test_denoise_gated_by_brisque_on_noisy_image() -> None:
    rng = np.random.default_rng(0)
    clean = load("open_c")
    noisy = np.clip(clean.astype(np.int16) + rng.normal(0, 40, clean.shape), 0, 255).astype(np.uint8)
    res = enhance_keeper(noisy, ["denoise"])
    assert res["brisque_before"] > 50 and res["ops_applied"] == ["denoise"]
    # BRISQUE is not monotonic in noise (see docs/CV_FACES.md); check the pixels got closer to clean
    out = cv2.imdecode(np.frombuffer(res["jpeg"], np.uint8), cv2.IMREAD_COLOR)
    err = lambda x: float(np.abs(x.astype(np.float32) - clean).mean())  # noqa: E731
    assert err(out) < err(noisy)


def test_unknown_op_rejected_and_writer_needs_ids() -> None:
    with pytest.raises(ValueError):
        enhance_keeper(load("open_a"), ["sharpen"])
    with pytest.raises(ValueError):
        enhance_keeper(load("open_a"), ["wb"], writer=lambda k, b: None)


def test_clahe_raises_contrast_and_grayworld_neutralises_cast() -> None:
    img = load("open_a")
    flat = cv2.convertScaleAbs(img, alpha=0.4, beta=80)
    L = lambda x: cv2.cvtColor(x, cv2.COLOR_BGR2Lab)[:, :, 0].std()  # noqa: E731
    assert L(clahe_l(flat, 2.0, 8)) > L(flat) * 1.2
    cast = img.copy()
    cast[:, :, 0] = np.clip(cast[:, :, 0].astype(int) + 60, 0, 255).astype(np.uint8)  # blue cast
    m0, m1 = cast.reshape(-1, 3).mean(0), grayworld(cast, 0.99).reshape(-1, 3).mean(0)
    assert np.ptp(m1) < np.ptp(m0)


def test_composition_centrality() -> None:
    img = np.full((600, 900, 3), 40, np.uint8)
    cv2.circle(img, (600, 200), 60, (255, 255, 255), -1)  # on a thirds point
    on = score_composition(img)
    img2 = np.full((600, 900, 3), 40, np.uint8)
    cv2.circle(img2, (450, 300), 60, (255, 255, 255), -1)  # dead centre
    centre = score_composition(img2)
    assert on["subject_centrality"] > 0.9 > centre["subject_centrality"]
    assert on["nima_mean"] is None and on["nima_available"] is False


def test_publish_blur_removes_detectable_faces() -> None:
    img = cv2.hconcat([load("open_a"), load("closed_b")])
    res = blur_faces_for_publication(img)
    assert res["n_blurred"] >= 2 and res["image"].shape == img.shape
    assert detect_faces(res["image"], min_score=0.6)["n_faces"] == 0
    assert not np.array_equal(res["image"], img)
    assert np.array_equal(img, cv2.hconcat([load("open_a"), load("closed_b")]))  # input untouched

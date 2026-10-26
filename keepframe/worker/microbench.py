"""Per-stage micro-benchmark of the core CV tools on synthetic images (single thread, like a pool worker).

    python -m keepframe.worker.microbench [--repeats 30] [--json out.json]

Stages mirror docs/REPORT.md §3: decode of a 2560-px WebP (the processed delivery size), resize to 1024 (INTER_AREA),
proxy JPEG q90 encode/decode, each hash, Laplacian/Tenengrad/FFT, saliency ROI, BRISQUE (if the LIVE model
is present), exposure, best-of ranking, contact sheet. Reports p50/p95 ms.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from collections.abc import Callable
from typing import Any

import cv2
import numpy as np

from keepframe import opencv_info
from keepframe.config import section
from keepframe.io import images
from keepframe.tools import sharpness as sh
from keepframe.tools.best import rank_group
from keepframe.tools.exposure import score_exposure_image
from keepframe.tools.hashes import hamming
from keepframe.tools.sheet import render_sheet
from keepframe.worker.synth import scene


def _time(fn: Callable[[], Any], repeats: int) -> dict[str, float]:
    fn()  # warm-up
    xs = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1e3)
    xs.sort()
    return {"p50_ms": round(statistics.median(xs), 3), "p95_ms": round(xs[min(len(xs) - 1, int(0.95 * len(xs)))], 3),
            "n": repeats}


def run(repeats: int = 30) -> dict[str, Any]:
    cv2.setNumThreads(1)
    full = scene(77, 1707, 2560)
    webp = images.encode(full, ".webp", 85)
    p = images.resize_long_edge(full, 1024)
    jpg = images.encode(p, ".jpg", 90)
    gray = cv2.cvtColor(p, cv2.COLOR_BGR2GRAY)
    cfg = section("sharpness")
    h1 = cv2.img_hash.pHash(gray).ravel()
    many = np.repeat(h1[None, :], 10_000, axis=0)
    res: dict[str, dict[str, float]] = {}
    res["decode_webp_2560"] = _time(lambda: images.decode(webp), repeats)
    res["resize_2560_to_1024_area"] = _time(lambda: images.resize_long_edge(full, 1024), repeats)
    res["proxy_encode_jpeg_q90"] = _time(lambda: images.encode(p, ".jpg", 90), repeats)
    res["proxy_decode_jpeg_1024"] = _time(lambda: images.decode(jpg), repeats)
    res["to_gray"] = _time(lambda: cv2.cvtColor(p, cv2.COLOR_BGR2GRAY), repeats)
    res["phash"] = _time(lambda: cv2.img_hash.pHash(gray), repeats)
    res["block_mean_hash_mode0"] = _time(
        lambda: cv2.img_hash.blockMeanHash(gray, mode=cv2.img_hash.BLOCK_MEAN_HASH_MODE_0), repeats)
    res["color_moment_hash"] = _time(lambda: cv2.img_hash.colorMomentHash(p), repeats)
    res["hamming_1_vs_10k_numpy"] = _time(lambda: hamming(h1[None, :], many), repeats)
    res["laplacian_var"] = _time(lambda: cv2.Laplacian(gray, cv2.CV_64F, ksize=3).var(), repeats)
    res["tenengrad_sobel"] = _time(lambda: sh.gradient_metrics(gray), repeats)
    res["fft_hf_500"] = _time(lambda: sh.fft_hf(gray, int(cfg["fft_resize"]), int(cfg["fft_window"])), repeats)
    res["sharpness_metrics_all"] = _time(lambda: sh.sharpness_metrics(gray, cfg), repeats)
    res["saliency_roi_512"] = _time(
        lambda: sh.saliency_roi(p, float(cfg["saliency_thresh_frac"]), int(cfg["saliency_long_edge"])), repeats)
    res["saliency_full_1024"] = _time(
        lambda: cv2.saliency.StaticSaliencyFineGrained.create().computeSaliency(p), max(5, repeats // 3))
    if sh.brisque_available():
        res["brisque_1024"] = _time(lambda: sh.brisque_score(p), max(5, repeats // 3))
    res["score_sharpness_full"] = _time(lambda: sh.score_sharpness_image(p, "full", brisque_mode="never"), repeats)
    res["score_sharpness_saliency"] = _time(lambda: sh.score_sharpness_image(p, "saliency", brisque_mode="never"),
                                            repeats)
    res["score_exposure"] = _time(lambda: score_exposure_image(p), repeats)
    group = {f"p{i}": {"sharpness_full_processed": {"lap_var": 100.0 + i * 37}, "exposure": {"label": "ok"}}
             for i in range(8)}
    res["pick_best_8"] = _time(lambda: rank_group(group), repeats)
    res["contact_sheet_4"] = _time(lambda: render_sheet([(p, "p_1 0.81", True)] + [(p, "p", False)] * 3),
                                   max(5, repeats // 3))
    info = opencv_info()
    return {"host": {"machine": platform.machine(), "processor": platform.processor(), "python":
                     platform.python_version(), "threads": cv2.getNumThreads()},
            "opencv_version": info["opencv_version"], "cv2_file": info["cv2_file"], "proxy_shape": list(p.shape),
            "stages": res}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=30)
    ap.add_argument("--json")
    a = ap.parse_args()
    out = run(a.repeats)
    text = json.dumps(out, indent=1)
    if a.json:
        open(a.json, "w").write(text)
    print(text)


if __name__ == "__main__":
    main()

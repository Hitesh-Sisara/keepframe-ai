#!/usr/bin/env python3
"""Single-thread per-op probe: is the KleidiCV HAL active, and which of our calls does it reach?

Times each OpenCV call on a fixed synthetic 1536x1024 BGR frame (and its gray / 1024-px versions) with
cv2.setNumThreads(1), p50 of --repeats runs after 5 warm-ups. Ops are of two kinds:
  ours      the exact calls bench/run_benchmark.py times per image (resize INTER_AREA, BGR2GRAY, CLAHE,
            Laplacian/Sobel to CV_64F, ...);
  hal-path  calls whose u8 paths the OpenCV 5 KleidiCV HAL implements (GaussianBlur, Sobel u8->CV_16S,
            resize INTER_LINEAR, threshold, erode, ...), so a KleidiCV build should differ on them.
Compare two builds' probe.json side by side (bench/summarize.py does it). No photos involved.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections.abc import Callable
from pathlib import Path

os.environ.setdefault("OPENCV_FORCE_DNN_ENGINE", "3")
import cv2  # noqa: E402
import numpy as np  # noqa: E402


def frame() -> np.ndarray:
    """Deterministic textured BGR frame (gradients + blurred noise), no external files."""
    rng = np.random.default_rng(2026)
    h, w = 1024, 1536
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    base = np.stack([xx / w * 255, yy / h * 255, (xx + yy) / (w + h) * 255], axis=-1)
    noise = cv2.GaussianBlur(rng.normal(0, 40, (h, w, 3)).astype(np.float32), (0, 0), 3)
    return np.clip(base + noise, 0, 255).astype(np.uint8)


def ops(img: np.ndarray) -> dict[str, tuple[str, Callable[[], object]]]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    p1024 = cv2.resize(img, (1024, 683), interpolation=cv2.INTER_AREA)
    g1024 = cv2.cvtColor(p1024, cv2.COLOR_BGR2GRAY)
    f32 = gray.astype(np.float32) / 255.0
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    k3 = np.ones((3, 3), np.uint8)
    return {
        "resize_area_bgr_to_1024": ("ours", lambda: cv2.resize(img, (1024, 683), interpolation=cv2.INTER_AREA)),
        "cvtcolor_bgr2gray_1024": ("ours", lambda: cv2.cvtColor(p1024, cv2.COLOR_BGR2GRAY)),
        "clahe_1024": ("ours", lambda: clahe.apply(g1024)),
        "laplacian_64f_1024": ("ours", lambda: cv2.Laplacian(g1024, cv2.CV_64F, ksize=3)),
        "sobel_64f_1024": ("ours", lambda: cv2.Sobel(g1024, cv2.CV_64F, 1, 0, ksize=3)),
        "phash_1024": ("ours", lambda: cv2.img_hash.pHash(g1024)),
        "resize_linear_bgr_half": ("hal-path", lambda: cv2.resize(img, (768, 512), interpolation=cv2.INTER_LINEAR)),
        "resize_linear_gray_half": ("hal-path", lambda: cv2.resize(gray, (768, 512), interpolation=cv2.INTER_LINEAR)),
        "cvtcolor_bgr2gray_full": ("hal-path", lambda: cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)),
        "cvtcolor_bgr2rgb_full": ("hal-path", lambda: cv2.cvtColor(img, cv2.COLOR_BGR2RGB)),
        "gaussian_3x3_gray": ("hal-path", lambda: cv2.GaussianBlur(gray, (3, 3), 0)),
        "gaussian_5x5_gray": ("hal-path", lambda: cv2.GaussianBlur(gray, (5, 5), 0)),
        "sobel_16s_gray": ("hal-path", lambda: cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)),
        "threshold_binary_gray": ("hal-path", lambda: cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)),
        "erode_3x3_gray": ("hal-path", lambda: cv2.erode(gray, k3)),
        "median_5_gray": ("hal-path", lambda: cv2.medianBlur(gray, 5)),
        "minmaxloc_gray": ("hal-path", lambda: cv2.minMaxLoc(gray)),
        "flip_bgr": ("hal-path", lambda: cv2.flip(img, 1)),
        "transpose_gray": ("hal-path", lambda: cv2.transpose(gray)),
        "exp_f32": ("hal-path", lambda: cv2.exp(f32)),
        "multiply_u8": ("hal-path", lambda: cv2.multiply(gray, gray, scale=1 / 255)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--repeats", type=int, default=60)
    args = ap.parse_args()
    cv2.setNumThreads(1)
    hal = next((ln.split(":", 1)[1].strip() for ln in cv2.getBuildInformation().splitlines()
                if ln.strip().startswith("Custom HAL:")), None)
    res: dict[str, dict[str, object]] = {}
    for name, (kind, fn) in ops(frame()).items():
        for _ in range(5):
            fn()
        ts = []
        for _ in range(args.repeats):
            t0 = time.perf_counter_ns()
            fn()
            ts.append((time.perf_counter_ns() - t0) / 1e6)
        res[name] = {"kind": kind, "p50_ms": round(statistics.median(ts), 4), "min_ms": round(min(ts), 4)}
        print(f"{name:28s} {kind:9s} {res[name]['p50_ms']:9.4f} ms", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"schema": "keepframe/kleidicv-probe/v1", "cv2_version": cv2.__version__,
                                    "custom_hal": hal, "repeats": args.repeats, "ops": res}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

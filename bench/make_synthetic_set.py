#!/usr/bin/env python3
"""Deterministic synthetic benchmark set (no customer photos).

Builds N WebPs shaped like typical delivery-sized processed images (long edge <= 2560, 3:2, WebP q85) from the
OpenCV sample images (opencv/opencv 5.x at a pinned commit) plus pure-noise frames, with blur /
motion / exposure / noise / crop variants so every stage sees realistic content (YuNet sees real
faces in lena/messi5). Writes <out>/<eventId>/<photoId>.webp and a manifest in the
bench/manifest_5k.jsonl shape (subset of fields) so run_benchmark.py treats both sets the same.

Same --seed and --n => byte-identical set on every host (checked via set_sha256 in results.json).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import urllib.request
from pathlib import Path

import cv2
import numpy as np

OPENCV_COMMIT = "7a75daab7bb730e48dd157170195237a4ca38228"
SAMPLES = ("lena.jpg", "messi5.jpg", "baboon.jpg", "fruits.jpg", "building.jpg", "starry_night.jpg",
           "home.jpg", "board.jpg", "aero3.jpg", "graf1.png")
VARIANTS = ("clean", "gauss_blur", "motion_blur", "dark", "bright", "noise", "crop_shift", "random_noise")


def fetch_samples(cache: Path) -> list[np.ndarray]:
    cache.mkdir(parents=True, exist_ok=True)
    imgs = []
    for name in SAMPLES:
        p = cache / name
        if not p.exists():
            url = f"https://raw.githubusercontent.com/opencv/opencv/{OPENCV_COMMIT}/samples/data/{name}"
            with urllib.request.urlopen(url, timeout=60) as r:
                p.write_bytes(r.read())
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            raise SystemExit(f"cannot decode sample {p}")
        imgs.append(img)
    return imgs


def to_frame(src: np.ndarray, long_edge: int, portrait: bool) -> np.ndarray:
    """Center-crop to 3:2 (or 2:3) and resize so the long edge is long_edge."""
    h, w = src.shape[:2]
    target = 2 / 3 if portrait else 3 / 2
    if w / h > target:
        nw = int(h * target)
        src = src[:, (w - nw) // 2:(w - nw) // 2 + nw]
    else:
        nh = int(w / target)
        src = src[(h - nh) // 2:(h - nh) // 2 + nh]
    if portrait:
        size = (round(long_edge * 2 / 3), long_edge)
    else:
        size = (long_edge, round(long_edge * 2 / 3))
    return cv2.resize(src, size, interpolation=cv2.INTER_CUBIC)


def apply_variant(img: np.ndarray, variant: str, rng: np.random.Generator) -> np.ndarray:
    if variant == "gauss_blur":
        k = int(rng.choice([7, 11, 15])) | 1
        return cv2.GaussianBlur(img, (k, k), 0)
    if variant == "motion_blur":
        n = int(rng.integers(15, 41))
        ker = np.zeros((n, n), np.float32)
        ker[n // 2, :] = 1.0 / n
        rot = cv2.getRotationMatrix2D((n / 2 - 0.5, n / 2 - 0.5), float(rng.uniform(0, 180)), 1.0)
        ker = cv2.warpAffine(ker, rot, (n, n))
        ker /= max(ker.sum(), 1e-6)
        return cv2.filter2D(img, -1, ker)
    if variant == "dark":
        lut = (np.power(np.arange(256) / 255.0, 2.6) * 255).astype(np.uint8)
        return cv2.LUT(img, lut)
    if variant == "bright":
        return cv2.convertScaleAbs(img, alpha=1.3, beta=70)
    if variant == "noise":
        n = rng.normal(0, 18, img.shape).astype(np.float32)
        return np.clip(img.astype(np.float32) + n, 0, 255).astype(np.uint8)
    if variant == "crop_shift":
        h, w = img.shape[:2]
        dx, dy = int(w * rng.uniform(0.02, 0.08)), int(h * rng.uniform(0.02, 0.08))
        return cv2.resize(img[dy:h - dy, dx:w - dx], (w, h), interpolation=cv2.INTER_LINEAR)
    if variant == "random_noise":
        h, w = img.shape[:2]
        small = rng.integers(0, 256, (h // 8, w // 8, 3), dtype=np.uint8)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return img


def make_one(i: int, seed: int, events: int, quality: int, out: Path, samples: list[np.ndarray]) -> dict:
    rng = np.random.default_rng([seed, i])  # per-index stream: order/parallelism independent
    ev = f"ev_syn_{i % events}"
    pid = f"syn_{i:05d}"
    src = samples[i % len(samples)]
    long_edge = int(rng.choice([2560, 2560, 2560, 2048, 1600]))
    portrait = bool(rng.random() < 0.3)
    variant = VARIANTS[int(rng.integers(0, len(VARIANTS)))]
    img = apply_variant(to_frame(src, long_edge, portrait), variant, rng)
    ok, buf = cv2.imencode(".webp", img, [cv2.IMWRITE_WEBP_QUALITY, quality])
    if not ok:
        raise RuntimeError("webp encode failed")
    dst = out / ev / f"{pid}.webp"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(buf.tobytes())
    h, w = img.shape[:2]
    return {"photo_id": pid, "event_id": ev, "processed_key": f"raw/{ev}/{pid}.webp", "width": w, "height": h,
            "synthetic": True, "variant": variant, "source_sample": SAMPLES[i % len(SAMPLES)]}


_SAMPLES: list[np.ndarray] = []


def _job(a: tuple) -> dict:
    if not _SAMPLES:
        cv2.setNumThreads(1)
        _SAMPLES.extend(fetch_samples(a[5]))
    return make_one(*a[:5], _SAMPLES)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--events", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--out", type=Path, default=Path("/tmp/benchsyn"))
    ap.add_argument("--manifest", type=Path, default=None, help="default <out>/manifest_synthetic.jsonl")
    ap.add_argument("--cache", type=Path, default=Path("/tmp/opencv-samples"))
    ap.add_argument("--quality", type=int, default=85)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args()

    fetch_samples(args.cache)  # download once before forking
    manifest = args.manifest or args.out / "manifest_synthetic.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    jobs = [(i, args.seed, args.events, args.quality, args.out, args.cache) for i in range(args.n)]
    with cf.ProcessPoolExecutor(max_workers=args.jobs) as ex:
        rows = list(ex.map(_job, jobs, chunksize=4))
    with manifest.open("w") as mf:
        for r in rows:
            mf.write(json.dumps(r) + "\n")
    digest = hashlib.sha256()
    for r in rows:
        digest.update(hashlib.sha256((args.out / r["event_id"] / f"{r['photo_id']}.webp").read_bytes()).digest())
    print(f"wrote {args.n} images to {args.out}, manifest {manifest}, set_sha256 {digest.hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

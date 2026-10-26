"""Synthetic images and events for tests and offline smoke runs (no customer photos).

    python -m keepframe.worker.synth --root /tmp/cull-local --event-id ev_synth [--n-bursts 3]

writes a LOCAL_MODE tree: manifests/<ev>.jsonl, manifests/<ev>.meta.json, raw/<ev>/<pid>.webp,
originals/<ev>/<pid>.jpg (some), exif/<ev>/<pid>.exif.json (some).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def scene(seed: int, h: int = 1365, w: int = 2048) -> np.ndarray:
    """Textured, natural-ish BGR scene: gradient background, shapes, lines, text, mild grain."""
    r = np.random.default_rng(seed)
    corners = np.clip(r.uniform(60, 190, 3) + r.uniform(-60, 60, (2, 2, 3)), 0, 255).astype(np.uint8)
    img = cv2.resize(corners, (w, h), interpolation=cv2.INTER_LINEAR)  # smooth gradient background
    s = w / 1024
    for _ in range(45):
        c = tuple(int(x) for x in r.integers(0, 255, 3))
        if r.random() < 0.5:
            cv2.circle(img, (int(r.integers(0, w)), int(r.integers(0, h))), int(r.integers(10, 130) * s), c, -1,
                       cv2.LINE_AA)
        else:
            x, y = int(r.integers(0, w)), int(r.integers(0, h))
            cv2.rectangle(img, (x, y), (x + int(r.integers(20, 200) * s), y + int(r.integers(20, 200) * s)), c, -1)
    for _ in range(40):
        c = tuple(int(x) for x in r.integers(0, 255, 3))
        a = (int(r.integers(0, w)), int(r.integers(0, h)))
        b = (int(r.integers(0, w)), int(r.integers(0, h)))
        cv2.line(img, a, b, c, int(r.integers(1, 4)), cv2.LINE_AA)
    cv2.putText(img, f"KEEPFRAME {seed:04d}", (int(80 * s), int(h * 0.55)), cv2.FONT_HERSHEY_SIMPLEX, 3 * s,
                (15, 15, 15), int(6 * s), cv2.LINE_AA)
    return add_grain(img, 4.0, seed)


def add_grain(img: np.ndarray, sigma: float, seed: int) -> np.ndarray:
    """Gaussian sensor-like grain (cv2.randn, seeded for reproducibility)."""
    cv2.setRNGSeed(int(seed) & 0x7FFFFFFF)
    noise = np.empty(img.shape, np.int16)
    # cv2.randn with scalar mean/stddev on a multi-channel Mat fills only channel 0; the fixtures were calibrated
    # on that, so pass per-channel arrays with the same effect (bit-identical). A 1-element array is NOT
    # equivalent: it yields garbage (std ~15000) on 3-channel int16 in OpenCV 5.0.0.
    ch = img.shape[2] if img.ndim == 3 else 1
    sd = np.zeros(ch)
    sd[0] = float(sigma)
    cv2.randn(noise, np.zeros(ch), sd)
    out: np.ndarray = cv2.add(img.astype(np.int16), noise, dtype=cv2.CV_16S).clip(0, 255).astype(np.uint8)
    return out


def checkerboard(h: int = 683, w: int = 1024, cell: int = 16) -> np.ndarray:
    ys = (np.arange(h) // cell) % 2
    xs = (np.arange(w) // cell) % 2
    g = ((ys[:, None] ^ xs[None, :]) * 255).astype(np.uint8)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def gaussian_blur(img: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(img, (0, 0), sigma)


def motion_blur(img: np.ndarray, length: int = 31, angle_deg: float = 0.0) -> np.ndarray:
    line = np.zeros((length, length), np.float32)
    line[length // 2, :] = 1.0 / length
    m = cv2.getRotationMatrix2D((length / 2 - 0.5, length / 2 - 0.5), angle_deg, 1.0)
    k = np.asarray(cv2.warpAffine(line, m, (length, length)), dtype=np.float32)
    k /= max(float(k.sum()), 1e-6)
    return cv2.filter2D(img, -1, k)


def jitter(img: np.ndarray, seed: int, max_shift: float = 6.0) -> np.ndarray:
    """A burst neighbour: small translation + fresh grain."""
    r = np.random.default_rng(seed)
    dx, dy = r.uniform(-max_shift, max_shift, 2)
    m = np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float32)
    out = cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REFLECT)
    return add_grain(out, 2.0, seed + 7919)


def exposure_shift(img: np.ndarray, gain: float, bias: float = 0.0) -> np.ndarray:
    return cv2.convertScaleAbs(img, alpha=gain, beta=bias)


def _write(root: Path, key: str, data: bytes) -> None:
    p = root / key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def make_event(root: str | Path, event_id: str = "ev_synth", camera: str = "SYNTH-CAM",
               start: datetime | None = None, size: tuple[int, int] = (1365, 2048),
               webp_quality: int = 92, seed: int = 0, prefix: str = "") -> dict[str, Any]:
    """12-photo synthetic event. Returns {"event_id", "photos": [...], "expect": {photo_id: note}}.

    Layout (capture order):
      burst A (4): a1 sharp, a2 sharp jitter, a3 heavy gaussian blur, a4 sharp jitter   -> 1 group
      burst B (3): b1 sharp, b2 = exact md5 copy of b1, b3 motion blurred               -> 1 group
      singles    : s_sharp, s_blur (gaussian), s_dark (underexposed), s_over (blown),
                   s_flat (near-uniform frame)
    Photos are 30 s apart between scenes, 0.4 s apart inside bursts. s_blur has an original (JPEG).
    size = (h, w) of the processed images (default 2048 long edge like typical processed delivery WebPs).
    seed offsets every scene (distinct content per call); prefix is prepended to photo ids. Multiple calls with
    the same event_id append to one event (bench sets); the manifest is rewritten with all rows.
    """
    H, W = size
    k = W / 2048  # blur strengths scale with resolution
    # blur strong enough that Laplacian AND FFT both vote blurry (grain keeps FFT "sharp" for milder blur),
    # so expected verdicts do not depend on the optional BRISQUE model files
    root = Path(root)
    start = start or datetime(2026, 10, 5, 13, 0, 0, tzinfo=UTC)  # 18:30 IST
    rows: list[tuple[str, np.ndarray, float, dict[str, Any]]] = []
    a = scene(101 + seed, H, W)
    rows += [("a1", a, 0.0, {}), ("a2", jitter(a, 1, 6 * k), 0.4, {}),
             ("a3", gaussian_blur(jitter(a, 2, 6 * k), 12.0 * k), 0.8, {}), ("a4", jitter(a, 3, 6 * k), 1.2, {})]
    b = scene(202 + seed, H, W)
    rows += [("b1", b, 30.0, {}), ("b2", b, 30.4, {"same_bytes_as": "b1"}),
             ("b3", motion_blur(jitter(b, 4, 6 * k), max(9, int(41 * k) | 1)), 30.8, {})]
    rows += [("s_sharp", scene(303 + seed, H, W), 60.0, {}),
             ("s_blur", gaussian_blur(scene(404 + seed, H, W), 12.0 * k), 90.0, {"original": True}),
             ("s_dark", exposure_shift(scene(505 + seed, H, W), 0.05), 120.0, {}),
             ("s_over", exposure_shift(scene(606 + seed, H, W), 1.0, 170.0), 150.0, {}),
             ("s_flat", np.full((H, W, 3), 100 + (seed // 1000) * 7 % 97, np.uint8), 180.0, {})]

    encoded: dict[str, bytes] = {}
    photos = []
    rows = [(prefix + pid, img, dt, {k: (prefix + v if k == "same_bytes_as" else v) for k, v in opt.items()})
            for pid, img, dt, opt in rows]
    for pid, img, dt, opt in rows:
        data = encoded[opt["same_bytes_as"]] if "same_bytes_as" in opt else bytes(
            cv2.imencode(".webp", img, [cv2.IMWRITE_WEBP_QUALITY, webp_quality])[1])
        encoded[pid] = data
        _write(root, f"raw/{event_id}/{pid}.webp", data)
        when = start + timedelta(seconds=dt)
        rec: dict[str, Any] = {
            "photo_id": pid, "event_id": event_id, "album": "main", "name": f"{pid}.jpg",
            "clicked_at": when.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "created_at": (when + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "camera": camera, "lens": "SYNTH 50mm", "width": img.shape[1], "height": img.shape[0],
            "md5": hashlib.md5(data).hexdigest(),  # noqa: S324 - matches the source md5 field, not security
            "processed_key": f"{event_id}/{pid}.webp", "mfaces": 0, "frs_status": None, "frs_faces": [],
            "rek_faces": [],
        }
        if opt.get("original"):
            ok, jb = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            _write(root, f"originals/{event_id}/{pid}.jpg", bytes(jb))
            rec.update(original_key=f"{event_id}/{pid}.jpg", original_width=img.shape[1],
                       original_height=img.shape[0])
        frac = round(dt % 1, 3)
        local = when + timedelta(hours=5, minutes=30)
        exif = {"EXIF": {"ExposureTime": "1/250", "ISO": 800, "DateTimeOriginal": local.strftime("%Y:%m:%d %H:%M:%S"),
                         "OffsetTimeOriginal": "+05:30", "SubSecTimeOriginal": f"{int(frac * 1000):03d}"}}
        _write(root, f"exif/{event_id}/{pid}.exif.json", json.dumps(exif).encode())
        rec["exif_key"] = f"{event_id}/{pid}.exif.json"
        photos.append(rec)
    mpath = root / f"manifests/{event_id}.jsonl"
    prev = [json.loads(x) for x in mpath.read_text().splitlines()] if prefix and mpath.exists() else []
    keep_prev = [r for r in prev if r["photo_id"] not in {p["photo_id"] for p in photos}]
    _write(root, f"manifests/{event_id}.jsonl", "".join(json.dumps(r) + "\n" for r in keep_prev + photos).encode())
    meta = {"event_id": event_id, "name": "Synthetic event", "category": "ceremony", "event_date": start.date().isoformat(),
            "n_photos": len(photos), "n_with_original": sum(1 for p in photos if p.get("original_key")),
            "cameras": [camera], "exported_at": start.isoformat()}
    _write(root, f"manifests/{event_id}.meta.json", json.dumps(meta).encode())
    return {"event_id": event_id, "photos": photos}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--event-id", default="ev_synth")
    a = ap.parse_args()
    ev = make_event(a.root, a.event_id)
    print(json.dumps({"event_id": ev["event_id"], "n_photos": len(ev["photos"]), "root": str(Path(a.root).resolve())}))


if __name__ == "__main__":
    main()

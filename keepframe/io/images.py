"""Image codec helpers. All decoding goes through cv2.imdecode (no Pillow, by design).

OpenCV 5 cannot decode HEIC; `decode()` returns None for HEIC/HEIF input so callers fall back to
the processed WebP and record `variant_fallback=true`.
"""

from __future__ import annotations

import cv2
import numpy as np

HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1")
HEIC_EXTS = (".heic", ".heif", ".hif")


def is_heic(data: bytes | None = None, key: str | None = None) -> bool:
    """True for HEIC/HEIF by extension or by the ISO-BMFF `ftyp` brand."""
    if key and key.lower().endswith(HEIC_EXTS):
        return True
    if data and len(data) >= 12 and data[4:8] == b"ftyp":
        return data[8:12] in HEIC_BRANDS
    return False


def decode(data: bytes, flags: int = cv2.IMREAD_COLOR, key: str | None = None) -> np.ndarray | None:
    """Decode bytes to BGR uint8 (or gray with IMREAD_GRAYSCALE). None for HEIC or undecodable data."""
    if not data or is_heic(data, key):
        return None
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, flags)
    if img is None or img.size == 0:
        return None
    return img


def encode(img: np.ndarray, ext: str = ".jpg", quality: int = 90) -> bytes:
    """Encode to .jpg/.jpeg, .webp or .png. `quality` is JPEG/WebP quality or PNG compression 0-9."""
    ext = ext if ext.startswith(".") else "." + ext
    e = ext.lower()
    if e in (".jpg", ".jpeg"):
        params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
    elif e == ".webp":
        params = [cv2.IMWRITE_WEBP_QUALITY, int(quality)]
    elif e == ".png":
        params = [cv2.IMWRITE_PNG_COMPRESSION, int(min(max(quality, 0), 9))]
    else:
        raise ValueError(f"unsupported output format {ext}")
    ok, buf = cv2.imencode(e, img, params)
    if not ok:
        raise ValueError(f"cv2.imencode failed for {ext}")
    return buf.tobytes()


def resize_long_edge(img: np.ndarray, long_edge: int, upscale: bool = False) -> np.ndarray:
    """Resize so max(h, w) == long_edge with INTER_AREA (INTER_CUBIC if upscaling is allowed)."""
    h, w = img.shape[:2]
    cur = max(h, w)
    if cur == long_edge or (cur < long_edge and not upscale):
        return img
    s = long_edge / cur
    size = (max(1, round(w * s)), max(1, round(h * s)))
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC
    return cv2.resize(img, size, interpolation=interp)


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

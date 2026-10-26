"""make_contact_sheet helper tool: a JPEG grid of a group's frames for approvals and the report.

Built with cv2.resize + cv2.hconcat/vconcat; captions use the OpenCV 5 cv2.FontFace text API
(putText(img, text, org, color, fface, size, weight)), falling back to Hershey fonts if absent.
Writes sheets/<event_id>/<group_id>.jpg to the work store.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import cv2
import numpy as np

from keepframe.config import section
from keepframe.schemas import ContactSheetResult

_font: Any = None


def _font_face() -> Any:
    global _font
    if _font is None and hasattr(cv2, "FontFace"):
        try:
            _font = cv2.FontFace("sans")
        except cv2.error:
            _font = False
    return _font or None


def put_label(img: np.ndarray, text: str, org: tuple[int, int], color: tuple[int, int, int], px: int = 18,
              weight: int = 600) -> None:
    """Draw text in place, preferring the OpenCV 5 FontFace renderer."""
    ff = _font_face()
    if ff is not None:
        try:
            cv2.putText(img, text, org, color, ff, px, weight)
            return
        except cv2.error:
            pass
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, px / 30.0, color, 1 if weight < 600 else 2, cv2.LINE_AA)


def _tile(img: np.ndarray, tile_h: int, tile_w: int, caption: str, highlight: bool) -> np.ndarray:
    h, w = img.shape[:2]
    s = min(tile_h / h, tile_w / w)
    small = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    bar = 28
    canvas = np.full((tile_h + bar, tile_w, 3), 24, np.uint8)
    y0, x0 = (tile_h - small.shape[0]) // 2, (tile_w - small.shape[1]) // 2
    canvas[y0 : y0 + small.shape[0], x0 : x0 + small.shape[1]] = small
    color = (80, 200, 80) if highlight else (230, 230, 230)
    if highlight:
        cv2.rectangle(canvas, (1, 1), (tile_w - 2, tile_h + bar - 2), (80, 200, 80), 3)
    put_label(canvas, caption[:40], (8, tile_h + 20), color, 16)
    return canvas


def render_sheet(tiles: Sequence[tuple[np.ndarray, str, bool]], cols: int | None = None,
                 tile_h: int | None = None, title: str | None = None) -> np.ndarray:
    """tiles = [(bgr, caption, highlight)] -> BGR sheet image."""
    cfg = section("worker")
    cols = max(1, min(cols or int(cfg["sheet_cols"]), len(tiles)))
    tile_h = tile_h or int(cfg["sheet_tile_h"])
    tile_w = int(tile_h * 1.5)
    cells = [_tile(img, tile_h, tile_w, cap, hl) for img, cap, hl in tiles]
    blank = np.full_like(cells[0], 24)
    while len(cells) % cols:
        cells.append(blank)
    rows = [cv2.hconcat(cells[i : i + cols]) for i in range(0, len(cells), cols)]
    sheet = cv2.vconcat(rows) if len(rows) > 1 else rows[0]
    if title:
        head = np.full((36, sheet.shape[1], 3), 40, np.uint8)
        put_label(head, title[:120], (10, 25), (255, 255, 255), 20, 700)
        sheet = cv2.vconcat([head, sheet])
    return sheet


def make_contact_sheet(group_id: str, event_id: str | None = None, photo_ids: list[str] | None = None,
                       captions: dict[str, str] | None = None, best_photo_id: str | None = None,
                       background: bool = False) -> dict[str, Any]:
    """Render the group's frames (from 1024-px proxies) into sheets/<event_id>/<group_id>.jpg.

    background=True queues the upload (caller must call proxies.flush_writes()); default uploads inline.
    Returns {key, n_tiles, width, height}.
    """
    from keepframe.io import images, proxies
    from keepframe.tools.ingest import _contexts

    if photo_ids is None or event_id is None:
        for ctx in ([_contexts[event_id]] if event_id else _contexts.values()):
            if group_id in ctx.groups:
                event_id, photo_ids = ctx.event_id, photo_ids or ctx.groups[group_id]
                break
    if not photo_ids or not event_id:
        raise KeyError(f"unknown group {group_id}")
    captions = captions or {}
    tiles = [(proxies.load_p1024(event_id, p), captions.get(p, p), p == best_photo_id) for p in photo_ids]
    sheet = render_sheet(tiles, title=f"{group_id}  ({len(photo_ids)} frames)")
    key = f"sheets/{event_id}/{group_id}.jpg"
    data = images.encode(sheet, ".jpg", int(section("worker")["sheet_jpeg_quality"]))
    if background:
        proxies.put_background(key, data, content_type="image/jpeg")
    else:
        proxies.get_store().put_bytes(key, data, content_type="image/jpeg")
    return ContactSheetResult(key=key, n_tiles=len(tiles), width=sheet.shape[1], height=sheet.shape[0]).model_dump()

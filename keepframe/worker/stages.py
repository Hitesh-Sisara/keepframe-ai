"""Per-photo stage functions for the batch worker. They run inside pool workers (cv2.setNumThreads(1))
and return plain dicts (results + per-stage timings) to the parent.

Stage A  prepare_photo   proxy (decode, resize, JPEG q90 proxy) + hashes
Stage B  analyse_photo   exposure; sharpness on the P1 ROI (saliency for singletons, full for group members);
                         faces (face detector if importable, else manifest frsInfo/Rekognition boxes);
                         P2 escalation (faces ROI / original); P3 eyes + face quality for group members.
"""

from __future__ import annotations

import importlib
import importlib.util
import time
from functools import lru_cache
from typing import Any

from keepframe.config import section
from keepframe.io import images, proxies
from keepframe.schemas import PhotoRecord
import cv2

from keepframe.tools.exposure import over_flag, score_exposure_image, subject_stats, under_flag
from keepframe.tools.hashes import hash_image, to_jsonable
from keepframe.tools.sharpness import score_sharpness_image
from keepframe.worker import policies


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1e3


# -- face tools (optional, lazy)


@lru_cache(maxsize=None)
def _face_tool(module: str, name: str) -> Any:
    """keepframe.tools.<module>.<name> if the face tools provide it, else None."""
    full = f"keepframe.tools.{module}"
    try:
        if importlib.util.find_spec(full) is None:
            return None
        mod = importlib.import_module(full)
    except Exception:  # noqa: BLE001 - a broken optional module must not stop the worker
        return None
    return getattr(mod, name, None)


def faces_available() -> bool:
    return _face_tool("faces", "detect_faces") is not None


def manifest_faces(rec: PhotoRecord, full_wh: tuple[int, int]) -> list[dict[str, Any]]:
    """Faces from existing source metadata (no detector): frsInfo when indexed, else Rekognition boxes.
    bbox_px is in processed-image pixels (full_wh)."""
    W, H = full_wh
    out: list[dict[str, Any]] = []
    if (rec.frs_status or "") == "indexed" and rec.frs_faces:
        for i, f in enumerate(rec.frs_faces):
            bl, t, w, h = (list(f.bbox) + [0, 0, 0, 0])[:4]
            out.append({"face_id": f"f{i}", "bbox_px": [bl * W, t * H, w * W, h * H], "score": f.det_score,
                        "landmarks5": None, "source": "frsInfo",
                        "min_px": f.min_px if f.min_px is not None else min(w * W, h * H)})
    elif rec.rek_faces:
        for i, rf in enumerate(rec.rek_faces):
            bl, t, w, h = (list(rf.bbox) + [0, 0, 0, 0])[:4]
            conf = rf.confidence
            out.append({"face_id": f"f{i}", "bbox_px": [bl * W, t * H, w * W, h * H],
                        "score": None if conf is None else conf / (100.0 if conf > 1 else 1.0),
                        "landmarks5": None, "source": "rekognition", "min_px": min(w * W, h * H)})
    return out


def _ratio_boxes(faces: list[dict[str, Any]], wh: tuple[int, int]) -> list[list[float]]:
    W, H = wh
    return [[f["bbox_px"][0] / W, f["bbox_px"][1] / H, f["bbox_px"][2] / W, f["bbox_px"][3] / H] for f in faces]


# --------------------------------------------------------------------------- stage A


def prepare_photo(event_id: str, photo_id: str) -> dict[str, Any]:
    """Proxy + hashes. Returns {photo_id, ok, hashes, p1024_wh, timings, error?}."""
    tm: dict[str, float] = {}
    try:
        t0 = time.perf_counter()
        p = proxies.load_p1024(event_id, photo_id, timings=tm)
        tm["proxy_total"] = _ms(t0)
        t0 = time.perf_counter()
        h = to_jsonable(hash_image(p))
        tm["hashes"] = _ms(t0)
        out = {"photo_id": photo_id, "ok": True, "hashes": h, "p1024_wh": [p.shape[1], p.shape[0]], "timings": tm}
        upload = proxies.take_deferred(proxies.proxy_key(event_id, photo_id))
        if upload is not None:
            out["proxy_jpeg"] = upload  # parent uploads in the background
        out["io"] = io_delta()
        return out
    except Exception as e:  # noqa: BLE001
        return {"photo_id": photo_id, "ok": False, "error": f"{type(e).__name__}: {e}"[:300], "timings": tm,
                "io": io_delta()}


# --------------------------------------------------------------------------- stage B


def analyse_photo(event_id: str, photo_id: str, in_group: bool) -> dict[str, Any]:
    """All per-photo vision results needed by the policies. Never raises (errors are returned)."""
    from keepframe.tools.ingest import get_context

    tm: dict[str, float] = {}
    res: dict[str, Any] = {"photo_id": photo_id, "ok": True, "in_group": in_group}
    try:
        ctx = get_context(event_id, create=False)
        info = ctx.photos[photo_id]
        rec = info.record
        wcfg = section("worker")
        p = proxies.load_p1024(event_id, photo_id)
        full_wh = (rec.width, rec.height) if rec.width and rec.height else None
        if full_wh is None:
            full_wh = (p.shape[1], p.shape[0])

        t0 = time.perf_counter()
        exposure = score_exposure_image(p)
        tm["exposure"] = _ms(t0)
        res["exposure"] = exposure

        # P1: singletons -> saliency ROI; group members -> full frame (compared within the group)
        roi = "full" if in_group else "saliency"
        first = score_sharpness_image(p, roi=roi, timings=tm)
        first["variant"], first["variant_fallback"] = "processed", False
        res[f"sharpness_{roi}_processed"] = first
        brisque = first.get("brisque")
        chain = [first]

        # faces: detect_faces (reuses frsInfo/Rekognition boxes, refines landmarks with YuNet) for
        # group members; manifest boxes only (no detector) for singletons or when the face tools are absent.
        faces: list[dict[str, Any]] = []
        faces_source = "none"
        full_img = None
        detect = _face_tool("faces", "detect_faces")
        known = rec.known_face_count()  # frs_status no_faces -> 0 (mfaces is null in real data)
        if in_group and detect is not None and (known is None or known > 0):
            t0 = time.perf_counter()
            try:
                full_img = proxies.load_full(event_id, photo_id)
                tm["faces_load_full"] = _ms(t0)
                t0 = time.perf_counter()
                fr = detect(full_img, event_id=event_id, photo_id=photo_id, record=rec)
                faces = list(fr.get("faces") or [])
                full_wh = (full_img.shape[1], full_img.shape[0])  # detect_faces bbox_px frame
                fr = {**fr, "image_w": full_wh[0], "image_h": full_wh[1]}
                faces_source = "detector"
                res["detect_faces"] = fr
            except Exception as e:  # noqa: BLE001
                res.setdefault("errors", []).append(f"detect_faces: {type(e).__name__}: {e}"[:200])
            tm["faces"] = _ms(t0)
        elif known == 0:
            faces_source = "metadata_none"  # frsInfo no_faces / mfaces 0: no people, detection skipped
        if faces_source not in ("detector", "metadata_none"):
            faces = manifest_faces(rec, full_wh)
            faces_source = "manifest" if faces else ("metadata_none" if known == 0 else "unknown")
        res["faces"] = faces
        res["faces_source"] = faces_source

        # subject lightness from the faces we now know (event baseline is applied later by the parent)
        if faces:
            t0 = time.perf_counter()
            L = cv2.cvtColor(p, cv2.COLOR_BGR2Lab)[:, :, 0]
            exposure.update({k: (round(v, 3) if isinstance(v, float) else v) for k, v in
                             subject_stats(L, _ratio_boxes(faces, full_wh), section("exposure")).items()})
            exposure["under_flag"] = under_flag(exposure, None)
            exposure["over_flag"] = over_flag(exposure, None)
            tm["exposure"] = tm.get("exposure", 0.0) + _ms(t0)

        # faces-ROI sharpness for every group member with faces (like-for-like best pick)
        tried = [f"{roi}:processed"]
        if faces and in_group and wcfg.get("group_faces_roi", True):
            fs = score_sharpness_image(p, roi="faces", face_boxes=_ratio_boxes(faces, full_wh), timings=tm)
            fs["variant"], fs["variant_fallback"] = "processed", False
            res["sharpness_faces_processed"] = fs
            tried.append("faces:processed")
            if fs["roi_used"] == "faces":
                chain.append(fs)

        # P2 escalation
        while True:
            step = policies.next_sharpness_step(chain[-1], len(faces), proxies.original_key(event_id, photo_id) is not None,
                                                tried)
            if step is None:
                break
            tried.append(f"{step['roi']}:{step['variant']}")
            t0 = time.perf_counter()
            img = p
            fallback = False
            if step["variant"] == "original":
                orig = proxies.load_original(event_id, photo_id)
                if orig is None:
                    fallback = True
                else:
                    img = images.resize_long_edge(orig, int(section("proxy")["long_edge"]))
            if fallback:
                r = dict(chain[-1])
                r["variant_fallback"] = True
                res["variant_fallback"] = True
                tm["original"] = tm.get("original", 0.0) + _ms(t0)
                chain.append(r)
                break
            r = score_sharpness_image(img, roi=step["roi"], face_boxes=_ratio_boxes(faces, full_wh),
                                      brisque_value=brisque if step["variant"] == "processed" else None, timings=tm)
            r["variant"], r["variant_fallback"] = step["variant"], False
            if step["variant"] == "original":
                tm["original"] = tm.get("original", 0.0) + _ms(t0)
            res[f"sharpness_{step['roi']}_{step['variant']}"] = r
            if r["roi_used"] != step["roi"]:
                break  # ROI unusable (too small) -> keep the previous result
            chain.append(r)
        res["sharpness"] = policies.final_sharpness(chain)
        res["sharpness_chain"] = [f"{c.get('roi_used')}:{c.get('variant')}" for c in chain]

        # P3: eyes + face quality (groups only unless configured), skip dark scenes with small faces
        eyes_fn = _face_tool("eyes", "score_eyes_open")
        fq_fn = _face_tool("face_quality", "score_face_quality")
        res["eyes_available"] = eyes_fn is not None
        want_eyes = in_group or bool(wcfg.get("singleton_eyes"))
        if want_eyes and faces and full_img is not None:
            if eyes_fn is not None and policies.should_run_eyes(exposure["mean_L"], faces):
                t0 = time.perf_counter()
                try:
                    res["eyes"] = eyes_fn(full_img, faces, event_id=event_id, photo_id=photo_id)
                except Exception as e:  # noqa: BLE001
                    res.setdefault("errors", []).append(f"score_eyes_open: {type(e).__name__}: {e}"[:200])
                tm["eyes"] = _ms(t0)
            if fq_fn is not None:
                t0 = time.perf_counter()
                try:
                    res["face_quality"] = fq_fn(full_img, faces, event_id=event_id, photo_id=photo_id)
                except Exception as e:  # noqa: BLE001
                    res.setdefault("errors", []).append(f"score_face_quality: {type(e).__name__}: {e}"[:200])
                tm["face_quality"] = _ms(t0)
    except Exception as e:  # noqa: BLE001
        res["ok"] = False
        res["error"] = f"{type(e).__name__}: {e}"[:300]
    res["timings"] = tm
    res["io"] = io_delta()
    return res



_last_io: dict[str, int] = {}


def io_delta() -> dict[str, int]:
    """S3 call counts since the previous call in this process (attached to every task result)."""
    global _last_io
    cur = proxies.io_counts()
    d = {k: v - _last_io.get(k, 0) for k, v in cur.items() if v - _last_io.get(k, 0)}
    _last_io = cur
    return d

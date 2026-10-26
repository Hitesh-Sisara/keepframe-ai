"""Real OpenCV 5 tool backend (``CULL_TOOLS=real``): core and face tool functions on real images.

Images come from keepframe.io.proxies (S3 work bucket with a disk cache, or LOCAL_MODE). Face boxes for the
faces-ROI sharpness come from the agent's own recorded detect_faces result (ratios of the processed image),
so the agent controls exactly which evidence feeds which tool.
"""

from __future__ import annotations

import time
from typing import Any

from keepframe.config import section
from keepframe.io import images, proxies
from keepframe.tools.best import rank_group


class RealTools:
    flavor = "real"

    def __init__(self, event_id: str) -> None:
        self.event_id = event_id
        self._ingested = False

    def ensure_ingested(self) -> dict[str, Any]:
        """Register manifest keys via the shared ingest (needed for key/extension resolution).

        EXIF is NOT bulk-loaded here (661 sidecar GETs took ~160 s); strands_agent.load_work attaches EXIF
        facts for the agent's small work set only."""
        from keepframe.tools.ingest import get_context, ingest_event

        if not self._ingested:
            res = ingest_event(self.event_id, load_exif=False)
            self._ingested = True
            self._ctx = get_context(self.event_id, create=False)
            return res
        return {}

    def _info(self, photo_id: str) -> Any:
        self.ensure_ingested()
        return self._ctx.photos.get(photo_id)

    def score_sharpness(self, photo_id: str, roi: str, variant: str,
                        face_boxes: list[list[float]] | None = None) -> dict[str, Any]:
        from keepframe.tools.sharpness import score_sharpness_image

        self.ensure_ingested()
        fallback = False
        img = None
        if variant == "original":
            orig = proxies.load_original(self.event_id, photo_id)
            if orig is None:
                fallback = True
            else:
                img = images.resize_long_edge(orig, int(section("proxy")["long_edge"]))
        if img is None:
            img = proxies.load_p1024(self.event_id, photo_id)
        res = score_sharpness_image(img, roi=roi, face_boxes=face_boxes if roi == "faces" else None)
        res["variant"] = "processed" if fallback else variant
        res["variant_fallback"] = fallback
        res.pop("votes", None)
        return res

    def score_exposure(self, photo_id: str) -> dict[str, Any]:
        from keepframe.tools.exposure import score_exposure_image

        self.ensure_ingested()
        return score_exposure_image(proxies.load_p1024(self.event_id, photo_id))

    def detect_faces(self, photo_id: str) -> dict[str, Any]:
        from keepframe.tools.faces import detect_faces

        info = self._info(photo_id)
        img = proxies.load_full(self.event_id, photo_id)
        res = detect_faces(img, event_id=self.event_id, photo_id=photo_id, record=info.record if info else None)
        res["image_w"], res["image_h"] = int(img.shape[1]), int(img.shape[0])
        return res

    def score_eyes_open(self, photo_id: str, faces: dict[str, Any]) -> dict[str, Any]:
        from keepframe.tools.eyes import score_eyes_open

        img = proxies.load_full(self.event_id, photo_id)
        return score_eyes_open(img, faces, event_id=self.event_id, photo_id=photo_id)

    def score_face_quality(self, photo_id: str, faces: dict[str, Any]) -> dict[str, Any]:
        from keepframe.tools.face_quality import score_face_quality

        img = proxies.load_full(self.event_id, photo_id)
        return score_face_quality(img, faces, event_id=self.event_id, photo_id=photo_id)

    def pick_best_in_cluster(self, group_id: str, results: dict[str, dict[str, Any]],
                             weights: dict[str, float] | None = None) -> dict[str, Any]:
        return rank_group(results, weights)

    def score_composition(self, photo_id: str) -> dict[str, Any]:
        from keepframe.tools.composition import score_composition

        return score_composition(proxies.load_full(self.event_id, photo_id))

    def enhance_keeper(self, event_id: str, photo_id: str, ops: list[str] | None) -> dict[str, Any]:
        from keepframe.tools.enhance import enhance_keeper

        from keepframe.io.exif import load_exif

        ex = load_exif(event_id, photo_id)
        iso = ex.iso if ex else None
        store = proxies.get_store()
        return enhance_keeper(proxies.load_full(event_id, photo_id), ops, event_id=event_id, photo_id=photo_id,
                              iso=iso, writer=lambda k, b: store.put_bytes(k, b, "image/jpeg"))

    def make_contact_sheet(self, event_id: str, group_id: str, photo_ids: list[str],
                           captions: dict[str, str], best_photo_id: str | None) -> dict[str, Any]:
        from keepframe.tools.sheet import make_contact_sheet

        self.ensure_ingested()
        return make_contact_sheet(group_id, event_id=event_id, photo_ids=photo_ids, captions=captions,
                                  best_photo_id=best_photo_id)

    def exif_facts(self, photo_id: str) -> dict[str, Any]:
        from keepframe.io.exif import load_exif
        from keepframe.worker import policies as wp

        info = self._info(photo_id)
        ex = load_exif(self.event_id, photo_id)
        return {"exposure_time_s": ex.exposure_time_s if ex else None,
                "hour": wp.local_hour(info.ts if info else None, ex.local_hour if ex and ex.tz_known else None)}


def timed(fn: Any, *a: Any, **kw: Any) -> tuple[Any, float]:
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    return out, round((time.perf_counter() - t0) * 1e3, 2)

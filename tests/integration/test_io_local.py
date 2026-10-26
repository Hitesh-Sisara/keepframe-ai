"""io: images helpers, LOCAL_MODE store + proxies cache, EXIF sidecar parsing, tool entrypoints."""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from keepframe.io import images
from keepframe.io._s3_stub import LocalStore
from keepframe.io.exif import parse_exif
from keepframe.worker.synth import scene


def test_images_roundtrip_and_resize():
    img = scene(1, 600, 900)
    for ext in (".jpg", ".webp", ".png"):
        out = images.decode(images.encode(img, ext, 90 if ext != ".png" else 3))
        assert out is not None and out.shape == img.shape
    r = images.resize_long_edge(img, 450)
    assert max(r.shape[:2]) == 450 and r.shape[1] == 450
    assert images.resize_long_edge(img, 2000) is img  # never upscale by default
    assert images.decode(b"not an image") is None
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32
    assert images.is_heic(heic) and images.decode(heic) is None
    assert images.is_heic(key="x/IMG_1.HEIC")
    with pytest.raises(ValueError):
        images.encode(img, ".tiff")


def test_local_store_layout_and_escape(tmp_path):
    st = LocalStore(tmp_path)
    st.put_bytes("results/e/x.json", b"{}")
    assert st.exists("results/e/x.json") and st.get_bytes("results/e/x.json") == b"{}"
    assert st.list("results/e/") == ["results/e/x.json"]
    with pytest.raises(ValueError):
        st.get_bytes("../../etc/passwd")
    with pytest.raises(FileNotFoundError):
        st.get_bytes("nope")


def test_proxies_and_tools_local_mode(synth_event):
    from keepframe.io import proxies
    from keepframe.tools.best import pick_best_in_cluster
    from keepframe.tools.cluster import cluster_near_duplicates
    from keepframe.tools.exposure import score_exposure
    from keepframe.tools.hashes import compute_hashes
    from keepframe.tools.ingest import ingest_event
    from keepframe.tools.sharpness import score_sharpness
    from keepframe.tools.sheet import make_contact_sheet

    local_env = synth_event("ev_io")
    assert proxies.local_mode()
    ing = ingest_event("ev_io")
    assert ing["n_photos"] == 12 and ing["cameras"] == ["SYNTH-CAM"] and ing["n_with_original"] == 1
    assert ing["time_span"]["seconds"] == pytest.approx(180.0, abs=0.01)

    full = proxies.load_full("ev_io", "a1")
    p = proxies.load_p1024("ev_io", "a1")
    assert full.shape[:2] == (1066, 1600) and p.shape[:2] == (682, 1024)
    assert (local_env / "proxy/ev_io/a1.jpg").is_file()
    assert not p.flags.writeable
    proxies.reset_store()  # cold memory, warm disk -> identical pixels (proxy is always the decoded JPEG)
    assert np.array_equal(proxies.load_p1024("ev_io", "a1"), p)
    assert proxies.load_p1024_gray("ev_io", "a1").ndim == 2
    assert proxies.load_original("ev_io", "a1") is None
    assert proxies.load_original("ev_io", "s_blur") is not None

    ingest_event("ev_io")
    h = compute_hashes("ev_io")
    assert h["computed"] == 12 and h["cached"] == 0 and not h["failures"]
    assert compute_hashes("ev_io")["cached"] == 12
    cl = cluster_near_duplicates("ev_io")
    assert cl["n_groups"] == 2

    s = score_sharpness("s_blur", roi="full", variant="original")
    assert s["variant"] == "original" and not s["variant_fallback"] and s["label"] == "blurry"
    s2 = score_sharpness("s_sharp", roi="full", variant="original")
    assert s2["variant_fallback"] and s2["variant"] == "processed"
    assert score_exposure("s_dark")["label"] == "under"
    for pid in cl["groups"][0]["photo_ids"]:
        score_sharpness(pid)
        score_exposure(pid)
    best = pick_best_in_cluster(cl["groups"][0]["group_id"])
    assert best["best_photo_id"] in cl["groups"][0]["photo_ids"] and best["ranking"][0]["score"] >= best["ranking"][-1]["score"]
    sheet = make_contact_sheet(cl["groups"][0]["group_id"], best_photo_id=best["best_photo_id"])
    img = cv2.imread(str(local_env / sheet["key"]))
    assert img is not None and img.shape[:2] == (sheet["height"], sheet["width"]) and sheet["n_tiles"] == 4


def test_heic_original_falls_back(synth_event):
    from keepframe.io import proxies
    from keepframe.tools.ingest import ingest_event
    from keepframe.tools.sharpness import score_sharpness

    local_env = synth_event("ev_h")
    (local_env / "originals/ev_h/s_blur.jpg").rename(local_env / "originals/ev_h/s_blur.heic")
    rows = [json.loads(x) for x in (local_env / "manifests/ev_h.jsonl").read_text().splitlines()]
    for r in rows:
        if r["photo_id"] == "s_blur":
            r["original_key"] = "ev_h/s_blur.heic"
    (local_env / "manifests/ev_h.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    ingest_event("ev_h")
    assert proxies.load_original("ev_h", "s_blur") is None and proxies.original_is_heic("ev_h", "s_blur")
    r = score_sharpness("s_blur", variant="original")
    assert r["variant_fallback"] is True


def test_exif_generic_schemas():
    a = parse_exif({"EXIF": {"ExposureTime": "1/30", "ISO": 3200, "DateTimeOriginal": "2026:10:05 21:14:03",
                             "OffsetTimeOriginal": "+05:30", "SubSecTimeOriginal": "45"}})
    assert a.exposure_time_s == pytest.approx(1 / 30) and a.iso == 3200 and a.subsec == pytest.approx(0.45)
    assert a.local_hour == 21 and a.tz_known and a.ts is not None and a.ts % 1 == pytest.approx(0.45)
    b = parse_exif([{"SourceFile": "x.jpg", "EXIF:ExposureTime": 0.004, "EXIF:ISOSpeedRatings": "ISO 400",
                     "ExifIFD:DateTimeOriginal": "2026-10-05T09:00:00Z", "FNumber": "f/2.8"}])
    assert b.exposure_time_s == 0.004 and b.iso == 400 and b.f_number == 2.8 and b.local_hour == 9
    c = parse_exif({"tags": {"exposure_time": {"value": "2"}, "PhotographicSensitivity": [100]}})
    assert c.exposure_time_s == 2.0 and c.iso == 100 and c.ts is None
    assert parse_exif({}).exposure_time_s is None


def test_exif_small_sidecar_prod_shape():
    """Small sidecar, $SOURCE_BUCKET_EXIF_SMALL (docs/DATA.md §4): flat, float ExposureTime, no TZ, no sub-second."""
    doc = {"DateTimeOriginal": "2026:04:12 19:42:07", "DateTime": "2026:04:12 19:42:07", "ExposureTime": 0.0025,
           "ISOSpeedRatings": 1600, "FNumber": 2.8, "FocalLength": 85, "Flash": 16, "Orientation": 1,
           "Make": "SYNTH", "Model": "CAM-1", "LensModel": "85mm"}
    e = parse_exif(doc)
    assert e.schema_kind == "small" and e.exposure_time_s == 0.0025 and e.iso == 1600 and e.f_number == 2.8
    assert e.subsec is None and not e.tz_known and e.local_hour == 19


def test_exif_rich_sidecar_prod_shape():
    """Rich sidecar, $SOURCE_BUCKET_EXIF_RICH: nested; SubSecCreateDate is the best timestamp; string ExposureTime."""
    doc = {"basicExif": {"SubSecTimeOriginal": "37", "ISOSpeedRatings": 400},
           "exifToolData": {"SubSecCreateDate": "2026:03:08 18:05:11.37+05:30", "SubSecTimeOriginal": 37,
                            "CreateDate": "2026:03:08 18:05:11", "ExposureTime": "1/160", "ISO": 400,
                            "FNumber": 1.8, "SerialNumber": "000000000"},
           "cameraInfo": {"ExposureTime": "1/160", "ISO": 400},
           "processingInfo": {"Exposure2012": "+0.35", "ISO": 99999}}
    e = parse_exif(doc)
    assert e.schema_kind == "rich" and e.exposure_time_s == pytest.approx(1 / 160) and e.iso == 400
    assert e.subsec == pytest.approx(0.37) and e.tz_known and e.local_hour == 18
    assert e.ts % 1 == pytest.approx(0.37, abs=1e-6)
    assert "000000000" not in e.model_dump_json()  # serials are never extracted


def test_order_key_filename_within_second():
    from keepframe.schemas import PhotoRecord
    from keepframe.tools.ingest import PhotoInfo, natural_key

    assert natural_key("IMG_0099.jpg") < natural_key("IMG_0100.jpg") < natural_key("IMG_100a.jpg")
    recs = [PhotoRecord(photo_id=pid, event_id="e", clicked_at="2026-10-05T13:00:00Z", name=name)
            for pid, name in (("z", "DSC_10.jpg"), ("a", "DSC_9.jpg"), ("m", "DSC_11.jpg"))]
    assert [p.photo_id for p in sorted((PhotoInfo(r) for r in recs), key=lambda p: p.order_key)] == ["a", "z", "m"]


def test_known_face_count_prefers_frs():
    from keepframe.schemas import PhotoRecord

    def rec(**kw):
        return PhotoRecord(photo_id="p", event_id="e", **kw)

    assert rec(frs_status="indexed", frs_faces=[{"bbox": [0.1, 0.1, 0.1, 0.1]}] * 2).known_face_count() == 2
    assert rec(frs_status="no_faces").known_face_count() == 0
    assert rec(mfaces=None).known_face_count() is None  # real data: mfaces always null
    assert rec(exif_bucket="src-exif-small").model_dump()["exif_bucket"] == "src-exif-small"


def test_agent_path_faces_roi_from_context(synth_event):
    """Agent records detect_faces in the context; score_sharpness(roi='faces') uses those boxes."""
    from keepframe.io import proxies
    from keepframe.tools.ingest import get_context, ingest_event
    from keepframe.tools.sharpness import score_sharpness

    synth_event("ev_ag")
    ingest_event("ev_ag")
    full = proxies.load_full("ev_ag", "a1")
    h, w = full.shape[:2]
    get_context("ev_ag").record("a1", "detect_faces", {"faces": [{"face_id": "f0", "bbox_px": [w * 0.3, h * 0.3, w * 0.3,
                                                                                                h * 0.3]}]})
    r = score_sharpness("a1", roi="faces")
    assert r["roi_used"] == "faces"
    x, y, rw, rh = r["roi_rect"]
    assert abs(x - 0.27 * 1024) < 3 and abs(rw - 0.36 * 1024) < 4  # 20 % expansion, in proxy pixels
    assert get_context("ev_ag").results["a1"]["sharpness_faces_processed"]["lap_var"] == r["lap_var"]


def test_exif_index_reused(synth_event):
    from keepframe.tools import ingest as ing

    root = synth_event("ev_x")
    ing.ingest_event("ev_x")
    idx = root / "results/ev_x/exif_index.json"
    assert idx.is_file()
    doc = json.loads(idx.read_text())
    assert len(doc["photos"]) == 12 and "SerialNumber" not in idx.read_text()
    for p in (root / "exif/ev_x").iterdir():
        p.unlink()  # sidecars gone: the index alone must supply EXIF
    ing.reset_contexts()
    ing.ingest_event("ev_x")
    ctx = ing.get_context("ev_x", create=False)
    assert all(ctx.photos[p].exif is None for p in ctx.order)  # exif/ LIST is empty -> nothing wanted
    (root / "exif/ev_x").rmdir()
    synth_event("ev_x")  # restore sidecars
    ing.reset_contexts()
    ing.ingest_event("ev_x")
    assert all(ing.get_context("ev_x", create=False).photos[p].exif is not None for p in ing.get_context("ev_x").order)

"""End-to-end LOCAL_MODE run on a 12-image synthetic event (integration test)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _install_fake_s3(monkeypatch, fake) -> None:
    """Replace keepframe.io.s3 both in sys.modules and as the package attribute (a unit test may have
    imported the real module already)."""
    import keepframe.io as io_pkg

    monkeypatch.setitem(sys.modules, "keepframe.io.s3", fake)
    monkeypatch.setattr(io_pkg, "s3", fake, raising=False)


def _read(root: Path, ev: str):
    res = root / "results" / ev
    decisions = {d["photo_id"]: d for d in map(json.loads, (res / "decisions.jsonl").read_text().splitlines())}
    return decisions, json.loads((res / "groups.json").read_text()), json.loads((res / "summary.json").read_text())


@pytest.fixture()
def event(synth_event):
    return synth_event("ev_t")


def test_pipeline_end_to_end(event):
    from keepframe.worker.run_event import run_event

    s = run_event("ev_t", local_root=str(event), workers=1)
    d, g, summ = _read(event, "ev_t")
    assert len(d) == 12 and summ["n_photos"] == 12

    # groups: burst A (4) and burst B (3), everything else singleton
    members = sorted(tuple(x["members"]) for x in g["groups"])
    assert members == [("a1", "a2", "a3", "a4"), ("b1", "b2", "b3")]
    assert g["exact_duplicates"] == {"b2": "b1"}

    # P4: every group keeps exactly one frame
    for grp in g["groups"]:
        keeps = [p for p in grp["members"] if d[p]["verdict"] == "keep"]
        assert keeps == [grp["best_photo_id"]]
        assert d[grp["best_photo_id"]]["reason_codes"] == ["KEEP_BEST_OF_GROUP"]

    assert d["b2"]["verdict"] == "reject" and d["b2"]["reason_codes"][0] == "DUPLICATE_OF:b1"
    assert d["a3"]["verdict"] == "reject" and "BLUR_FOCUS" in d["a3"]["reason_codes"]
    assert "BURST_NOT_BEST" in d["a3"]["reason_codes"]
    assert d["b3"]["verdict"] == "reject"
    assert d["s_sharp"]["verdict"] == "keep" and d["s_sharp"]["reason_codes"] == ["KEEP_SINGLETON"]
    assert "BLUR_FOCUS" in d["s_blur"]["reason_codes"] and d["s_blur"]["verdict"] in ("reject", "review")
    assert "EXPOSURE_UNDER" in d["s_dark"]["reason_codes"]
    assert d["s_over"]["reason_codes"][0] == "EXPOSURE_OVER" and d["s_over"]["verdict"] == "reject"
    assert d["s_flat"]["reason_codes"][0] == "NO_SUBJECT"

    # explain every verdict; never delete
    for x in d.values():
        assert x["verdict"] in ("keep", "reject", "review") and x["reason_codes"] and 0 < len(x["reason_text"]) <= 140
    # low confidence rejects go to review (P5)
    for x in d.values():
        if x["verdict"] == "reject":
            assert x["confidence"] >= 0.8

    # summary carries the reproducibility header
    assert summ["opencv_version"].startswith("5.") and summ["cv2_file"].endswith((".py", ".so"))
    assert len(summ["thresholds_sha"]) == 64 and summ["flavor"] == "stock" and summ["instance_type"]
    assert sum(summ["verdict_counts"].values()) == 12
    for stage in ("ingest", "proxies_hashes", "cluster", "analyse", "decide", "decode_full", "hashes", "exposure",
                  "sharpness_metrics"):
        assert stage in summ["stages"], stage
    assert s["run_id"] == summ["run_id"]

    # proxies written in the S3 layout, approval sheets for escalated groups
    assert (event / "proxy" / "ev_t" / "a1.jpg").is_file()
    for grp in g["groups"]:
        if grp["needs_approval"]:
            assert (event / grp["contact_sheet_key"]).is_file()


def test_deterministic_rerun_and_parallel_matches_inline(event, tmp_path, monkeypatch):
    """Robustness metric (docs/EVALUATION.md §2): same event twice -> identical verdicts; pool == inline."""
    from keepframe.io import proxies
    from keepframe.worker.run_event import run_event

    run_event("ev_t", local_root=str(event), workers=1, run_id="r1")
    d1, g1, _ = _read(event, "ev_t")
    # cold cache second time, pooled workers
    monkeypatch.setenv("CULL_CACHE_DIR", str(tmp_path / "cache2"))
    proxies.reset_store()
    run_event("ev_t", local_root=str(event), workers=3, run_id="r2")
    d2, g2, s2 = _read(event, "ev_t")
    strip = lambda d: {k: {f: v for f, v in x.items() if f != "run_id"} for k, x in d.items()}  # noqa: E731
    assert strip(d1) == strip(d2)
    assert [x["members"] for x in g1["groups"]] == [x["members"] for x in g2["groups"]]
    assert s2["workers"] == 3

    # each run keeps its own evidence under results/<event>/<run_id>/; the unversioned copy is the latest run
    for rid in ("r1", "r2"):
        res = event / "results" / "ev_t" / rid
        assert {p.name for p in res.iterdir()} == {"decisions.jsonl", "groups.json", "summary.json", "hashes.json"}
        summ = json.loads((res / "summary.json").read_text())
        assert summ["run_id"] == rid and summ["results_run_prefix"] == f"results/ev_t/{rid}/"
        assert json.loads((res / "groups.json").read_text())["run_id"] == rid
        assert {json.loads(x)["run_id"] for x in (res / "decisions.jsonl").read_text().splitlines()} == {rid}
    latest = event / "results" / "ev_t"
    for name in ("decisions.jsonl", "groups.json", "summary.json", "hashes.json"):
        assert (latest / name).read_bytes() == (latest / "r2" / name).read_bytes(), name


def test_cli_max_photos(event):
    out = subprocess.run(
        [sys.executable, "-m", "keepframe.worker.run_event", "--event-id", "ev_t", "--local-root", str(event),
         "--max-photos", "5", "--workers", "1", "--no-sheets"],
        capture_output=True, text=True, check=True,
        env={**__import__("os").environ, "KEEPFRAME_IMPORT_LOG": "1"},
    )
    summary = json.loads(out.stdout.strip().splitlines()[-1])
    assert summary["n_photos"] == 5 and summary["status"] == "ok"
    logs = [json.loads(line) for line in out.stderr.splitlines() if line.startswith("{")]
    build = next(x for x in logs if x["event"] == "opencv_build")
    assert build["opencv_version"].startswith("5.") and build["build_info_head"]
    assert any(x["event"] == "stage" and x["stage"] == "cluster" for x in logs)


def test_missing_raw_fails_loudly_unless_allowed(event):
    from keepframe.worker.run_event import MissingRawError, run_event

    (event / "raw" / "ev_t" / "s_sharp.webp").unlink()
    with pytest.raises(MissingRawError, match="1 of 12"):
        run_event("ev_t", local_root=str(event), workers=1)
    assert not (event / "results" / "ev_t" / "decisions.jsonl").exists()
    s = run_event("ev_t", local_root=str(event), workers=1, allow_missing_raw=True)
    d, _, _ = _read(event, "ev_t")
    assert d["s_sharp"]["verdict"] == "review" and d["s_sharp"]["confidence"] == 0.0
    assert s["status"] == "partial"


def test_with_face_tools(event, monkeypatch):
    """face tools present: the worker calls them with main's signatures and P3 applies."""
    import types

    from keepframe.worker import stages
    from keepframe.worker.run_event import run_event

    calls: dict[str, list] = {"detect": [], "eyes": [], "fq": []}

    def detect_faces(img=None, *, event_id=None, photo_id=None, record=None, min_score=None, reuse_existing=True):
        calls["detect"].append(photo_id)
        h, w = img.shape[:2]
        return {"faces": [{"face_id": "f0", "bbox_px": [w * 0.4, h * 0.3, w * 0.2, h * 0.3], "score": 0.95,
                           "landmarks5": [[0, 0]] * 5, "source": "yunet"}], "n_faces": 1}

    def score_eyes_open(img=None, faces=None, face_ids=None, *, event_id=None, photo_id=None):
        calls["eyes"].append(photo_id)
        p = 0.05 if photo_id == "a1" else 0.95
        return {"per_face": [{"face_id": "f0", "left_open_p": p, "right_open_p": p,
                              "state": "closed" if p < 0.3 else "open", "confidence": 0.9, "min_px": 200,
                              "is_main": True}],
                "any_main_subject_closed": p < 0.3, "main_subject_face_id": "f0", "confidence": 0.9}

    def score_face_quality(img=None, faces=None, face_ids=None, *, event_id=None, photo_id=None, identity=False):
        calls["fq"].append(photo_id)
        return {"per_face": [{"face_id": "f0", "fiqa": 0.7, "lap_var": 50.0, "minPx": 200, "good": True}],
                "main_subject_face_id": "f0"}

    mods = {"faces": detect_faces, "eyes": score_eyes_open, "face_quality": score_face_quality}
    for name, fn in mods.items():
        m = types.ModuleType(f"keepframe.tools.{name}")
        setattr(m, fn.__name__, fn)
        monkeypatch.setitem(sys.modules, m.__name__, m)
    # synthetic photos claim mfaces=0; pretend people are present
    for line_file in (event / "manifests").glob("*.jsonl"):
        rows = [json.loads(x) for x in line_file.read_text().splitlines()]
        for r in rows:
            r["mfaces"] = None
        line_file.write_text("".join(json.dumps(r) + "\n" for r in rows))
    stages._face_tool.cache_clear()
    monkeypatch.setattr(stages.importlib.util, "find_spec", lambda name: object())
    try:
        s = run_event("ev_t", local_root=str(event), workers=1)
    finally:
        stages._face_tool.cache_clear()
    d, g, _ = _read(event, "ev_t")
    assert s["faces_available"]
    assert sorted(calls["detect"]) == ["a1", "a2", "a3", "a4", "b1", "b2", "b3"]  # groups only (P1)
    assert set(calls["eyes"]) <= set(calls["detect"]) and calls["fq"]
    # a1 (main subject eyes closed, p=0.95) is rejected with EYES_CLOSED and is not the keeper
    assert d["a1"]["verdict"] == "reject" and "EYES_CLOSED" in d["a1"]["reason_codes"]
    ga = next(x for x in g["groups"] if "a1" in x["members"])
    assert ga["best_photo_id"] != "a1"
    assert d["a1"]["scores"]["faces_source"] == "detector"


def test_s3_mode_counts_and_background_uploads(event, tmp_path, monkeypatch):
    """WORK_BUCKET set: a fake keepframe.io.s3 over a directory. One LIST per prefix, no per-photo HEADs,
    proxies uploaded once, counters in summary.json; a warm second run downloads proxies, not raw images."""
    import os
    import types

    from keepframe.io import proxies
    from keepframe.io._s3_stub import LocalStore
    from keepframe.worker.run_event import run_event

    backing = LocalStore(event)
    fake = types.ModuleType("keepframe.io.s3")
    fake.get_bytes = backing.get_bytes
    fake.put_bytes = backing.put_bytes
    fake.exists = backing.exists
    fake.list = backing.list
    _install_fake_s3(monkeypatch, fake)
    monkeypatch.setenv("WORK_BUCKET", "fake-bucket")
    monkeypatch.setenv("CULL_DISABLE_DDB", "1")  # never touch DynamoDB / AWS credentials from tests
    monkeypatch.setenv("CULL_CACHE_DIR", str(tmp_path / "c1"))
    proxies.reset_store()
    s = run_event("ev_t", workers=1)  # the fake s3 module exists only in this process (pool workers re-import)
    assert s["status"] == "ok", s["failures"][:2]
    assert s["s3_put_count"] >= 12 + 3  # 12 proxies + hashes/decisions/groups (+ sheets)
    assert s["s3_head_count"] == 0  # LIST + known keys instead of per-photo HEAD
    assert s["s3_list_count"] <= 5  # raw/, proxy/, originals/, exif/ (+1 slack)
    assert s["s3_get_count"] >= 12 + 12 + 1  # raw + exif + manifest (the manifest is read through the store)
    assert len([k for k in backing.list("proxy/ev_t/")]) == 12
    d1, _, _ = _read(event, "ev_t")

    monkeypatch.setenv("CULL_CACHE_DIR", str(tmp_path / "c2"))  # cold disk cache, warm bucket
    proxies.reset_store()
    s2 = run_event("ev_t", workers=1)
    d2, _, _ = _read(event, "ev_t")
    assert {k: v["verdict"] for k, v in d1.items()} == {k: v["verdict"] for k, v in d2.items()}
    assert s2["s3_put_count"] < s["s3_put_count"]  # proxies reused, not re-uploaded
    assert os.path.isdir(tmp_path / "c2" / "proxy" / "ev_t")
    assert not (tmp_path / "c2" / "raw").exists() or not any((tmp_path / "c2" / "raw").rglob("a1.*"))


def test_twenty_frame_burst_end_to_end(local_env):
    """A studio-style 20-frame near-identical burst: 1 keep, at most 1 human question, the rest rejected."""
    import hashlib

    import cv2

    from keepframe.worker.run_event import run_event
    from keepframe.worker.synth import jitter, scene

    ev = "ev_burst"
    base = scene(777, 1066, 1600)
    rows = []
    for i in range(20):
        img = jitter(base, 100 + i, 2.0)
        data = bytes(cv2.imencode(".webp", img, [cv2.IMWRITE_WEBP_QUALITY, 85])[1])
        p = local_env / f"raw/{ev}/f{i:02d}.webp"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        rows.append({"photo_id": f"f{i:02d}", "event_id": ev, "name": f"IMG_{i:04d}.jpg", "camera": "C",
                     "clicked_at": f"2026-10-05T13:00:{i // 4:02d}Z", "width": 1600, "height": 1066,
                     "md5": hashlib.md5(data).hexdigest(), "frs_status": "no_faces"})  # noqa: S324
    (local_env / "manifests").mkdir(parents=True, exist_ok=True)
    (local_env / f"manifests/{ev}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    s = run_event(ev, local_root=str(local_env), workers=1, sheets=False)
    d, g, _ = _read(local_env, ev)
    assert s["n_groups"] == 1 and g["groups"][0]["size"] == 20
    v = [x["verdict"] for x in d.values()]
    assert v.count("keep") == 1 and v.count("reject") >= 17
    assert s["escalation_count"] <= 1 and v.count("review") <= 2
    assert len(g["groups"][0]["tied_candidates"]) <= 3


def test_manifest_original_absent_in_store_does_not_fail(event, tmp_path, monkeypatch):
    """Regression (E07): manifest has original_key, sync copied no originals, the S3 module raises botocore-style
    NoSuchKey, proxies are pre-cached in the store, borderline group members ask for the original. Must fall back
    to the processed image (variant_fallback), never fail the photo."""
    import types

    from keepframe.io import proxies
    from keepframe.io._s3_stub import LocalStore
    from keepframe.worker.run_event import run_event

    # 1st run builds the proxies in the store (pre-cached proxies, like the second deployed E07 run)
    run_event("ev_t", local_root=str(event), workers=1)
    for p in (event / "originals" / "ev_t").iterdir():
        p.unlink()
    rows = [json.loads(x) for x in (event / "manifests/ev_t.jsonl").read_text().splitlines()]
    for r in rows:  # every photo claims an original, as in the real manifests
        r["original_key"] = f"ev_t/{r['photo_id']}.jpg"
    (event / "manifests/ev_t.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    class NoSuchKey(Exception):
        def __init__(self, key):
            super().__init__("An error occurred (NoSuchKey) when calling the GetObject operation")
            self.response = {"Error": {"Code": "NoSuchKey"}}

    backing = LocalStore(event)

    def get_bytes(key):
        try:
            return backing.get_bytes(key)
        except FileNotFoundError:
            raise NoSuchKey(key) from None

    fake = types.ModuleType("keepframe.io.s3")
    fake.get_bytes, fake.put_bytes, fake.exists, fake.list = get_bytes, backing.put_bytes, backing.exists, backing.list
    _install_fake_s3(monkeypatch, fake)
    monkeypatch.setenv("WORK_BUCKET", "fake-bucket")
    monkeypatch.setenv("CULL_DISABLE_DDB", "1")
    monkeypatch.setenv("CULL_CACHE_DIR", str(tmp_path / "c3"))
    proxies.reset_store()
    s = run_event("ev_t", workers=1)
    assert s["status"] == "ok" and s["failures"] == []
    # direct API: absent original -> None, not an exception
    assert proxies.load_original("ev_t", "s_blur") is None
    # without the LIST (agent path), a NoSuchKey from the store is still treated as absent
    proxies.set_present_originals(None)
    assert proxies.load_original("ev_t", "s_blur") is None

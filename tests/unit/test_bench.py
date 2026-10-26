"""run_benchmark + summarize end to end on a few synthetic images (models fetched once, sha-checked)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
NAMES = ("face_detection_yunet_2023mar.onnx", "brisque_model_live.yml", "brisque_range_live.yml")


@pytest.fixture(scope="module")
def models_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Private copy of the 3 pinned models, so this test never writes (or races on) a shared models dir.

    Sources, first hit wins: $CULL_MODELS_DIR, /tmp/models, <repo>/models. run_benchmark re-checks every
    sha256 and downloads only what is missing, into this private dir.
    """
    import os
    import shutil

    d = tmp_path_factory.mktemp("models")
    for src in (os.environ.get("CULL_MODELS_DIR"), "/tmp/models", str(ROOT / "models")):
        if not src:
            continue
        for n in NAMES:
            if (Path(src) / n).exists() and not (d / n).exists():
                shutil.copyfile(Path(src) / n, d / n)
    return d


@pytest.fixture(scope="module")
def tiny_set(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    d = tmp_path_factory.mktemp("bench")
    rng = np.random.default_rng(7)
    rows = []
    for i in range(6):
        ev = f"ev_t{i % 2}"
        img = cv2.GaussianBlur(rng.integers(0, 256, (400, 600, 3), dtype=np.uint8), (0, 0), 1 + i)
        ok, buf = cv2.imencode(".webp", img, [cv2.IMWRITE_WEBP_QUALITY, 80])
        assert ok
        (d / "imgs" / ev).mkdir(parents=True, exist_ok=True)
        (d / "imgs" / ev / f"t{i}.webp").write_bytes(buf.tobytes())
        rows.append({"photo_id": f"t{i}", "event_id": ev, "processed_key": f"raw/{ev}/t{i}.webp"})
    man = d / "m.jsonl"
    man.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d, man


def test_run_and_summarize(tiny_set: tuple[Path, Path], models_dir: Path) -> None:
    d, man = tiny_set
    out = d / "results"
    env = {k: v for k, v in __import__("os").environ.items() if not k.startswith(("CULL_", "WORK_BUCKET"))}
    cmd = [sys.executable, str(ROOT / "bench" / "run_benchmark.py"), "--host-label", "x86-devbox-not-a-baseline",
           "--manifest", str(man), "--local-dir", str(d / "imgs"), "--workers", "2", "--reps", "2",
           "--warmup", "2", "--models-dir", str(models_dir), "--out-root", str(out), "--date", "2000-01-01",
           "--source", "/nonexistent"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    run = out / "2000-01-01" / "x86-devbox-not-a-baseline"
    res = json.loads((run / "results.json").read_text())
    env = json.loads((run / "env.json").read_text())
    assert res["n_images"] == 6 and res["reps"] == 2 and len(res["reps_detail"]) == 2
    raw_csv = (run / "raw" / "per_image.csv").read_text()
    import csv as _csv
    errs = {row["error"] for row in _csv.DictReader(raw_csv.splitlines()) if row["error"]}
    assert all(rep["errors"] == 0 for rep in res["reps_detail"]), (errs, r.stdout[-1500:], r.stderr[-1500:])
    # the timed pass is far shorter than the 1 Hz sampler period; stop() must still record a sample
    assert all(rep["cpu_util_mean"] is not None and rep["cpu_util_samples"] >= 1 for rep in res["reps_detail"])
    assert all(rep["peak_rss_mb"] > 0 for rep in res["reps_detail"])
    assert set(res["reps_detail"][0]["stages"]) >= {"decode_webp", "yunet_1024", "brisque", "opencv_total"}
    assert len(res["set_sha256"]) == 64
    assert env["cv2_version"] == cv2.__version__ and len(env["cv2_build_information_head"]) == 40
    assert env["has_contrib"]["img_hash"] and env["has_contrib"]["quality"]
    # Custom HAL recorded for every row (carotene / KleidiCV / ARMPL axis, decisions 2026-10-07)
    hal = next((ln.split(":", 1)[1].strip() for ln in cv2.getBuildInformation().splitlines()
                if ln.strip().startswith("Custom HAL:")), None)
    assert env["custom_hal"] == hal and res["custom_hal"] == hal
    assert env["kleidicv"] == bool(hal and "kleidicv" in hal.lower())
    assert (run / "raw" / "per_image.csv").read_text().count("\n") == 1 + 6 * 2

    r = subprocess.run([sys.executable, str(ROOT / "bench" / "summarize.py"), "--root", str(out), "--date",
                        "2000-01-01"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    # devbox labels are excluded from the /bench doc by default; publish explicitly to test the shape
    r = subprocess.run([sys.executable, str(ROOT / "bench" / "summarize.py"), "--root", str(out), "--date",
                        "2000-01-01", "--publish-labels", "x86-devbox-not-a-baseline"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    ui = json.loads((out / "2000-01-01" / "results.json").read_text())
    # published contract 2026-10-05, exact keys and types
    assert {"generated_at", "hosts"} <= set(ui)
    h = ui["hosts"][0]
    contract = {"label": str, "instance_type": str, "opencv_version": str, "cv2_file": str, "images_per_sec": float,
                "cost_per_1k_usd": (float, type(None)), "cpu_util_mean": float, "peak_rss_mb": float, "stages": list}
    for k, t in contract.items():
        assert isinstance(h[k], t), (k, h[k])
    assert h["instance_type"] == "non-ec2" and h["cost_per_1k_usd"] is None
    assert all(set(st) == {"name", "p50_ms", "p95_ms"} for st in h["stages"])
    assert [st["name"] for st in h["stages"]][0] == "decode_webp"
    # aliases read by ui/app/bench/page.tsx (BenchDoc) as merged on main
    assert h["host_label"] == h["label"] and h["images_per_s"] == h["images_per_sec"]
    assert {"stage", "host_label", "p50_ms", "p95_ms"} == set(ui["stages"][0])
    assert ui["n_images"] == 6 and ui["synthetic"] is False
    assert "x86-devbox-not-a-baseline" in (out / "2000-01-01" / "results.md").read_text()


def test_price_math() -> None:
    sys.path.insert(0, str(ROOT / "bench"))
    import run_benchmark as rb

    prices = rb.load_prices(ROOT / "bench" / "prices.yaml")
    assert rb.price_per_hour(prices, "c8g.4xlarge") == {"ec2_usd_per_hour": 0.43184}
    assert rb.price_per_hour(prices, "c8g.xlarge") == {"ec2_usd_per_hour": 0.10796}
    assert rb.price_per_hour(prices, None) == {"ec2_usd_per_hour": None}
    assert "cool" not in (ROOT / "bench" / "prices.yaml").read_text().lower().split("#")[0]
    assert rb.pct([1, 2, 3, 4], 0.5) == 2.5
    assert rb.custom_hal(["  Custom HAL:   YES (carotene (ver 0.0.1) KleidiCV (ver 26.03))"]) == \
        "YES (carotene (ver 0.0.1) KleidiCV (ver 26.03))"
    assert rb.custom_hal(["Other: x"]) is None


def test_monitor_records_short_passes() -> None:
    sys.path.insert(0, str(ROOT / "bench"))
    import run_benchmark as rb

    m = rb.Monitor()
    m.start()
    m.stop()  # well under 1 s
    assert len(m.cpu) == 1 and m.rss_peak > 0


def test_kleidicv_pair_gated_on_opencv_hal() -> None:
    """The KleidiCV ON/OFF table only compares rows whose env.json opencv_hal says carotene vs kleidicv+carotene."""
    sys.path.insert(0, str(ROOT / "bench"))
    import summarize as sm

    def host(label: str, hal: str, p50: float) -> dict:
        return {"label": label, "opencv_hal": hal, "set_sha256": "s", "images_per_sec": 1000 / p50,
                "cxx_compiler": "gcc 14", "stages": [{"name": "opencv_total", "p50_ms": p50}]}

    ok = sm.pair_table([host("src-gcc14-carotene", "carotene", 100.0),
                        host("src-gcc14-kleidicv", "kleidicv-26.03+carotene", 80.0)])
    assert any(ln.startswith("| opencv_total | 1.250x") for ln in ok)
    # mislabelled: the "kleidicv" row's cv2 has no KleidiCV -> no table
    assert sm.pair_table([host("src-gcc14-carotene", "carotene", 100.0),
                          host("src-gcc14-kleidicv", "carotene", 80.0)]) == []

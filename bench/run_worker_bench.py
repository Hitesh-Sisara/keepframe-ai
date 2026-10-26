#!/usr/bin/env python3
"""End-to-end worker benchmark: `keepframe.worker.microbench` + `keepframe.worker.run_event` reps.

Runs the product's deterministic batch worker (no LLM) on one event in LOCAL_MODE, so S3 is excluded
from timing and nothing is written to the shared results/ prefix. The event's manifests/, raw/, exif/
(and originals/ if present) must already be under --local-root in the work-bucket layout
(bench/ec2/launch_bench_host.sh --worker-event syncs them before timing).

Each rep starts cold: proxy/, results/, sheets/ under the local root and $CULL_CACHE_DIR are removed
first, so every rep pays proxy generation exactly like a first run in production. Reports images/s
(worker's own n_ok / wall_s and ours from subprocess wall), CPU % (psutil 1 Hz), peak RSS of the
process tree, $/1k images from bench/prices.yaml, and the worker's per-stage p50/p95.

Writes bench/results/<date>/<label>/worker/{worker.json,microbench.json}; raw per-rep summaries
(contain the event id) go to .../worker/raw/ (gitignored).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_benchmark import Monitor, imds, load_prices, price_per_hour  # noqa: E402


def cv2_hal(py: str) -> str | None:
    """`Custom HAL:` line of the cv2 the worker subprocesses import (same interpreter, same venv)."""
    code = ("import cv2\nfor ln in cv2.getBuildInformation().splitlines():\n"
            "    if ln.strip().startswith('Custom HAL:'): print(ln.split(':', 1)[1].strip())")
    r = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=60)
    return r.stdout.strip() or None


def run_tree(cmd: list[str], env: dict[str, str], log: Path) -> tuple[int, float, Monitor]:
    import psutil

    mon = Monitor()
    with log.open("w") as lf:
        t0 = time.perf_counter()
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=lf, text=True)
        mon.me = psutil.Process(proc.pid)  # sample the worker's tree, not ours
        mon.start()
        out, _ = proc.communicate()
        wall = time.perf_counter() - t0
        mon.stop()
    (log.with_suffix(".stdout")).write_text(out or "")
    return proc.returncode, wall, mon


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host-label", required=True)
    ap.add_argument("--event-id", required=True)
    ap.add_argument("--local-root", type=Path, required=True)
    ap.add_argument("--src", type=Path, required=True, help="dir holding keepframe/ and config/ (main tree)")
    ap.add_argument("--models-dir", type=Path, required=True)
    ap.add_argument("--cache-dir", type=Path, default=Path("/tmp/cull-bench-cache"))
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--microbench-repeats", type=int, default=30)
    ap.add_argument("--prices", type=Path, default=HERE / "prices.yaml")
    ap.add_argument("--out-root", type=Path, default=HERE / "results")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    args = ap.parse_args()

    out = args.out_root / args.date / args.host_label / "worker"
    (out / "raw").mkdir(parents=True, exist_ok=True)
    itype = imds("instance-type")
    env = {**os.environ, "PYTHONPATH": str(args.src), "CULL_MODELS_DIR": str(args.models_dir),
           "CULL_CONFIG_DIR": str(args.src / "config"), "CULL_CACHE_DIR": str(args.cache_dir),
           "OPENCV_FORCE_DNN_ENGINE": os.environ.get("OPENCV_FORCE_DNN_ENGINE", "3")}
    env.pop("WORK_BUCKET", None)
    if itype:
        env["CULL_INSTANCE_TYPE"] = itype
    py = sys.executable

    # 1. microbench (synthetic 2560-px scene, single thread)
    mb_json = out / "microbench.json"
    rc, mb_wall, _ = run_tree([py, "-m", "keepframe.worker.microbench", "--repeats", str(args.microbench_repeats),
                               "--json", str(mb_json)], env, out / "raw" / "microbench.log")
    print(f"microbench rc={rc} in {mb_wall:.1f}s", flush=True)

    # 2. run_event reps, cold each time
    reps = []
    for rep in range(1, args.reps + 1):
        for sub in ("proxy", "results", "sheets", "preview"):
            shutil.rmtree(args.local_root / sub / args.event_id, ignore_errors=True)
        shutil.rmtree(args.cache_dir, ignore_errors=True)
        # --flavor stock: the product's label for "open-source OpenCV from the venv"; which build (carotene /
        # KleidiCV wheel or a source build) is recorded from the venv's cv2 below, not from this flag.
        cmd = [py, "-m", "keepframe.worker.run_event", "--event-id", args.event_id, "--flavor", "stock",
               "--local-root", str(args.local_root), "--workers", str(args.workers),
               "--run-id", f"bench_{args.host_label}_rep{rep}"]
        rc, wall, mon = run_tree(cmd, env, out / "raw" / f"rep{rep}.log")
        summ_p = args.local_root / "results" / args.event_id / "summary.json"
        if rc != 0 or not summ_p.exists():
            print(f"rep {rep} FAILED rc={rc}; see {out / 'raw' / f'rep{rep}.log'}", flush=True)
            reps.append({"rep": rep, "rc": rc, "wall_s_outer": round(wall, 3)})
            continue
        s = json.loads(summ_p.read_text())
        shutil.copy(summ_p, out / "raw" / f"rep{rep}_summary.json")
        n_ok = s["n_photos"] - len(s.get("failures") or [])
        reps.append({
            "rep": rep, "rc": rc, "n_photos": s["n_photos"], "n_ok": n_ok, "n_groups": s["n_groups"],
            "wall_s_worker": s["wall_s"], "wall_s_outer": round(wall, 3),
            "images_per_sec_worker": s.get("images_per_s"),
            "images_per_sec_outer": round(s["n_photos"] / wall, 4),
            "cpu_util_mean": round(statistics.fmean(mon.cpu), 2) if mon.cpu else None,
            "peak_rss_mb": round(mon.rss_peak / 2**20, 1),
            "verdict_counts": s.get("verdict_counts"), "reason_counts": s.get("reason_counts"),
            "escalation_count": s.get("escalation_count"), "faces_available": s.get("faces_available"),
            "thresholds_sha": s.get("thresholds_sha"), "instance_type": s.get("instance_type"),
            "stages": {k: {kk: v.get(kk) for kk in ("n", "p50_ms", "p95_ms", "wall_ms", "total_ms")}
                       for k, v in (s.get("stages") or {}).items()},
            "opencv_version": s.get("opencv_version"), "cv2_file": s.get("cv2_file"),
            "opencv_hal": s.get("opencv_hal"),
        })
        r = reps[-1]
        print(f"rep {rep}: {r['n_photos']} photos in {r['wall_s_worker']}s (worker) / {r['wall_s_outer']}s (outer) "
              f"= {r['images_per_sec_worker']} img/s, cpu {r['cpu_util_mean']}%, rss {r['peak_rss_mb']} MB, "
              f"verdicts {r['verdict_counts']}", flush=True)

    pr = price_per_hour(load_prices(args.prices), itype)
    hourly = pr["ec2_usd_per_hour"]
    for r in reps:
        if "n_photos" in r and hourly:
            r["cost_per_1k_usd"] = round(hourly * (r["wall_s_outer"] / 3600) / (r["n_photos"] / 1000), 6)
    ok = [r for r in reps if r.get("rc") == 0 and "n_photos" in r]

    def med(k: str) -> float | None:
        v = [r[k] for r in ok if r.get(k) is not None]
        return round(statistics.median(v), 6) if v else None

    identical = None
    if len(ok) >= 2:
        decs = []
        for r in ok:
            p = out / "raw" / f"rep{r['rep']}_summary.json"
            decs.append(json.loads(p.read_text()).get("verdict_counts"))
        identical = all(d == decs[0] for d in decs)
    result = {
        "schema": "keepframe/worker-bench/v1",
        "host_label": args.host_label, "date": args.date, "instance_type": itype, "workers": args.workers,
        "custom_hal": cv2_hal(py), "reps": args.reps, "n_photos": ok[0]["n_photos"] if ok else None,
        "event": "661-photo event (id in raw/, not committed)",
        "prices_usd_per_hour": pr,
        "median": {k: med(k) for k in ("images_per_sec_worker", "images_per_sec_outer", "wall_s_worker",
                                       "wall_s_outer", "cpu_util_mean", "peak_rss_mb", "cost_per_1k_usd")},
        "verdict_counts_identical_across_reps": identical,
        "reps_detail": ok,
        "failed_reps": [r for r in reps if r not in ok],
        "method": "keepframe.worker.run_event in LOCAL_MODE (inputs pre-synced to local disk), cold proxy and "
                  "disk caches per rep, ProcessPool of --workers; images/sec_outer = n_photos / subprocess wall "
                  "(includes interpreter start and model load); _worker = the worker's own summary.",
    }
    (out / "worker.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["median"]), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Per-stage OpenCV 5 benchmark for KeepFrame (docs/COOL_BENCHMARK.md §1).

Copies the fixed image set to local disk first (S3 time is excluded), then times every
stage per image with time.perf_counter_ns inside ProcessPoolExecutor workers that each run
cv2.setNumThreads(1). Writes bench/results/<date>/<label>/{results.csv,results.json,env.json}
plus raw/per_image.csv (gitignored: it carries photo ids).

Self-contained on purpose: bench/ec2/launch_bench_host.sh ships this file inside EC2 user-data.
Runtime deps: opencv (any 5.x build: PyPI wheel or a source build), numpy, psutil, pyyaml, boto3 (only for
--source s3). env.json records which Custom HAL (carotene / KleidiCV / ARMPL) the cv2 build carries.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import re
import resource
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# New DNN engine first, classic fallback. Must be set before cv2 is imported.
os.environ.setdefault("OPENCV_FORCE_DNN_ENGINE", "3")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_SOURCE = f"s3://keepframe-work-{os.environ.get('AWS_ACCOUNT_ID', '<AWS_ACCOUNT_ID>')}-ap-south-1/bench/5k/"
KNOWN_LABELS = ("stock-carotene", "stock-kleidicv", "kleidicv-src", "stock", "c7g-stock", "x86-baseline",
                "x86-devbox-not-a-baseline")
LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")

STAGES = (
    "read_file",
    "decode_webp",
    "resize_1024_area",
    "cvtcolor_gray",
    "clahe",
    "laplacian_sobel",
    "phash_blockmean",
    "yunet_1024",
    "brisque",
)
# Stages that are OpenCV work (read_file is disk I/O, reported but not part of opencv_total).
CV_STAGES = STAGES[1:]

# Pinned fallbacks used until config/models.lock lands. sha256 verified 2026-10-05:
# YuNet against the Hugging Face LFS oid of opencv/face_detection_yunet; BRISQUE yml files from
# opencv_contrib 5.x at commit 1275703cc59d5d11ee1f586a6d2347629bc04f2a.
MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "brisque_model_live.yml": (
        "https://raw.githubusercontent.com/opencv/opencv_contrib/1275703cc59d5d11ee1f586a6d2347629bc04f2a"
        "/modules/quality/samples/brisque_model_live.yml",
        "4c44c7eec9e5139830c0bb0e4a044c9cd351ba5bbc9e3798beca3243e2782ff4",
    ),
    "brisque_range_live.yml": (
        "https://raw.githubusercontent.com/opencv/opencv_contrib/1275703cc59d5d11ee1f586a6d2347629bc04f2a"
        "/modules/quality/samples/brisque_range_live.yml",
        "a18427f4f7ad087524bd0d63389649a3899b51ca6d44a4d5f8d8171f1f914a9c",
    ),
}


# --------------------------------------------------------------------------- helpers
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_models(models_dir: Path) -> dict[str, str]:
    """Make sure the three model files exist and match their pinned sha256. Returns name->sha."""
    models_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, (url, want) in MODELS.items():
        p = models_dir / name
        if not p.exists() or sha256_file(p) != want:
            # unique temp name per process + atomic rename: concurrent fetchers never see a partial file
            tmp = p.with_name(f".{p.name}.{os.getpid()}.part")
            with urllib.request.urlopen(url, timeout=60) as r, tmp.open("wb") as f:
                shutil.copyfileobj(r, f)
            if sha256_file(tmp) != want:
                tmp.unlink(missing_ok=True)
                raise SystemExit(f"sha256 mismatch downloading {name}")
            os.replace(tmp, p)
        got = sha256_file(p)
        if got != want:
            raise SystemExit(f"sha256 mismatch for {name}: {got} != {want}")
        out[name] = got
    return out


def imds(path: str, timeout: float = 0.5) -> str | None:
    """IMDSv2 lookup; returns None off-EC2."""
    try:
        tok_req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "300"},
        )
        tok = urllib.request.urlopen(tok_req, timeout=timeout).read().decode()
        req = urllib.request.Request(
            f"http://169.254.169.254/latest/meta-data/{path}", headers={"X-aws-ec2-metadata-token": tok}
        )
        return urllib.request.urlopen(req, timeout=timeout).read().decode()
    except (urllib.error.URLError, OSError, TimeoutError):
        return None


def container_info() -> dict:
    info: dict = {"in_container": Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()}
    try:
        cg = Path("/proc/1/cgroup").read_text()
        info["in_container"] = info["in_container"] or any(k in cg for k in ("docker", "containerd", "ecs"))
    except OSError:
        pass
    info["image_digest_env"] = os.environ.get("IMAGE_DIGEST")
    uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if uri:
        try:
            meta = json.loads(urllib.request.urlopen(uri, timeout=1).read())
            info["ecs_image"] = meta.get("Image")
            info["ecs_image_id"] = meta.get("ImageID")
        except (urllib.error.URLError, OSError, ValueError):
            pass
    return info


ARM_PARTS = {"0xd0c": "Neoverse-N1", "0xd40": "Neoverse-V1", "0xd4f": "Neoverse-V2", "0xd8e": "Neoverse-N3",
             "0xd84": "Neoverse-V3", "0xd49": "Neoverse-N2"}


def cpu_model() -> str | None:
    try:
        out = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if line.strip().startswith("Model name:"):
                return line.split(":", 1)[1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("CPU part"):
                part = line.split(":", 1)[1].strip().lower()
                return ARM_PARTS.get(part, f"arm part {part}")
    except OSError:
        pass
    return None


def pkg_config_opencv() -> str | None:
    for mod in ("opencv5", "opencv4"):
        try:
            r = subprocess.run(["pkg-config", "--modversion", mod], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return f"{mod} {r.stdout.strip()}"
        except (OSError, subprocess.SubprocessError):
            return None
    return None


HAL_LINE_KEYS = ("Custom HAL", "Baseline", "Dispatched code generation")


def cv2_hal(build_info: str) -> dict[str, str | None]:
    """The Custom HAL / Baseline / Dispatched lines of getBuildInformation() (None when absent) plus
    `opencv_hal`, the short label keepframe.hal_short() writes to RUN# (same rule, duplicated: the bench
    bundle ships without the package)."""
    found: dict[str, str] = {}
    for ln in build_info.splitlines():
        key, sep, val = ln.strip().partition(":")
        if sep and key in HAL_LINE_KEYS and key not in found:
            found[key] = " ".join(val.split())
    custom = found.get("Custom HAL")
    short = "none"
    if custom and custom.upper().startswith("YES"):
        entries = re.findall(r"([A-Za-z][\w.+-]*) \(ver ([^)]*)\)", custom)
        parts = [n.lower() if v in ("", "0.0.1") else f"{n.lower()}-{v}" for n, v in entries]
        parts.sort(key=lambda x: not x.startswith("kleidicv"))
        short = "+".join(parts) or "unknown"
    return {"cv2_custom_hal": custom, "cv2_cpu_baseline": found.get("Baseline"),
            "cv2_cpu_dispatch": found.get("Dispatched code generation"), "opencv_hal": short}


def custom_hal(build_lines: list[str]) -> str | None:
    """The `Custom HAL:` line of getBuildInformation(), e.g. 'YES (carotene (ver 0.0.1) KleidiCV (ver 26.03))'."""
    for ln in build_lines:
        if ln.strip().startswith("Custom HAL:"):
            return ln.split(":", 1)[1].strip()
    return None


def collect_env(label: str, model_shas: dict[str, str], wheel: str | None = None) -> dict:
    import psutil

    build_info = cv2.getBuildInformation()
    build = build_info.splitlines()
    hal = custom_hal(build)
    return {
        "host_label": label,
        "collected_at": dt.datetime.now(dt.UTC).isoformat(),
        "cv2_version": cv2.__version__,
        "cv2_file": cv2.__file__,
        "opencv_wheel": wheel,
        "custom_hal": hal,
        "kleidicv": bool(hal and "kleidicv" in hal.lower()),
        "armpl": any(re.search(r"ARM Perf Lib:\s*YES|ARMPL \(ver", ln) for ln in build),
        "cv2_build_information_head": build[:40],
        # Lines further down that show what the build was optimised with (Custom HAL = carotene / KleidiCV /
        # ARMPL, NEON / SVE dispatch, parallel framework, DNN). REPORT §6 evidence.
        "cv2_build_information_hal": [ln.rstrip() for ln in build if re.search(
            r"kleidi|HAL|NEON|SVE|CPU/HW|dispatch|Parallel framework|Trace|OpenCL|DNN|ONNX|Version control|ARM Perf",
            ln, re.I)],
        **cv2_hal(build_info),
        "cv2_wheel": os.environ.get("CULL_CV2_WHEEL") or wheel,
        "cv2_num_threads_main": cv2.getNumThreads(),
        "cv2_use_optimized": cv2.useOptimized(),
        "opencv_force_dnn_engine": os.environ.get("OPENCV_FORCE_DNN_ENGINE"),
        "pkg_config_opencv": pkg_config_opencv(),
        "has_contrib": {m: hasattr(cv2, m) for m in ("img_hash", "quality", "saliency", "xphoto")},
        "numpy_version": np.__version__,
        "psutil_version": psutil.__version__,
        "python_version": sys.version,
        "python_executable": sys.executable,
        "kernel": platform.release(),
        "uname": " ".join(x for i, x in enumerate(platform.uname()) if i != 1),  # drop hostname
        "machine": platform.machine(),
        "cpu_model": cpu_model(),
        "cpu_count_logical": os.cpu_count(),
        "mem_total_mb": round(psutil.virtual_memory().total / 2**20),
        "ec2": {
            "instance_type": imds("instance-type"),
            "ami_id": imds("ami-id"),
            "instance_id": imds("instance-id"),
            "availability_zone": imds("placement/availability-zone"),
            "region": imds("placement/region"),
            "product_codes": imds("product-codes"),
        },
        "container": container_info(),
        "model_sha256": model_shas,
    }


# --------------------------------------------------------------------------- data set
def read_manifest(path: Path) -> list[dict]:
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        raise SystemExit(f"empty manifest {path}")
    return rows


def rel_path(rec: dict) -> str:
    """Local/S3-relative path of one record: <event_id>/<file name of processed_key>."""
    name = Path(rec["processed_key"]).name if rec.get("processed_key") else f"{rec['photo_id']}.webp"
    return f"{rec['event_id']}/{name}"


def stage_local(records: list[dict], local_dir: Path, source: str) -> float:
    """Copy the set to local disk (skips files already present). Returns seconds spent (not timed)."""
    t0 = time.perf_counter()
    missing = [r for r in records if not (local_dir / rel_path(r)).exists()]
    if missing:
        if not source.startswith("s3://"):
            raise SystemExit(f"{len(missing)} files missing under {local_dir} and --source is not s3://")
        import boto3
        from botocore.config import Config

        bucket, _, prefix = source[5:].partition("/")
        s3 = boto3.client("s3", config=Config(max_pool_connections=32, retries={"max_attempts": 8}))

        def get(rec: dict) -> None:
            dst = local_dir / rel_path(rec)
            dst.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, prefix + rel_path(rec), str(dst))

        with cf.ThreadPoolExecutor(32) as ex:
            list(ex.map(get, missing))
    return time.perf_counter() - t0


# --------------------------------------------------------------------------- worker
_W: dict = {}


def _init_worker(models_dir: str) -> None:
    cv2.setNumThreads(1)
    md = Path(models_dir)
    _W["yunet"] = cv2.FaceDetectorYN.create(
        str(md / "face_detection_yunet_2023mar.onnx"), "", (1024, 1024), 0.9, 0.3, 5000
    )
    _W["brisque"] = cv2.quality.QualityBRISQUE_create(
        str(md / "brisque_model_live.yml"), str(md / "brisque_range_live.yml")
    )
    _W["clahe"] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    _W["yunet_size"] = None


def process_one(path: str) -> dict:
    """Run every stage on one image; returns per-stage nanoseconds plus a few sanity fields."""
    pc = time.perf_counter_ns
    t = {}
    t0 = pc()
    with open(path, "rb") as f:
        buf = np.frombuffer(f.read(), dtype=np.uint8)
    t1 = pc()
    t["read_file"] = t1 - t0

    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    t2 = pc()
    t["decode_webp"] = t2 - t1
    if img is None:
        return {"path": path, "error": f"decode_failed:{buf.size}B", **t}
    h, w = img.shape[:2]

    s = 1024.0 / max(h, w)
    p1024 = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    t3 = pc()
    t["resize_1024_area"] = t3 - t2

    gray = cv2.cvtColor(p1024, cv2.COLOR_BGR2GRAY)
    t4 = pc()
    t["cvtcolor_gray"] = t4 - t3

    _ = _W["clahe"].apply(gray)
    t5 = pc()
    t["clahe"] = t5 - t4

    lap_var = cv2.Laplacian(gray, cv2.CV_64F, ksize=3).var()
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    tenengrad = float(np.mean(gx * gx + gy * gy))
    t6 = pc()
    t["laplacian_sobel"] = t6 - t5

    ph = cv2.img_hash.pHash(gray)
    bm = cv2.img_hash.blockMeanHash(gray, mode=cv2.img_hash.BLOCK_MEAN_HASH_MODE_0)
    t7 = pc()
    t["phash_blockmean"] = t7 - t6

    ph_, pw_ = p1024.shape[:2]
    if _W["yunet_size"] != (pw_, ph_):
        _W["yunet"].setInputSize((pw_, ph_))
        _W["yunet_size"] = (pw_, ph_)
    _, faces = _W["yunet"].detect(p1024)
    t8 = pc()
    t["yunet_1024"] = t8 - t7

    brisque = float(_W["brisque"].compute(p1024)[0])
    t9 = pc()
    t["brisque"] = t9 - t8

    return {
        "path": path,
        "error": "",
        "width": w,
        "height": h,
        "n_faces": 0 if faces is None else int(faces.shape[0]),
        "lap_var": round(float(lap_var), 3),
        "tenengrad": round(tenengrad, 3),
        "brisque_score": round(brisque, 3),
        "phash_hex": bytes(ph.flatten()).hex(),
        "blockmean_bytes": int(bm.size),
        "pid": os.getpid(),
        **t,
    }


# --------------------------------------------------------------------------- monitoring
class Monitor(threading.Thread):
    """1 Hz psutil sampler: system CPU % and summed RSS of this process + workers."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        import psutil

        self.ps = psutil
        self.me = psutil.Process()
        self.stop_evt = threading.Event()
        self.cpu: list[float] = []
        self.rss_peak = 0
        self.ps.cpu_percent(interval=None)

    def run(self) -> None:
        while not self.stop_evt.wait(1.0):
            self._sample()

    def _sample(self) -> None:
        self.cpu.append(self.ps.cpu_percent(interval=None))
        total = 0
        try:
            procs = [self.me, *self.me.children(recursive=True)]
        except self.ps.Error:
            procs = [self.me]
        for p in procs:
            try:
                total += p.memory_info().rss
            except self.ps.Error:
                pass
        self.rss_peak = max(self.rss_peak, total)

    def stop(self) -> None:
        """Stop sampling. Always records one final sample, so a pass shorter than the 1 s period still
        reports CPU (averaged since the previous sample) and RSS instead of None."""
        self.stop_evt.set()
        self.join(timeout=3)
        self._sample()


# --------------------------------------------------------------------------- stats
def pct(vals: list[float], q: float) -> float:
    if not vals:
        return float("nan")
    a = sorted(vals)
    k = (len(a) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(a) - 1)
    return a[lo] + (a[hi] - a[lo]) * (k - lo)


def stage_stats(rows: list[dict]) -> dict[str, dict[str, float]]:
    ok = [r for r in rows if not r["error"]]
    out = {}
    for st in (*STAGES, "opencv_total", "image_total"):
        ms = [r[st] / 1e6 for r in ok]
        out[st] = {
            "n": len(ms),
            "p50_ms": round(pct(ms, 0.50), 4),
            "p95_ms": round(pct(ms, 0.95), 4),
            "mean_ms": round(statistics.fmean(ms), 4) if ms else float("nan"),
            "min_ms": round(min(ms), 4) if ms else float("nan"),
            "max_ms": round(max(ms), 4) if ms else float("nan"),
        }
    return out


def load_prices(path: Path) -> dict:
    if not path.exists():
        return {}
    import yaml

    return yaml.safe_load(path.read_text()) or {}


def price_per_hour(prices: dict, instance_type: str | None) -> dict:
    """EC2 on-demand $/h for the instance type (None off EC2). Every row runs open-source OpenCV only, so
    there is no software fee on top."""
    ec2 = (prices.get("ec2_on_demand_usd_per_hour") or {}).get(instance_type or "", {})
    ec2_usd = ec2.get("usd") if isinstance(ec2, dict) else ec2
    return {"ec2_usd_per_hour": ec2_usd}


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host-label", required=True, help=f"one of {KNOWN_LABELS} (or another kebab-case label)")
    ap.add_argument("--manifest", type=Path, default=HERE / "manifest_5k.jsonl")
    ap.add_argument("--local-dir", type=Path, default=Path("/tmp/bench5k"))
    ap.add_argument("--source", default=DEFAULT_SOURCE, help="s3://bucket/prefix/ holding <eventId>/<file>")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=200, help="images run before each timed rep, not measured")
    ap.add_argument("--limit", type=int, default=0, help="use only the first N manifest rows (0 = all)")
    ap.add_argument("--models-dir", type=Path, default=REPO / "models")
    ap.add_argument("--prices", type=Path, default=HERE / "prices.yaml")
    ap.add_argument("--wheel", default=None, help="OpenCV wheel file name (or 'pypi'), recorded in env/results")
    ap.add_argument("--dataset-name", default=None, help="recorded in results (default: manifest stem)")
    ap.add_argument("--out-root", type=Path, default=HERE / "results")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--chunksize", type=int, default=4)
    ap.add_argument("--overwrite", action="store_true", help="replace an existing run on a different data set")
    args = ap.parse_args()

    if not LABEL_RE.match(args.host_label):
        raise SystemExit(f"bad --host-label {args.host_label!r}")
    if args.host_label not in KNOWN_LABELS:
        print(f"note: non-standard host label {args.host_label!r}", file=sys.stderr)

    hal = cv2_hal(cv2.getBuildInformation())
    print(f"cv2 {cv2.__version__} from {cv2.__file__} opencv_hal={hal['opencv_hal']} "
          f"custom_hal={hal['cv2_custom_hal']!r} baseline={hal['cv2_cpu_baseline']!r} "
          f"dispatch={hal['cv2_cpu_dispatch']!r}", flush=True)
    model_shas = ensure_models(args.models_dir)
    env = collect_env(args.host_label, model_shas, args.wheel)
    print(f"Custom HAL: {env['custom_hal']} (KleidiCV={env['kleidicv']}, ARMPL={env['armpl']})", flush=True)
    if "kleidicv" in args.host_label and not env["kleidicv"]:
        print(f"WARNING: label {args.host_label!r} but this cv2 build has no KleidiCV HAL", file=sys.stderr)
    if "carotene" in args.host_label and env["kleidicv"]:
        print(f"WARNING: label {args.host_label!r} but this cv2 build includes KleidiCV", file=sys.stderr)

    records = read_manifest(args.manifest)
    if args.limit:
        records = records[: args.limit]
    copy_s = stage_local(records, args.local_dir, args.source)
    paths = [str(args.local_dir / rel_path(r)) for r in records]
    by_path = {str(args.local_dir / rel_path(r)): r for r in records}
    n = len(paths)
    set_bytes = sum(os.path.getsize(p) for p in paths)
    set_digest = hashlib.sha256()
    for p in paths:  # manifest order; same files => same digest on every host
        set_digest.update(hashlib.sha256(Path(p).read_bytes()).digest())
    print(f"{n} images, {set_bytes / 2**20:.1f} MiB local, copy {copy_s:.1f}s; workers={args.workers}", flush=True)

    out_dir = args.out_root / args.date / args.host_label
    prev = out_dir / "results.json"
    if prev.exists() and not args.overwrite:
        old = json.loads(prev.read_text())
        if old.get("set_sha256") != set_digest.hexdigest():
            raise SystemExit(f"{out_dir} already holds a run on a different data set "
                             f"({old.get('dataset')}); pass --overwrite or a different --date/--out-root")
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)

    reps = []
    raw_rows: list[dict] = []
    warm = paths[: min(args.warmup, n)]
    for rep in range(1, args.reps + 1):
        with cf.ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker,
                                    initargs=(str(args.models_dir),)) as ex:
            list(ex.map(process_one, warm, chunksize=args.chunksize))  # warm-up, discarded
            mon = Monitor()
            mon.start()
            t0 = time.perf_counter_ns()
            rows = list(ex.map(process_one, paths, chunksize=args.chunksize))
            wall_ns = time.perf_counter_ns() - t0
            mon.stop()
        for r in rows:
            r["rep"] = rep
            if not r["error"]:
                r["opencv_total"] = sum(r[s] for s in CV_STAGES)
                r["image_total"] = r["opencv_total"] + r["read_file"]
        errors = sum(1 for r in rows if r["error"])
        wall_s = wall_ns / 1e9
        rep_out = {
            "rep": rep,
            "n_images": n,
            "errors": errors,
            "wall_s": round(wall_s, 4),
            "images_per_sec": round(n / wall_s, 4),
            "cpu_util_mean": round(statistics.fmean(mon.cpu), 2) if mon.cpu else None,
            "cpu_util_samples": len(mon.cpu),
            "peak_rss_mb": round(mon.rss_peak / 2**20, 1),
            "stages": stage_stats(rows),
        }
        reps.append(rep_out)
        raw_rows.extend(rows)
        print(f"rep {rep}: {n} imgs in {wall_s:.2f}s = {n / wall_s:.2f} img/s, "
              f"cpu {rep_out['cpu_util_mean']}%, rss {rep_out['peak_rss_mb']} MB, errors {errors}", flush=True)

    max_child_rss_mb = round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)
    itype = env["ec2"]["instance_type"]
    pr = price_per_hour(load_prices(args.prices), itype)
    for r in reps:
        hourly = pr["ec2_usd_per_hour"]
        r["cost_per_1k_usd"] = round(hourly * (r["wall_s"] / 3600) / (n / 1000), 6) if hourly else None

    def med(key: str) -> float | None:
        vals = [r[key] for r in reps if r[key] is not None]
        return round(statistics.median(vals), 6) if vals else None

    summary = {
        "schema": "keepframe/bench-run/v1",
        "host_label": args.host_label,
        "date": args.date,
        "dataset": args.dataset_name or args.manifest.stem,
        "n_images": n,
        "set_mib": round(set_bytes / 2**20, 2),
        "set_sha256": set_digest.hexdigest(),
        "workers": args.workers,
        "reps": args.reps,
        "warmup_images": len(warm),
        "chunksize": args.chunksize,
        "copy_to_local_s_not_timed": round(copy_s, 2),
        "instance_type": itype,
        "opencv_version": cv2.__version__,
        "cv2_file": cv2.__file__,
        "opencv_wheel": args.wheel,
        "custom_hal": env["custom_hal"],
        "kleidicv": env["kleidicv"],
        "armpl": env["armpl"],
        "opencv_hal": env["opencv_hal"],
        "prices_usd_per_hour": pr,
        "median": {k: med(k) for k in ("images_per_sec", "wall_s", "cpu_util_mean", "peak_rss_mb", "cost_per_1k_usd")},
        "max_worker_rss_mb": max_child_rss_mb,
        "reps_detail": reps,
        "method": "per-image stage times via time.perf_counter_ns in worker processes with cv2.setNumThreads(1); "
                  "images/sec = N / wall time of the timed pass; warm-up images run first in the same pool and "
                  "are discarded; images were on local disk before timing.",
    }

    (out_dir / "env.json").write_text(json.dumps(env, indent=2) + "\n")
    (out_dir / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    with (out_dir / "results.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["host_label", "rep", "stage", "n", "p50_ms", "p95_ms", "mean_ms", "min_ms", "max_ms",
                    "images_per_sec", "wall_s", "cpu_util_mean", "peak_rss_mb", "cost_per_1k_usd"])
        for r in reps:
            for st, s in r["stages"].items():
                w.writerow([args.host_label, r["rep"], st, s["n"], s["p50_ms"], s["p95_ms"], s["mean_ms"],
                            s["min_ms"], s["max_ms"], r["images_per_sec"], r["wall_s"], r["cpu_util_mean"],
                            r["peak_rss_mb"], r["cost_per_1k_usd"]])
    raw_cols = ["rep", "event_id", "photo_id", "pid", "error", "width", "height", "n_faces", "lap_var",
                "tenengrad", "brisque_score", "phash_hex", *STAGES, "opencv_total", "image_total"]
    with (out_dir / "raw" / "per_image.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=raw_cols, extrasaction="ignore")
        w.writeheader()
        for r in raw_rows:
            rec = by_path.get(r["path"], {})
            w.writerow({**r, "event_id": rec.get("event_id"), "photo_id": rec.get("photo_id")})
    print(f"wrote {out_dir}", flush=True)
    print(json.dumps(summary["median"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Merge per-label benchmark runs into results.md + results.json (the UI /bench route schema).

Input:  bench/results/<date>/<label>/{results.json,env.json} written by run_benchmark.py.
Output: bench/results/<date>/results.md, results.json (the /bench document, published labels only),
        results_full.json (every label, all fields), and with --upload
        s3://<bucket>/bench/results/<date>/... plus s3://<bucket>/bench/results/latest/results.json.

UI schema (exact contract keys, see ui_doc for the additive aliases the shipped page reads):
{generated_at, hosts:[{label, instance_type, opencv_version, cv2_file, images_per_sec,
cost_per_1k_usd, cpu_util_mean, peak_rss_mb, stages:[{name, p50_ms, p95_ms}]}]}.
Every value is the median over reps; min/max across reps are kept alongside.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
STAGE_ORDER = ("decode_webp", "resize_1024_area", "cvtcolor_gray", "clahe", "laplacian_sobel",
               "phash_blockmean", "yunet_1024", "brisque", "opencv_total", "read_file", "image_total")
# Comparison baseline for the speed-up column, first one present wins.
BASELINE_ORDER = ("stock-carotene", "stock", "c7g-stock", "x86-baseline")


def _mmm(vals: list[float | None]) -> dict:
    v = [x for x in vals if x is not None]
    if not v:
        return {"median": None, "min": None, "max": None}
    return {"median": round(statistics.median(v), 6), "min": round(min(v), 6), "max": round(max(v), 6)}


def host_entry(run_dir: Path) -> dict:
    res = json.loads((run_dir / "results.json").read_text())
    env = json.loads((run_dir / "env.json").read_text()) if (run_dir / "env.json").exists() else {}
    reps = res["reps_detail"]
    stages = []
    for name in STAGE_ORDER:
        p50 = _mmm([r["stages"][name]["p50_ms"] for r in reps if name in r["stages"]])
        p95 = _mmm([r["stages"][name]["p95_ms"] for r in reps if name in r["stages"]])
        if p50["median"] is None:
            continue
        stages.append({"name": name, "p50_ms": p50["median"], "p95_ms": p95["median"],
                       "p50_ms_min": p50["min"], "p50_ms_max": p50["max"],
                       "p95_ms_min": p95["min"], "p95_ms_max": p95["max"]})
    ips = _mmm([r["images_per_sec"] for r in reps])
    cost = _mmm([r.get("cost_per_1k_usd") for r in reps])
    cpu = _mmm([r["cpu_util_mean"] for r in reps])
    rss = _mmm([r["peak_rss_mb"] for r in reps])
    return {
        "label": res["host_label"],
        "instance_type": res.get("instance_type") or env.get("ec2", {}).get("instance_type") or "non-ec2",
        "opencv_version": res["opencv_version"],
        "cv2_file": res["cv2_file"],
        "images_per_sec": ips["median"],
        "cost_per_1k_usd": cost["median"],
        "cpu_util_mean": cpu["median"],
        "peak_rss_mb": rss["median"],
        "stages": stages,
        "images_per_sec_min": ips["min"],
        "images_per_sec_max": ips["max"],
        "cost_per_1k_usd_min": cost["min"],
        "cost_per_1k_usd_max": cost["max"],
        "dataset": res.get("dataset"),
        "set_sha256": res.get("set_sha256"),
        "n_images": res["n_images"],
        "workers": res["workers"],
        "reps": res["reps"],
        "warmup_images": res.get("warmup_images"),
        "date": res.get("date"),
        "cpu_model": env.get("cpu_model"),
        "ami_id": (env.get("ec2") or {}).get("ami_id"),
        "kernel": env.get("kernel"),
        "numpy_version": env.get("numpy_version"),
        "prices_usd_per_hour": res.get("prices_usd_per_hour"),
        "opencv_wheel": res.get("opencv_wheel") or env.get("opencv_wheel"),
        "custom_hal": res.get("custom_hal") or env.get("custom_hal") or _hal_from_env(env),
        "instance_id": (env.get("ec2") or {}).get("instance_id"),
        "opencv_hal": res.get("opencv_hal") or env.get("opencv_hal"),
        "cxx_compiler": _build_line(env, "C++ Compiler"),
        "worker": worker_entry(run_dir),
    }


def _hal_from_env(env: dict) -> str | None:
    """`Custom HAL:` value for runs recorded before env.json carried it (2026-10-05 had only the 40-line head)."""
    for ln in (env.get("cv2_build_information_hal") or []) + (env.get("cv2_build_information_head") or []):
        if ln.strip().startswith("Custom HAL:"):
            return str(ln.split(":", 1)[1].strip())
    return None


def _build_line(env: dict, key: str) -> str | None:
    for ln in (env.get("cv2_build_information_head") or []) + (env.get("cv2_build_information_hal") or []):
        if ln.strip().startswith(key + ":"):
            return " ".join(ln.split(":", 1)[1].split())
    return None


def worker_entry(run_dir: Path) -> dict | None:
    """End-to-end product worker (keepframe.worker.run_event) results, if run on this host."""
    wp = run_dir / "worker" / "worker.json"
    if not wp.exists():
        return None
    w = json.loads(wp.read_text())
    reps = w.get("reps_detail") or []
    stages = {}
    for name in sorted({k for r in reps for k in (r.get("stages") or {})}):
        p50 = [r["stages"][name]["p50_ms"] for r in reps if r["stages"].get(name, {}).get("p50_ms") is not None]
        p95 = [r["stages"][name]["p95_ms"] for r in reps if r["stages"].get(name, {}).get("p95_ms") is not None]
        wall = [r["stages"][name]["wall_ms"] for r in reps if r["stages"].get(name, {}).get("wall_ms")]
        stages[name] = {"n": reps[0]["stages"].get(name, {}).get("n") if reps else None,
                        "p50_ms": round(statistics.median(p50), 3) if p50 else None,
                        "p95_ms": round(statistics.median(p95), 3) if p95 else None,
                        "wall_ms": round(statistics.median(wall), 1) if wall else None}
    ips = _mmm([r.get("images_per_sec_worker") for r in reps])
    mb = run_dir / "worker" / "microbench.json"
    return {"n_photos": w.get("n_photos"), "custom_hal": w.get("custom_hal"), "workers": w.get("workers"), "reps": len(reps),
            "images_per_sec": ips["median"], "images_per_sec_min": ips["min"], "images_per_sec_max": ips["max"],
            "wall_s": w["median"].get("wall_s_worker"), "cost_per_1k_usd": w["median"].get("cost_per_1k_usd"),
            "cpu_util_mean": w["median"].get("cpu_util_mean"), "peak_rss_mb": w["median"].get("peak_rss_mb"),
            "verdict_counts": reps[0].get("verdict_counts") if reps else None,
            "verdicts_identical_across_reps": w.get("verdict_counts_identical_across_reps"),
            "stages": stages,
            "microbench": json.loads(mb.read_text()).get("stages") if mb.exists() else None}


CONTRACT_HOST_KEYS = ("label", "instance_type", "opencv_version", "cv2_file", "images_per_sec", "cost_per_1k_usd",
                      "cpu_util_mean", "peak_rss_mb", "stages")
CONTRACT_STAGES = ("decode_webp", "resize_1024_area", "cvtcolor_gray", "clahe", "laplacian_sobel",
                   "phash_blockmean", "yunet_1024", "brisque", "opencv_total")


def ui_doc(hosts: list[dict], date: str, generated_at: str) -> dict:
    """The /bench document (bench/results/latest/results.json).

    Contract (2026-10-05): {generated_at, hosts:[{label, instance_type, opencv_version, cv2_file,
    images_per_sec, cost_per_1k_usd, cpu_util_mean, peak_rss_mb, stages:[{name, p50_ms, p95_ms}]}]}.
    The ui/app/bench page merged on main reads an older shape (BenchDoc in ui/lib/types.ts): top-level
    stages[{stage, host_label, p50_ms, p95_ms}], hosts[].{host_label, images_per_s, cpu_util_pct,
    usd_per_1k}, date, instance_type, n_images, synthetic. Those are emitted as additive aliases so both
    readers work; the contract keys are exact.
    """
    out_hosts, flat = [], []
    for h in hosts:
        stages = [{"name": st["name"], "p50_ms": st["p50_ms"], "p95_ms": st["p95_ms"]}
                  for st in h["stages"] if st["name"] in CONTRACT_STAGES]
        ch = {k: h[k] for k in CONTRACT_HOST_KEYS if k != "stages"}
        ch["stages"] = stages
        ch["custom_hal"] = h.get("custom_hal")
        ch["opencv_hal"] = h.get("opencv_hal")
        ch.update({"host_label": h["label"], "images_per_s": h["images_per_sec"],
                   "cpu_util_pct": h["cpu_util_mean"], "usd_per_1k": h["cost_per_1k_usd"],
                   "n_images": h["n_images"], "workers": h["workers"], "dataset": h["dataset"]})
        if h.get("worker"):
            w = h["worker"]
            ch["worker"] = {k: w[k] for k in ("n_photos", "workers", "images_per_sec", "wall_s", "cost_per_1k_usd",
                                              "cpu_util_mean", "peak_rss_mb", "verdicts_identical_across_reps")}
        out_hosts.append(ch)
        flat += [{"stage": st["name"], "host_label": h["label"], "p50_ms": st["p50_ms"], "p95_ms": st["p95_ms"]}
                 for st in stages]
    itypes = sorted({h["instance_type"] for h in hosts})
    return {"generated_at": generated_at, "hosts": out_hosts,
            "date": date, "synthetic": any("synthetic" in (h.get("dataset") or "") for h in hosts),
            "instance_type": " / ".join(itypes), "n_images": hosts[0]["n_images"] if hosts else None,
            "stages": flat}


# Same-toolchain pairs (KleidiCV OFF, KleidiCV ON): only the HAL differs, so their ratio isolates KleidiCV.
PAIRS = (("src-gcc14-carotene", "src-gcc14-kleidicv"), ("stock-carotene", "stock-kleidicv"))


def probe_tables(root: Path) -> list[str]:
    """Per-op table from <label>/probe.json files (bench/kleidicv_probe.py), labels in folder order."""
    probes = {p.parent.name: json.loads(p.read_text()) for p in sorted(root.glob("*/probe.json"))}
    if not probes:
        return []
    labs = list(probes)
    first = probes[labs[0]]["ops"]
    out = ["", "## Single-op probe (`bench/kleidicv_probe.py`, one thread, synthetic 1536x1024 frame, p50 ms)", "",
           "`ours` = calls the stage benchmark makes; `hal-path` = u8 calls the OpenCV 5 KleidiCV HAL implements.", "",
           "| op | kind | " + " | ".join(labs) + " |", "|---|---|" + "---|" * len(labs)]
    for op, v in first.items():
        cells = [f"{probes[lab]['ops'][op]['p50_ms']:.3f}" if op in probes[lab]["ops"] else "n/a" for lab in labs]
        out.append(f"| {op} | {v['kind']} | " + " | ".join(cells) + " |")
    out += ["", "Custom HAL per probe: " + "; ".join(f"`{lab}` = {probes[lab].get('custom_hal')}" for lab in labs)]
    return out


def pair_table(hosts: list[dict]) -> list[str]:
    by = {h["label"]: h for h in hosts}
    # gate on env.json opencv_hal (2026-10-08), not on the label: OFF must be exactly `carotene`, ON
    # must be `kleidicv-<ver>+carotene`; the pair must share data set and C++ compiler.
    rows = [(a, b) for a, b in PAIRS if a in by and b in by
            and by[a].get("set_sha256") == by[b].get("set_sha256")
            and by[a].get("opencv_hal") == "carotene"
            and str(by[b].get("opencv_hal") or "").startswith("kleidicv-") and by[b]["opencv_hal"].endswith("+carotene")]
    if not rows:
        return []
    out = ["", "## KleidiCV ON vs OFF (p50 ratio OFF/ON, >1 = KleidiCV faster)", "",
           "Pairs gated on env.json `opencv_hal` (`carotene` vs `kleidicv-26.03+carotene`). C++ compiler: " + "; ".join(
               f"`{x}` {by[x].get('cxx_compiler')}" for a, b in rows for x in (a, b)), "",
           "| stage | " + " | ".join(f"`{b}` vs `{a}`" for a, b in rows) + " |", "|---|" + "---|" * len(rows)]
    for name in STAGE_ORDER:
        cells = []
        for a, b in rows:
            sa = next((x for x in by[a]["stages"] if x["name"] == name), None)
            sb = next((x for x in by[b]["stages"] if x["name"] == name), None)
            cells.append(f"{sa['p50_ms'] / sb['p50_ms']:.3f}x" if sa and sb and sb["p50_ms"] else "n/a")
        if any(c != "n/a" for c in cells):
            out.append(f"| {name} | " + " | ".join(cells) + " |")
    out.append("| images/sec | " + " | ".join(
        f"{by[b]['images_per_sec'] / by[a]['images_per_sec']:.3f}x" for a, b in rows) + " |")
    return out


def fmt(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def render_md(hosts: list[dict], date: str, root: Path | None = None) -> str:
    base = next((h for lab in BASELINE_ORDER for h in hosts if h["label"] == lab), None)
    lines = [f"# Benchmark results {date}", "",
             "Median of reps; brackets are min-max across reps. Per-stage numbers are per image on one "
             "worker process with `cv2.setNumThreads(1)`. Images were on local disk before timing.", ""]
    lines += ["| label | instance | OpenCV | Custom HAL | dataset (N) | workers | img/s | $/1k img | CPU % | peak RSS MB |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for h in hosts:
        lines.append(
            f"| {h['label']} | {h['instance_type']} | {h['opencv_version']} | {h.get('custom_hal') or 'n/a'} | "
            f"{h['dataset']} ({h['n_images']}) | {h['workers']} | {fmt(h['images_per_sec'])} "
            f"[{fmt(h['images_per_sec_min'])}-{fmt(h['images_per_sec_max'])}] | "
            f"{fmt(h['cost_per_1k_usd'], 5)} | {fmt(h['cpu_util_mean'], 1)} | {fmt(h['peak_rss_mb'], 0)} |")
    shas = {h.get("set_sha256") for h in hosts}
    if len(shas) > 1:
        lines += ["", "**Warning:** these hosts ran on different data sets (set_sha256 differs); compare rows "
                  "only within one dataset."]
    lines += ["", "## Per-stage latency (ms per image)", ""]
    hdr = "| stage | " + " | ".join(f"{h['label']} p50 | {h['label']} p95" for h in hosts) + " |"
    lines += [hdr, "|---|" + "---|" * (2 * len(hosts))]
    names = [s for s in STAGE_ORDER if any(st["name"] == s for h in hosts for st in h["stages"])]
    for name in names:
        cells = []
        for h in hosts:
            st = next((s for s in h["stages"] if s["name"] == name), None)
            if st is None:
                cells += ["n/a", "n/a"]
            else:
                cells += [f"{st['p50_ms']:.3f} [{st['p50_ms_min']:.3f}-{st['p50_ms_max']:.3f}]",
                          f"{st['p95_ms']:.3f}"]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    if base and any(h is not base and "not-a-baseline" not in h["label"] for h in hosts):
        lines += ["", f"## Speed-up vs `{base['label']}` (p50 ratio, >1 = faster than baseline)", ""]
        others = [h for h in hosts if h is not base and "not-a-baseline" not in h["label"]
                  and h.get("set_sha256") == base.get("set_sha256")]
        lines += ["| stage | " + " | ".join(h["label"] for h in others) + " |", "|---|" + "---|" * len(others)]
        bmap = {s["name"]: s["p50_ms"] for s in base["stages"]}
        for name in names:
            cells = []
            for h in others:
                st = next((s for s in h["stages"] if s["name"] == name), None)
                cells.append(f"{bmap[name] / st['p50_ms']:.2f}x" if st and name in bmap and st["p50_ms"] else "n/a")
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
        if base["images_per_sec"]:
            lines.append("| images/sec | " + " | ".join(
                f"{h['images_per_sec'] / base['images_per_sec']:.2f}x" if h["images_per_sec"] else "n/a"
                for h in others) + " |")
        if base["cost_per_1k_usd"]:
            lines.append("| $/1k images | " + " | ".join(
                f"{h['cost_per_1k_usd'] / base['cost_per_1k_usd']:.2f}x" if h["cost_per_1k_usd"] else "n/a"
                for h in others) + " |")
    wk = [h for h in hosts if h.get("worker")]
    lines += pair_table(hosts)
    wbase = next((h for lab in BASELINE_ORDER for h in wk if h["label"] == lab), None)
    if wk:
        lines += ["", "## End-to-end product worker (`keepframe.worker.run_event`, no LLM)", "",
                  "Cold run per rep (proxies regenerated), inputs on local disk, LOCAL_MODE. Unlike the "
                  "stage table above, the worker runs faces/eyes only on grouped photos and BRISQUE only "
                  "when votes disagree, as the product does.", "",
                  "| label | instance | photos | workers | img/s | wall s | $/1k img | CPU % | peak RSS MB | "
                  "verdicts (keep/reject/review) | identical across reps |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for h in wk:
            w = h["worker"]
            vc = w.get("verdict_counts") or {}
            lines.append(f"| {h['label']} | {h['instance_type']} | {w['n_photos']} | {w['workers']} | "
                         f"{fmt(w['images_per_sec'])} [{fmt(w['images_per_sec_min'])}-{fmt(w['images_per_sec_max'])}] | "
                         f"{fmt(w['wall_s'])} | {fmt(w['cost_per_1k_usd'], 5)} | {fmt(w['cpu_util_mean'], 1)} | "
                         f"{fmt(w['peak_rss_mb'], 0)} | {vc.get('keep')}/{vc.get('reject')}/{vc.get('review')} | "
                         f"{w['verdicts_identical_across_reps']} |")
        if wbase and len(wk) > 1 and wbase["worker"]["images_per_sec"]:
            wb = wbase["worker"]
            lines += ["", f"Worker speed-up vs `{wbase['label']}` (images/s ratio; per-stage p50 ratio, >1 = faster)", "",
                      "| metric | " + " | ".join(h["label"] for h in wk if h is not wbase) + " |",
                      "|---|" + "---|" * (len(wk) - 1)]
            lines.append("| images/s | " + " | ".join(
                fmt(h["worker"]["images_per_sec"] / wb["images_per_sec"]) + "x" if h["worker"]["images_per_sec"] else "n/a"
                for h in wk if h is not wbase) + " |")
            for name, v in wb["stages"].items():
                cells = []
                for h in wk:
                    if h is wbase:
                        continue
                    o = h["worker"]["stages"].get(name) or {}
                    key = "p50_ms" if v.get("p50_ms") else "wall_ms"
                    cells.append(f"{v[key] / o[key]:.2f}x" if v.get(key) and o.get(key) else "n/a")
                lines.append(f"| {name} | " + " | ".join(cells) + " |")
        for h in wk:
            st = h["worker"]["stages"]
            lines += ["", f"Worker stages on `{h['label']}` (per-photo p50/p95 ms; wall ms for pipeline phases)", "",
                      "| stage | n | p50 | p95 | phase wall ms |", "|---|---|---|---|---|"]
            for name, v in st.items():
                lines.append(f"| {name} | {v['n']} | {fmt(v['p50_ms'], 3)} | {fmt(v['p95_ms'], 3)} | "
                             f"{fmt(v['wall_ms'], 1) if v['wall_ms'] else ''} |")
    if root is not None:
        lines += probe_tables(root)
    lines += ["", "Labels containing `not-a-baseline` are script-validation runs on the x86 dev box, not "
              "product numbers. `$/1k img` = EC2 on-demand $/h x wall-hours / (N/1000), prices from "
              "`bench/prices.yaml`; n/a off EC2. Every row is open-source OpenCV (no software fee).", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="results date folder (default: newest)")
    ap.add_argument("--root", type=Path, default=HERE / "results")
    ap.add_argument("--labels", nargs="*", default=None, help="subset/order of labels (default: all)")
    ap.add_argument("--publish-labels", nargs="*", default=None,
                    help="labels that go into the /bench document (default: all except *not-a-baseline* and any "
                         "host on a different data set than the first)")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--bucket", default=f"keepframe-work-{os.environ.get('AWS_ACCOUNT_ID', '<AWS_ACCOUNT_ID>')}-ap-south-1")
    args = ap.parse_args()

    if args.date is None:
        dates = sorted(p.name for p in args.root.iterdir() if p.is_dir() and p.name[:4].isdigit())
        if not dates:
            raise SystemExit(f"no result folders under {args.root}")
        args.date = dates[-1]
    day = args.root / args.date
    run_dirs = sorted(p for p in day.iterdir() if (p / "results.json").exists())
    if args.labels:
        order = {lab: i for i, lab in enumerate(args.labels)}
        run_dirs = sorted((p for p in run_dirs if p.name in order), key=lambda p: order[p.name])
    hosts = [host_entry(p) for p in run_dirs]
    if not hosts:
        raise SystemExit(f"no runs under {day}")
    gen = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    if args.publish_labels is not None:
        pub = [h for lab in args.publish_labels for h in hosts if h["label"] == lab]
    else:
        pub = [h for h in hosts if "not-a-baseline" not in h["label"]]
        pub = [h for h in pub if h.get("set_sha256") == pub[0].get("set_sha256")] if pub else []
    doc = ui_doc(pub, args.date, gen)
    (day / "results.json").write_text(json.dumps(doc, indent=2) + "\n")
    (day / "results_full.json").write_text(json.dumps({"generated_at": gen, "date": args.date, "hosts": hosts},
                                                      indent=2) + "\n")
    (day / "results.md").write_text(render_md(hosts, args.date, day))
    print(f"wrote {day / 'results.md'}, results.json (/bench doc: {[h['label'] for h in pub]}) and "
          f"results_full.json ({len(hosts)} hosts)")

    if args.upload:
        import boto3

        s3 = boto3.client("s3")
        for p in sorted(day.rglob("*")):
            if p.is_file() and "raw" not in p.relative_to(day).parts:
                key = f"bench/results/{args.date}/{p.relative_to(day).as_posix()}"
                s3.upload_file(str(p), args.bucket, key)
        s3.upload_file(str(day / "results.json"), args.bucket, "bench/results/latest/results.json",
                       ExtraArgs={"ContentType": "application/json", "CacheControl": "max-age=60"})
        print(f"uploaded to s3://{args.bucket}/bench/results/{args.date}/ and bench/results/latest/results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

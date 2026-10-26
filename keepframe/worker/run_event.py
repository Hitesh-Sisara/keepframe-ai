"""Deterministic batch pipeline (no LLM) for one event.

    python -m keepframe.worker.run_event --event-id X [--flavor stock|cool] [--local-root DIR]
                                           [--max-photos N] [--workers N] [--run-id ID]

ingest -> proxies + hashes (pool) -> cluster -> sharpness/exposure/faces/eyes (pool) -> best per group
-> decisions (keepframe.worker.policies) -> results/<eventId>/{decisions.jsonl,groups.json,summary.json}
(+ sheets/<eventId>/<groupId>.jpg for groups that need approval).

LOCAL_MODE: when WORK_BUCKET is unset (or --local-root is given), every key resolves under the local root
using the S3 layout of docs/ARCHITECTURE.md "S3 layout", so the whole worker runs offline on a folder of images.
Per-stage timings go to summary.json["stages"] and to stderr as JSON log lines.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import numpy as np

from keepframe import log_json, opencv_info
from keepframe.config import section, thresholds_sha
from keepframe.io import proxies
from keepframe.schemas import Decision, Failure, GroupResult, RunSummary, StageStat
from keepframe.tools.best import rank_group
from keepframe.tools.cluster import ClusterItem, cluster_items
from keepframe.tools.exposure import over_flag, under_flag
from keepframe.tools.hashes import from_jsonable, hamming
from keepframe.tools.ingest import get_context, ingest_event
from keepframe.tools.sheet import make_contact_sheet
from keepframe.worker import policies, run_record, stages
from keepframe.worker.parallel import Pool, default_workers


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Stats:
    """Per-stage timings: per-photo samples (p50/p95) and stage wall time."""

    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = {}
        self.wall: dict[str, float] = {}

    def add(self, stage: str, ms: float) -> None:
        self.samples.setdefault(stage, []).append(float(ms))

    def add_timings(self, tm: dict[str, float]) -> None:
        for k, v in tm.items():
            self.add(k, v)

    def set_wall(self, stage: str, ms: float) -> None:
        self.wall[stage] = self.wall.get(stage, 0.0) + ms

    def summary(self) -> dict[str, StageStat]:
        out: dict[str, StageStat] = {}
        for k in sorted(set(self.samples) | set(self.wall)):
            v = np.asarray(self.samples.get(k, []), dtype=np.float64)
            out[k] = StageStat(
                n=int(v.size),
                total_ms=round(float(v.sum()), 2),
                p50_ms=round(float(np.percentile(v, 50)), 3) if v.size else None,
                p95_ms=round(float(np.percentile(v, 95)), 3) if v.size else None,
                wall_ms=round(self.wall.get(k, 0.0), 2),
            )
        return out


def instance_type() -> str:
    for k in ("CULL_INSTANCE_TYPE", "INSTANCE_TYPE"):
        if os.environ.get(k):
            return os.environ[k]
    if not proxies.local_mode():
        try:  # EC2 IMDSv2 (absent on Fargate/Lambda; short timeout)
            tok_req = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
                                             headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
            tok = urllib.request.urlopen(tok_req, timeout=0.3).read().decode()  # noqa: S310
            req = urllib.request.Request("http://169.254.169.254/latest/meta-data/instance-type",
                                         headers={"X-aws-ec2-metadata-token": tok})
            itype: str = urllib.request.urlopen(req, timeout=0.3).read().decode()  # noqa: S310
            return itype
        except OSError:
            pass
    return f"local-{platform.machine()}"


def _put_result(event_id: str, run_id: str, name: str, data: bytes, content_type: str) -> None:
    """Write results/<eventId>/<runId>/<name> (immutable evidence of this run), then the unversioned latest copy."""
    store = proxies.get_store()
    for key in run_record.result_keys(event_id, run_id, name):
        store.put_bytes(key, data, content_type)


def _compact_sharp(s: dict[str, Any] | None) -> dict[str, Any] | None:
    if not s:
        return None
    keys = ("lap_var", "tenengrad", "fft_hf", "brisque", "label", "confidence", "blur_kind", "motion_ratio",
            "roi_used", "variant", "variant_fallback")
    return {k: s.get(k) for k in keys}


def _scores(r: dict[str, Any], rank: dict[str, Any] | None) -> dict[str, Any]:
    ex = r.get("exposure") or {}
    out: dict[str, Any] = {
        "sharpness": _compact_sharp(r.get("sharpness")),
        "sharpness_chain": r.get("sharpness_chain"),
        "exposure": {k: ex.get(k) for k in ("clip_lo_pct", "clip_hi_pct", "mean_L", "std_L", "entropy", "label",
                                            "confidence", "subject_L", "subject_std_L", "subject_source",
                                            "subject_clip_hi", "p5_L", "under_flag", "over_flag")} if ex else None,
        "n_faces": len(r.get("faces") or []),
        "faces_source": r.get("faces_source"),
    }
    if r.get("eyes"):
        out["eyes"] = r["eyes"]
    if r.get("face_quality"):
        out["face_quality"] = r["face_quality"]
    if rank:
        out["best_score"] = rank.get("score")
        out["breakdown"] = rank.get("breakdown")
    return out


def _evidence(ctx: Any, pid: str, r: dict[str, Any]) -> policies.PhotoEvidence:
    info = ctx.photos[pid]
    ex = info.exif
    return policies.PhotoEvidence(
        photo_id=pid,
        sharpness=r.get("sharpness"),
        exposure=r.get("exposure"),
        faces=r.get("faces") or [],
        faces_known=r.get("faces_source") in ("detector", "manifest", "metadata_none"),
        mfaces=info.record.known_face_count(),
        eyes=r.get("eyes"),
        eyes_known=bool(r.get("eyes_available")) and r.get("faces_source") == "detector",
        exposure_time_s=ex.exposure_time_s if ex else None,
        hour=policies.local_hour(info.ts, ex.local_hour if ex and ex.tz_known else None),
    )


class MissingRawError(RuntimeError):
    """Manifest photos without a processed image in raw/<eventId>/ (sync incomplete)."""


def run_event(event_id: str, flavor: str = "stock", local_root: str | None = None, max_photos: int | None = None,
              workers: int | None = None, run_id: str | None = None, sheets: bool | None = None,
              allow_missing_raw: bool = False, run_note: str | None = None) -> dict[str, Any]:
    """Run the full deterministic pipeline; returns the summary dict (also written to results/).

    Raises MissingRawError before any analysis when raw/ holds fewer images than the manifest lists, unless
    allow_missing_raw (then the missing photos become review decisions with confidence 0)."""
    if local_root:
        os.environ.pop("WORK_BUCKET", None)
        os.environ["CULL_LOCAL_ROOT"] = os.path.abspath(local_root)
    proxies.reset_store()
    t_run = time.perf_counter()
    started = _now()
    run_id = run_record.check_run_id(run_id or f"run_{event_id}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    workers = workers or default_workers()
    info = opencv_info()
    stats = Stats()
    failures: list[Failure] = []
    header = {"run_id": run_id, "event_id": event_id, "flavor": flavor, "workers": workers,
              "thresholds_sha": thresholds_sha(), "instance_type": instance_type(),
              "local_mode": proxies.local_mode(), **info}
    log_json("run_start", **header)

    def stage_done(name: str, t0: float, **extra: Any) -> None:
        ms = (time.perf_counter() - t0) * 1e3
        stats.set_wall(name, ms)
        log_json("stage", run_id=run_id, stage=name, wall_ms=round(ms, 1), **extra)

    # 0. one LIST each of raw/ and proxy/, then start pulling images into the disk cache while ingest reads
    #    the manifest and EXIF sidecars (all S3 GETs share one 64-thread pool; compute is ~10 ms per photo)
    t0 = time.perf_counter()
    store = proxies.get_store()
    # CULL_RECOMPUTE_PROXIES=1 (benchmarks): ignore stored proxies and rebuild them from raw/
    recompute = os.environ.get("CULL_RECOMPUTE_PROXIES") == "1"
    known_proxies = set() if recompute else set(store.list(f"proxy/{event_id}/"))
    proxies.set_known_proxies(known_proxies)
    have = {k.rsplit("/", 1)[-1].rsplit(".", 1)[0] for k in known_proxies}
    early = proxies.prefetch_async(
        [k for k in store.list(f"raw/{event_id}/") if k.rsplit("/", 1)[-1].rsplit(".", 1)[0] not in have]
        + sorted(known_proxies))
    present_originals = set(store.list(f"originals/{event_id}/"))
    proxies.set_present_originals(present_originals)
    stage_done("list", t0, n_proxies=len(known_proxies), n_originals=len(present_originals))

    # 1. ingest
    t0 = time.perf_counter()
    ing = ingest_event(event_id, max_photos=max_photos)
    ctx = get_context(event_id, create=False)
    for pid in ing["missing_raw"]:
        failures.append(Failure(photo_id=pid, error="processed image missing in raw/"))
    stage_done("ingest", t0, n_photos=ing["n_photos"], n_missing_raw=ing["n_missing_raw"])
    if ing["n_missing_raw"] and not allow_missing_raw:
        log_json("missing_raw", run_id=run_id, n_missing=ing["n_missing_raw"], sample=ing["missing_raw"][:5])
        raise MissingRawError(f"{ing['n_missing_raw']} of {ing['n_photos'] + ing['n_missing_raw']} manifest photos "
                              f"have no raw/{event_id}/ object (e.g. {ing['missing_raw'][:3]}); re-run the sync or "
                              "pass --allow-missing-raw")

    # 1b. wait for the images the pool reads first (anything ingest kept but the early LIST missed is fetched too)
    t0 = time.perf_counter()
    want = []
    for pid in ctx.order:
        pk = proxies.proxy_key(event_id, pid)
        want.append(pk if pk in known_proxies else proxies.raw_key(event_id, pid))
    pf = early.wait()
    late = proxies.prefetch([k for k in want if k])
    stage_done("prefetch", t0, **pf, late_fetched=late["fetched"])
    worker_io: Counter[str] = Counter()

    with Pool(event_id, workers if len(ctx.order) > 1 else 1, known_proxies=known_proxies,
              originals=present_originals) as pool:
        # 2. proxies + hashes
        t0 = time.perf_counter()
        prep = pool.map(stages.prepare_photo, [(event_id, p) for p in ctx.order])
        ok_ids: list[str] = []
        n_up = 0
        for r in prep:
            if r.get("proxy_jpeg") is not None:
                proxies.put_background(proxies.proxy_key(event_id, r["photo_id"]), r.pop("proxy_jpeg"), "image/jpeg")
                n_up += 1
            if pool.workers > 1:
                worker_io.update(r.get("io") or {})
            stats.add_timings(r.get("timings", {}))
            if r["ok"]:
                ctx.hashes[r["photo_id"]] = r["hashes"]
                ok_ids.append(r["photo_id"])
            else:
                failures.append(Failure(photo_id=r["photo_id"], error=r["error"]))
        _put_result(event_id, run_id, "hashes.json", json.dumps(ctx.hashes, sort_keys=True).encode(),
                    "application/json")
        stage_done("proxies_hashes", t0, n=len(prep), failed=len(prep) - len(ok_ids), proxy_uploads_queued=n_up)

        # 3. cluster
        t0 = time.perf_counter()
        items = []
        for pid in ok_ids:
            h = from_jsonable(ctx.hashes[pid])
            pi = ctx.photos[pid]
            items.append(ClusterItem(pid, pi.camera, pi.ts, h["phash"], h["block_mean"], h["color_moment"],
                                     pi.record.md5, pi.record.name))
        cl = cluster_items(items, event_id, load_gray=lambda p: proxies.load_p1024_gray(event_id, p))
        ctx.groups = {g["group_id"]: list(g["photo_ids"]) for g in cl["groups"]}
        ctx.group_of = {p: gid for gid, ps in ctx.groups.items() for p in ps}
        ctx.exact_duplicates = dict(cl["exact_duplicates"])
        stage_done("cluster", t0, n_groups=cl["n_groups"], n_singletons=len(cl["singletons"]))

        # group members need the full-size image (faces) and maybe the original (policy 2): fetch them now
        t0 = time.perf_counter()
        need: list[str | None] = []
        if stages.faces_available():  # detect_faces runs on the full processed image
            need += [proxies.raw_key(event_id, p) for p in ctx.group_of if ctx.photos[p].record.known_face_count() != 0]
        # originals are read only when a group member stays borderline (policy 2): fetched lazily
        gp = proxies.prefetch([k for k in need if k])
        stage_done("prefetch_groups", t0, **gp)

        # 4. per-photo analysis
        t0 = time.perf_counter()
        ana = pool.map(stages.analyse_photo, [(event_id, p, p in ctx.group_of) for p in ok_ids])
        results: dict[str, dict[str, Any]] = {}
        for r in ana:
            if pool.workers > 1:
                worker_io.update(r.get("io") or {})
            stats.add_timings(r.get("timings", {}))
            if r["ok"]:
                results[r["photo_id"]] = r
                ctx.results[r["photo_id"]] = r
            else:
                failures.append(Failure(photo_id=r["photo_id"], error=r["error"]))
        stage_done("analyse", t0, n=len(ana))

    # adaptive per-event exposure baseline: a dim venue is a venue property, not a defect
    exp_cfg = section("exposure")
    means = [r["exposure"]["mean_L"] for r in results.values() if r.get("exposure")]
    event_median_l = float(np.median(means)) if len(means) >= int(exp_cfg["event_baseline_min_photos"]) else None
    for r in results.values():
        if r.get("exposure"):
            r["exposure"]["under_flag"] = under_flag(r["exposure"], event_median_l, exp_cfg)
            r["exposure"]["over_flag"] = over_flag(r["exposure"], event_median_l, exp_cfg)
    log_json("exposure_baseline", run_id=run_id, event_median_mean_L=event_median_l,
             n_label_under=sum(1 for r in results.values() if (r.get("exposure") or {}).get("label") == "under"),
             n_under_flag=sum(1 for r in results.values() if (r.get("exposure") or {}).get("under_flag")),
             n_label_over=sum(1 for r in results.values() if (r.get("exposure") or {}).get("label") == "over"),
             n_over_flag=sum(1 for r in results.values() if (r.get("exposure") or {}).get("over_flag")))

    # 5. groups -> best + decisions
    t0 = time.perf_counter()
    decisions: list[Decision] = []
    group_out: list[GroupResult] = []
    sheet_cfg = section("worker")["contact_sheets_for_approval"] if sheets is None else sheets
    sheet_jobs: list[tuple[str, list[str], dict[str, str], str]] = []
    for g in cl["groups"]:
        gid = g["group_id"]
        members = [p for p in g["photo_ids"] if p in results]
        if not members:
            continue
        tb = time.perf_counter()
        if len(members) == 1:
            best = {"best_photo_id": members[0], "ranking": [{"photo_id": members[0], "score": 1.0, "breakdown": {}}],
                    "tie": False, "margin": 1.0}
        else:
            best = rank_group({p: results[p] for p in members})
        stats.add("best", (time.perf_counter() - tb) * 1e3)
        ph = {p: from_jsonable(ctx.hashes[p])["phash"] for p in members}
        pairs = {(a, b): int(hamming(ph[a], ph[b])) for a in members for b in members if a < b}
        ev = {p: _evidence(ctx, p, results[p]) for p in members}
        gd = policies.decide_group(gid, best, ev, ctx.exact_duplicates, pairs)
        rank_of = {r["photo_id"]: r for r in best["ranking"]}
        sheet_key = None
        if gd.needs_approval and sheet_cfg:
            sheet_key = f"sheets/{event_id}/{gid}.jpg"
            sheet_jobs.append((gid, [r["photo_id"] for r in best["ranking"]],
                               {p: f"{p}  {rank_of[p]['score']:.2f}" for p in members}, gd.keeper_photo_id))
        for d in gd.decisions:
            decisions.append(Decision(photo_id=d.photo_id, event_id=event_id, verdict=d.verdict,  # type: ignore[arg-type]
                                      reason_codes=d.reason_codes, reason_text=d.reason_text,
                                      confidence=round(d.confidence, 4), group_id=gid, decided_by="worker",
                                      run_id=run_id, scores=_scores(results[d.photo_id], rank_of.get(d.photo_id)),
                                      needs_approval=d.needs_approval))
        group_out.append(GroupResult(group_id=gid, members=members, size=len(members), span_s=g["span_s"],
                                     best_photo_id=gd.keeper_photo_id, margin=round(gd.margin, 6), tie=gd.tie,
                                     ranking=best["ranking"], needs_approval=gd.needs_approval,
                                     tied_candidates=gd.tied_candidates, approval_reasons=gd.approval_reasons,
                                     contact_sheet_key=sheet_key))
    for pid in cl["singletons"]:
        if pid not in results:
            continue
        d = policies.decide_singleton(_evidence(ctx, pid, results[pid]))
        decisions.append(Decision(photo_id=pid, event_id=event_id, verdict=d.verdict,  # type: ignore[arg-type]
                                  reason_codes=d.reason_codes, reason_text=d.reason_text,
                                  confidence=round(d.confidence, 4), group_id=None, decided_by="worker",
                                  run_id=run_id, scores=_scores(results[pid], None), needs_approval=d.needs_approval))
    # contact sheets for escalated groups: rendered concurrently (cv2 releases the GIL; proxies are cached)
    if sheet_jobs:
        from concurrent.futures import ThreadPoolExecutor

        def render(job: tuple[str, list[str], dict[str, str], str]) -> float:
            gid, ids, caps, keeper = job
            ts = time.perf_counter()
            try:
                make_contact_sheet(gid, event_id=event_id, photo_ids=ids, captions=caps, best_photo_id=keeper,
                                   background=True)
            except Exception as e:  # noqa: BLE001
                log_json("sheet_error", run_id=run_id, group_id=gid, error=str(e)[:200])
            return (time.perf_counter() - ts) * 1e3

        with ThreadPoolExecutor(max_workers=min(8, len(sheet_jobs))) as ex:
            for ms in ex.map(render, sheet_jobs):
                stats.add("contact_sheet", ms)
    decided = {d.photo_id for d in decisions}
    for f in failures:
        if f.photo_id not in decided:
            decisions.append(Decision(photo_id=f.photo_id, event_id=event_id, verdict="review",
                                      reason_codes=["REVIEW_LOW_CONFIDENCE"],
                                      reason_text=f"Review: image could not be analysed ({f.error[:80]})"[:140],
                                      confidence=0.0, decided_by="worker", run_id=run_id, needs_approval=True))
            decided.add(f.photo_id)
    order = {p: i for i, p in enumerate(ctx.order)}
    decisions.sort(key=lambda d: (order.get(d.photo_id, 1 << 30), d.photo_id))
    stage_done("decide", t0, n_decisions=len(decisions))

    # 6. write results
    t0 = time.perf_counter()
    _put_result(event_id, run_id, "decisions.jsonl",
                "".join(d.model_dump_json() + "\n" for d in decisions).encode(), "application/x-ndjson")
    groups_doc = {"event_id": event_id, "run_id": run_id, "n_groups": len(group_out),
                  "singletons": [p for p in cl["singletons"] if p in results],
                  "exact_duplicates": cl["exact_duplicates"], "groups": [g.model_dump() for g in group_out]}
    _put_result(event_id, run_id, "groups.json", json.dumps(groups_doc, indent=1).encode(), "application/json")
    t1 = time.perf_counter()
    flushed = proxies.flush_writes()
    stats.set_wall("proxy_upload_drain", (time.perf_counter() - t1) * 1e3)
    stage_done("write", t0, proxy_uploads_flushed=flushed)
    io = Counter(proxies.io_counts())
    io.update(worker_io)

    wall_s = time.perf_counter() - t_run
    vc: Counter[str] = Counter(d.verdict for d in decisions)
    rc = Counter(c.partition(":")[0] for d in decisions for c in d.reason_codes)
    summary = RunSummary(
        run_id=run_id, event_id=event_id, status="ok" if not failures else "partial", started_at=started,
        ended_at=_now(), wall_s=round(wall_s, 3), n_photos=len(ctx.order), n_groups=len(group_out),
        n_singletons=len(groups_doc["singletons"]), verdict_counts={k: vc[k] for k in ("keep", "reject", "review")},
        reason_counts=dict(sorted(rc.items())), escalation_count=sum(1 for g in group_out if g.needs_approval)
        + sum(1 for d in decisions if d.needs_approval and d.group_id is None),  # human questions, 1 per group
        opencv_version=info["opencv_version"], cv2_file=info["cv2_file"], opencv_hal=info.get("opencv_hal"),
        thresholds_sha=header["thresholds_sha"],
        flavor=flavor,  # type: ignore[arg-type]
        instance_type=header["instance_type"], image_sha=run_record.image_sha(),
        results_run_prefix=run_record.run_results_prefix(event_id, run_id),
        faces_available=stages.faces_available(), workers=workers, stages=stats.summary(), failures=failures[:200],
        images_per_s=round(len(ok_ids) / wall_s, 3) if wall_s > 0 else None,
        s3_get_count=io.get("get", 0), s3_put_count=io.get("put", 0), s3_head_count=io.get("head", 0),
        s3_list_count=io.get("list", 0), s3_get_bytes=io.get("get_bytes", 0), s3_put_bytes=io.get("put_bytes", 0),
    ).model_dump()
    _put_result(event_id, run_id, "summary.json", json.dumps(summary, indent=1).encode(), "application/json")
    if not proxies.local_mode():
        run_record.write_run_item(summary, run_note=run_note)  # RUN# item for GET /runs; never raises
    for name, st in summary["stages"].items():
        log_json("stage_stats", run_id=run_id, stage=name, **st)
    log_json("run_end", run_id=run_id, wall_s=summary["wall_s"], verdict_counts=summary["verdict_counts"],
             images_per_s=summary["images_per_s"], status=summary["status"])
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m keepframe.worker.run_event", description=__doc__.split("\n")[0])
    ap.add_argument("--event-id", required=True)
    ap.add_argument("--flavor", choices=("stock", "cool"), default=os.environ.get("CULL_FLAVOR", "stock"))
    ap.add_argument("--local-root", help="LOCAL_MODE root (S3 layout on disk); implies WORK_BUCKET unset")
    ap.add_argument("--max-photos", type=int)
    ap.add_argument("--workers", type=int, help="process pool size (default: vCPUs)")
    ap.add_argument("--run-id")
    ap.add_argument("--no-sheets", action="store_true", help="skip contact sheets for approval groups")
    ap.add_argument("--run-note", help="free text stored as run_note on the RUN# item")
    ap.add_argument("--allow-missing-raw", action="store_true",
                    help="analyse what exists when raw/ is incomplete (default: fail before analysis)")
    a = ap.parse_args(argv)
    s = run_event(a.event_id, flavor=a.flavor, local_root=a.local_root, max_photos=a.max_photos, workers=a.workers,
                  run_id=a.run_id, sheets=False if a.no_sheets else None, allow_missing_raw=a.allow_missing_raw,
                  run_note=a.run_note)
    print(json.dumps({k: s[k] for k in ("run_id", "event_id", "status", "wall_s", "n_photos", "n_groups",
                                        "verdict_counts", "images_per_s")}))
    return 0 if s["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())

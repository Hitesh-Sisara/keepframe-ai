# CV core

OpenCV 5 tools for hashing, near-duplicate clustering, sharpness, exposure and best-of-burst, plus the
deterministic no-LLM worker that turns them into keep / reject / review decisions.
Faces, eyes and face quality come from the face tools (`docs/CV_FACES.md`); the worker calls them when importable.

## Run it

```bash
uv venv .venv --python 3.12 && uv pip install -e '.[dev]'     # or the pinned list in pyproject.toml
python -m keepframe.worker.synth --root /tmp/cull-local --event-id ev_synth   # 12 synthetic frames
python -m keepframe.worker.run_event --event-id ev_synth --local-root /tmp/cull-local [--workers 8]
python -m keepframe.worker.microbench --repeats 50        # per-stage timings below
.venv/bin/python -m pytest tests/unit tests/integration -q  # 78 tests, ~40 s
```

LOCAL_MODE: with `WORK_BUCKET` unset (or `--local-root`), every key resolves under the local root in the
S3 layout of `docs/ARCHITECTURE.md` "S3 layout" (`manifests/`, `raw/`, `originals/`, `exif/`, `proxy/`, `sheets/`, `results/`), so the
worker runs offline on a folder. With `WORK_BUCKET` set it uses `keepframe.io.s3` (data agent); decoded
inputs are cached under `$CULL_CACHE_DIR` (default `/tmp/cull`). Models: `$CULL_MODELS_DIR`, else
`/opt/models`, else `<repo>/models` (BRISQUE LIVE yml files from `config/models.lock`).

Outputs per run go under `results/<eventId>/<runId>/`, so a rerun never overwrites an earlier run's evidence.
For one release the worker also writes the same bytes to the unversioned `results/<eventId>/<name>`, which always
holds the latest run. The files are `decisions.jsonl` (one `Decision` per photo, with reason codes, ≤140-char
reason text, confidence, raw scores), `groups.json` (members, best, margin, tie, approval flag, contact sheet
key), `summary.json` (`opencv_version`, `cv2_file`, `opencv_hal`, `thresholds_sha`, `flavor`, `instance_type`,
`image_sha`, `results_run_prefix`, verdict and reason counts, per-stage p50/p95/wall) and `hashes.json`.
`image_sha` is the container digest `sha256:…`: from env `CULL_IMAGE_SHA` / `IMAGE_SHA` / `IMAGE_DIGEST`,
else ECS task metadata v4 `Image` (when pinned by digest) or `ImageID`, else null. The worker also writes `proxy/<eventId>/*.jpg`, and `sheets/<eventId>/<groupId>.jpg` for groups
that need a human. With `WORK_BUCKET` set, one RUN# item is upserted into DynamoDB `$CULL_DDB_TABLE` (default `keepframe-decisions`):
pk `EVENT#<eventId>`, sk `RUN#<runId>`, `gsi1pk="RUNS"`, `decided_by="worker"`, `llm_provider="none"`.
It is an UpdateItem, so the item POST /runs created is kept. Failures are logged, never raised.

## Modules

| module | what |
|---|---|
| `keepframe/__init__.py` | asserts `cv2.__version__` 5.x and `img_hash/quality/saliency/xphoto`; logs version, `cv2.__file__`, `opencv_hal` + Custom HAL / Baseline / Dispatched lines, first 40 build-info lines as one JSON line (`KEEPFRAME_IMPORT_LOG=0` silences) |
| `config.py` | `thresholds()`, `section()`, `thresholds_sha()`, `reason_codes()`, `model_path()` |
| `schemas.py` | `PhotoRecord` (manifest, `extra="allow"`), every tool result, `Decision`, `GroupResult`, `TraceLine`, `RunSummary`, `ReasonCode` (checked against `config/reason_codes.yaml` at import) |
| `io/images.py`, `io/proxies.py`, `io/exif.py` | codecs + INTER_AREA resize (HEIC → None); `load_full/p1024/p1024_gray/original`; small + rich EXIF sidecars |
| `tools/{ingest,hashes,cluster,sharpness,exposure,best,sheet}.py` | the vision tools as plain functions; pure `*_image` variants for workers |
| `worker/policies.py` | P1–P9 policies as pure functions (shared with the agent) |
| `worker/{stages,parallel,run_event}.py` | pipeline, `ProcessPoolExecutor` with `cv2.setNumThreads(1)`, CLI |
| `worker/synth.py`, `worker/microbench.py` | synthetic events (tests, smoke runs), per-stage benchmark |

## Arm acceleration (KleidiCV)

[KleidiCV](https://gitlab.arm.com/kleidi/kleidicv) is Arm's Apache-2.0 library of NEON/SVE image-processing
kernels. OpenCV 5.0.0 links it as a custom HAL on aarch64 by default, next to carotene (the older NEON HAL),
so supported calls dispatch to it. It is plain open-source OpenCV.
This is NOT COOL: COOL is billed through AWS Marketplace and is out by decision (2026-10-07). We claim nothing about COOL.

Which wheel has it (opencv-contrib-python-headless 5.0.0.93, checked 2026-10-08 by grepping `cv2.abi3.so`):

| wheel | sha256 | Custom HAL |
|---|---|---|
| `opencv_contrib_python_headless-5.0.0.93-cp37-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl` | `c035e12b6078b3351ad577e90a4ff4ead74155fa5470eb8df8f5b54a8ce7727f` | carotene + KleidiCV 26.03 |
| `opencv_contrib_python_headless-5.0.0.93-cp37-abi3-manylinux_2_28_aarch64.whl` | `01331ae1a65a21d81310b5584900f73c3012ab4bdcf6aa26085c1870eea45da8` | carotene only |

On glibc ≥ 2.28 (Debian bookworm images, Ubuntu 24.04, AL2023), pip and uv both choose the manylinux_2_28
wheel. Both opencv-python-headless aarch64 wheels include KleidiCV, but we need contrib (img_hash,
quality/BRISQUE, saliency, xphoto).

Measured result ([bench/results/2026-10-08/results.md](../bench/results/2026-10-08/results.md), c8g.xlarge,
manifest_5k, 4 workers, 3 reps):

* KleidiCV is active in the manylinux2014 wheel. A probe shows it speeding up INTER_LINEAR resize, GaussianBlur
  and exp. But the calls we use are not on its accelerated path: INTER_AREA resize, CV_64F Laplacian/Sobel,
  CLAHE, pHash, YuNet and BRISQUE. With the compiler held constant (GCC 14.2.1 source builds, KleidiCV on vs
  off), the stage benchmark is 0.998× end to end and every stage is within about ±1%.
* The product worker (`run_event`, 661-photo event) is about 6% slower on the KleidiCV PyPI wheel than on the
  carotene wheel on two hosts: 25.85 and 25.56 img/s vs 27.45 img/s. Verdicts are identical in every rep.
  The KleidiCV PyPI wheel was built with GCC 10.2.1 and the carotene wheel with GCC 14.2.1. Given the
  same-compiler result above, we attribute that gap to the build rather than to KleidiCV (inferred, not proven).

So the product images ship the faster build, the carotene-only manylinux_2_28 wheel, and every run records
which HAL it used.

How we pin and verify it:

* `scripts/install_opencv_wheel.sh [python] [carotene|kleidicv]` (default `carotene`) reinstalls cv2 from the
  exact PyPI URL (`--no-deps`, `--require-hashes`). It asserts that KleidiCV is absent from
  `cv2.getBuildInformation()` for `carotene`, or present for `kleidicv`. It is a no-op on x86_64.
  `Dockerfile` (worker) and `services/agent_runtime/Dockerfile.agent` run it with `carotene` after
  `pip install -r`, and the worker image's final check repeats the assertion, so an arm64 image can only
  carry the pinned carotene wheel. The `kleidicv` variant is kept for the bench rows (`stock-kleidicv`,
  `bench/ec2/launch_bench_host.sh`). pyproject, uv.lock and requirements.txt keep the plain `==5.0.0.93` pin,
  and x86 is unchanged.
* At run time the `opencv_build` startup log line, `run_start`, `summary.json` and the RUN# item carry
  `opencv_hal` (`carotene` in the product images, `kleidicv-26.03+carotene` for the bench KleidiCV rows, `ipp`
  on x86, or `none`). The startup log also has `cv2_custom_hal`, `cv2_cpu_baseline` and `cv2_cpu_dispatch`, the
  raw "Custom HAL", "Baseline" and "Dispatched code generation" lines. The bench `env.json` always has the same
  four keys.

## Measured timings — x86 dev box (not Graviton)

x86_64 DigitalOcean VM, 8 vCPU "DO-Regular" @ 2.0 GHz, shared with other workloads (load avg 2–3), stock
`opencv-contrib-python-headless==5.0.0.93`, Python 3.12, `cv2.setNumThreads(1)`, 1024×683 synthetic proxy
from a 2560×1707 WebP, 50 repeats after warm-up (`python -m keepframe.worker.microbench`). These replace
the plan's "(est.)" figures for the dev box only; **Graviton (c8g) numbers come from the benchmark.**

| stage | p50 ms | p95 ms | design estimate (ms) |
|---|---|---|---|
| Decode processed WebP 2560 px (`cv2.imdecode`) | 45.4 | 47.5 | ~30–45 (est.) |
| Resize 2560→1024 `INTER_AREA` | 32.7 | 44.3 | (in decode est.) |
| Proxy JPEG q90 encode | 3.8 | 4.1 | — |
| Proxy JPEG decode (every later tool) | 4.1 | 4.6 | — |
| `img_hash.pHash` (gray 1024) | 0.0 | 0.1 | ~1 (est.) |
| `img_hash.blockMeanHash` mode 0 | 0.6 | 0.8 | ~1 (est.) |
| `img_hash.colorMomentHash` | 6.7 | 7.5 | ~1 (est.) |
| Hamming 1 vs 10k hashes (numpy unpackbits) | 0.9 | 1.1 | — |
| Laplacian var (CV_64F, ksize 3) | 6.3 | 7.2 | 6–10 for all three (est.) |
| Tenengrad (2× Sobel CV_64F) | 9.6 | 10.6 |  |
| FFT high-freq (500 px, `cv2.dft`) | 13.0 | 15.6 |  |
| Laplacian + Tenengrad + FFT together | 28.0 | 32.8 | 6–10 (est.) |
| Saliency ROI (`StaticSaliencyFineGrained` at 512 px) | 63.4 | 68.0 | — |
| Saliency at full 1024 px (not used) | 233.6 | 296.4 | — |
| BRISQUE 1024 (LIVE model, `cv2.quality`) | 59.0 | 70.7 | 80–150 (est.) |
| `score_sharpness` roi=full (no BRISQUE) | 33.8 | 37.7 | — |
| `score_sharpness` roi=saliency (no BRISQUE) | 86.9 | 95.0 | — |
| `score_exposure` (Lab, calcHist, entropy) | 8.1 | 9.9 | < 3 (est.) |
| `pick_best_in_cluster`, 8 frames | 0.3 | 0.3 | — |
| Contact sheet, 4 tiles (FontFace captions) | 22.5 | 26.0 | — |

Per photo on one vCPU: proxy ≈ 85 ms (decode + resize + encode), hashes ≈ 8 ms, full-frame sharpness
≈ 30 ms, exposure ≈ 8 ms; singletons add saliency (≈ 63 ms). BRISQUE runs only when Laplacian and FFT
votes disagree (22 % of frames in the synthetic set). Observations:

* `INTER_AREA` 2560→1024 (ratio 2.5) costs 30–40 ms, ~9× `INTER_LINEAR`; with WebP decode it is most of the
  per-photo budget. A good COOL / KleidiCV comparison point.
* Saliency is computed at 512 px: on 5 synthetic frames its ROI keeps IoU ≥ 0.93 with the 1024-px map at a
  quarter of the cost; 384 px dropped to IoU 0.76 (`sharpness.saliency_long_edge`).
* BRISQUE is ~50–60 ms at 1024, below the plan estimate.

End to end (`run_event`, 96 synthetic 2560-px frames, 17 groups, no face models, LOCAL_MODE, cold cache):

| workers | wall s | images/s | proxies+hashes s | analyse s | cluster | decide (+ sheets) |
|---|---|---|---|---|---|---|
| 1 | 20.1 | 4.8 | 12.2 | 7.5 | 8 ms | 327 ms |
| 4 | 6.6 | 14.5 | 3.5 | 2.3 | 8 ms | 484 ms |
| 8 | 6.1 | 15.9 | 3.1 | 2.1 | 10 ms | 572 ms |

Scaling flattens past 4 workers on this shared VM. Process start-up is in the first stage. With the face tools
models, `detect_faces` ≈ 120 ms per group member (YuNet at 1024 plus `load_full`), eyes ≈ 7 ms, face
quality ≈ 14 ms (one synthetic-face run).

## Real-event runs (work bucket, x86 dev box, 6 workers)

Event E09 (Birthday, 661 photos, 2 cameras, frsInfo faces, face models). Times are from this box to
ap-south-1 S3 (≈ 450 ms per GET; Fargate in-region is ~10× lower):

| run | wall s | img/s | keep / reject / review | S3 GET / PUT / HEAD / LIST |
|---|---|---|---|---|
| before (first run, main) | 201 | 3.3 | 312 / 42 / 307 | not counted; ingest 164 s |
| after, warm bucket proxies, cold disk | 56 | 11.8 | 609 / 42 / 10 | 1414 / 13 / 0 / 3 |
| after, fully cold (`CULL_RECOMPUTE_PROXIES=1`) | 59 | 11.2 | 609 / 42 / 10 | 1322 / 674 / 0 / 3 |

The 10 reviews: 9 are burst runners-up with low confidence or group-eyes, 1 is `EXPOSURE_UNDER`. I/O changes:
one LIST each of `raw/`, `proxy/`, `exif/` (no per-photo HEAD); all GETs on one 64-thread pool
(`CULL_IO_THREADS`) that starts pulling images while ingest reads the manifest and EXIF; group members'
full images prefetched before face detection; proxy JPEGs returned to the parent and uploaded on a
32-thread background pool (`CULL_IO_THREADS_PUT`); contact sheets rendered concurrently. `summary.json`
carries `s3_{get,put,head,list}_count` and bytes. Contact sheet p50 went from 461 to 100 ms, and
`faces_load_full` from 344 to 74 ms.

Event E07 (Engagement studio burst shoot, 1000 photos, 785 in bursts up to 75 frames; deployed run vs this branch):

| run | keep / reject / review | human questions | failures | EXPOSURE_OVER | wall s |
|---|---|---|---|---|---|
| deployed (main before fix) | 292 / 258 / 450 | 686 | 83 (NoSuchKey) | 159 | — |
| this branch | 301 / 624 / 75 | 98 (≤ 1 per group, 171 groups) | 0 | 0 | 63 |

Root causes: (1) P5 was applied per runner-up, so every non-best frame of a near-identical burst had margin
≈ 0 and was escalated; it is now one question per group about the KEEP slot (`tied_candidates`, ≤ 3) and
runners-up are confident rejects. (2) The manifests carry `original_key` on every row but the sync copied no
originals for E07, so policy-2 escalation requested a missing object and botocore `NoSuchKey` escaped as a
photo failure; originals now resolve from one LIST of `originals/<ev>/` and missing keys are treated as
absent. (3) White studio backdrops tripped the global over rule although faces had a median of 1.4 % clipped
pixels; `EXPOSURE_OVER` now needs a clipped subject and a frame far above the event median.

Group sizes on E09 are {2: 49, 3: 1}. The thresholds are not over-splitting: the manifest's
`same_second_bursts` has 57 pairs and no larger bursts. Consecutive same-camera frames 2–3 s apart have
median pHash 25 (p10 17), and frames 4–10 s apart median 29 (p10 20), so a higher pHash cutoff would merge
unrelated frames. This photographer shot single frames.

## Decisions and deviations

* **Sharpness label.** The Laplacian vote has 3 states. A borderline Laplacian always labels `borderline`
  (the policy-2 trigger). Otherwise Laplacian and FFT decide when they agree; when they disagree BRISQUE
  breaks the tie (the plan's majority of votes), or the label is `borderline` without the model.
  Confidence = 0.5·clip(|lap−90|/90, 0.3, 0.95) + 0.5·vote agreement.
* **Policy 2 extension.** A `blurry` full frame with faces is also re-scored on the face ROI (not only
  `borderline`): a bokeh portrait's soft background can push full-frame lap_var under 60.
* **P1.** Singletons use roi=saliency. Group members use roi=full, plus roi=faces when faces exist so the
  best pick compares like with like (the most specific ROI common to every member).
* **Best pick.** Plan formula and weights. Sharpness is min-max normalised on log1p(lap_var) within the
  group, and the spread is scaled by min(1, ln(max/min)/ln 4), so near-equal frames are decided by
  eyes / face quality rather than a 25 % lap_var wobble.
* **Group eyes (C08).** In a frame with ≥3 faces, `REVIEW_POLICY_GROUP_EYES` (review, never reject) fires
  when ≥2 non-main faces are closed, or when the closed ones are ≥1/3 of the detected non-main faces
  (compared in integers). Unknown eye states never count as closed. A closed main subject is still
  `EYES_CLOSED`. The old rule fired only for exactly one closed face. Scope: the defect reaches singletons and
  rejected group runners-up (as a secondary code). `decide_group` writes only `KEEP_BEST_OF_GROUP` for a group
  keeper, so a keeper with several closed faces (the registry's E03 9-face frame) still passes. That part of
  C08 is open.
* **Byte-identical copies (C09/C22).** Before ranking decisions, each md5 class in a group collapses to one
  representative: the keeper if it is in the class, else the class's highest-ranked copy. Every other copy is
  `reject` with `DUPLICATE_OF:<representative>` and confidence 1.0, never `needs_approval`, and never a tied
  candidate. Identical files cannot get different verdicts, and the reviewer is never asked to choose between them.
* **Bursts (P5 per group).** One human question per group at most: keeper vs the strongest distinct
  alternative within 0.05 (or a frame with a merit the keeper lacks: main subject's eyes open vs closed,
  more subject-sized faces). The group records `tied_candidates` (keeper + ≤ 2) and `approval_reasons`;
  only the asked alternative is `review`. Every other member is rejected: `DUPLICATE_OF` / `BURST_NOT_BEST`
  at 0.95 when near-identical to the keeper (pHash ≤ 2, or score gap ≤ 0.01 with the same eye states), else
  0.55 + 2·gap floored at 0.8. `escalation_count` counts questions, not frames.
* **Keeper, margin, duplicates.** The keeper is the top-ranked member with no gated reject defect (P4: the
  top one is still kept if all fail). Margin/tie are measured against the best member that is not a
  duplicate of the keeper. `DUPLICATE_OF:<id>` = md5 twin, or pHash ≤ 2 **and** score gap < 0.05 (pHash is
  blind to blur, so a blurred copy is `BURST_NOT_BEST` + `BLUR_*`). Other members get `BURST_NOT_BEST` with
  confidence 0.55 + 2·gap. The runner-up of an escalated group is `review`.
* **Exposure.** `over` → reject; `low_contrast` → review. The plan's `under` rule (clip_lo > 40 % and
  mean_L < 45) is now a **soft label** kept in `scores.exposure.label` for the UI. `EXPOSURE_UNDER` (review;
  reject if faces are present but unreadable) needs `under_flag`. That flag requires the soft label AND a dark
  subject (brightest face box mean L < 40, or, without faces, frame p95 L < 45 with flat highlights) AND the
  frame being dark for its event (mean_L < 0.5 × event median, skipped for single-photo tool calls). On the
  first real event (indoor birthday, 661 frames, event median mean_L 44) the plain rule sent 307 frames
  (46 %) to review although the face-box median L was 106. With the new rule 1 frame is flagged (face
  L 38). Near-uniform frames (std_L < 6, entropy < 2.5 bits, no faces) → `NO_SUBJECT`. `clip_*_pct` are
  fractions 0–1.
* **P5.** Any reject with confidence < 0.8 becomes review + `REVIEW_LOW_CONFIDENCE`.
* **Capture order.** clicked_at (second-granular) + EXIF sub-second when present (rich sidecar
  `exifToolData.SubSecCreateDate`; only 3/12 events have it) + natural filename order + photo_id. Same-second
  frames always fall inside the cluster window.
* **Faces without the face tools.** If `keepframe.tools.faces` is not importable, group members use frsInfo boxes
  from the manifest (ratios of the processed image), eyes are unknown and the dark-and-unreadable reject
  is disabled. `frs_status == "no_faces"` means no people (mfaces is null in real data).
* **OpenCV 5.0.0 API note.** `cv2.estimateAffinePartial2D` rejects every `USAC_*` method ("Unknown or
  unsupported robust estimation method"); `cv2.estimateAffine2D` accepts `USAC_MAGSAC`. The optional SSIM
  refinement (`cluster.refine_enabled`, off by default) uses `estimateAffine2D(USAC_MAGSAC)` and falls back
  to partial + RANSAC.
* pHash is not crop-invariant: ~2 % border crops stay ≤ 10 bits, 3 % can exceed it (unit-tested).
* BRISQUE (trained on natural images) scores flat synthetic graphics as poor (≈ 100); unit tests avoid
  relying on it, and real-photo thresholds must be tuned on the labelled set (§8).

All thresholds: `config/thresholds.yaml` (its sha256 is in every summary). Reason codes:
`config/reason_codes.yaml`.

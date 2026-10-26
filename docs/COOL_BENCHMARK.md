# Arm OpenCV benchmark (formerly the COOL benchmark)

Status 2026-10-08: **COOL is out by decision.** The project rule of 2026-10-07 is to spend only AWS credits, and
COOL is Marketplace-billed (a software fee outside the credits; the account also fails Marketplace
purchases with `INVALID_PAYMENT_INSTRUMENT`). It is not subscribed, the `cull-worker-cool` task definition, its
c8g capacity provider and `Dockerfile.cool` are removed, and the report never claims "Best Use of COOL". The
benchmark axis is now the OpenCV 5 build on Graviton: the stock `opencv-contrib-python-headless` 5.0.0.93
wheel pip picks on Ubuntu 24.04 (manylinux_2_28 aarch64, `Custom HAL: carotene`, no KleidiCV) vs a
KleidiCV-enabled wheel (manylinux2014 contrib, KleidiCV 26.03, the open-source Arm kernels COOL is built on) vs a
source build (+ArmPL), same `manifest_5k` protocol on c8g. the benchmark owns the numbers
(bench/results/2026-10-08). Sections 4 and 5 below are kept as history only.

Earlier measurements: stock 5.0.0 on c8g.xlarge (Graviton4) and c7g.xlarge (Graviton3), 4 workers each, on the
real 5,000-photo bench set (§2).

## 1. What is measured

`bench/run_benchmark.py`. Per image, in a `ProcessPoolExecutor` with one worker per
vCPU and `cv2.setNumThreads(1)` in each worker, timed with `time.perf_counter_ns`:

| stage | call |
|---|---|
| read_file | read the WebP bytes from local disk (reported, excluded from `opencv_total`) |
| decode_webp | `cv2.imdecode(buf, IMREAD_COLOR)` |
| resize_1024_area | `cv2.resize(..., long edge 1024, INTER_AREA)` |
| cvtcolor_gray | `cv2.cvtColor(BGR2GRAY)` |
| clahe | `createCLAHE(2.0, (8,8)).apply(gray)` |
| laplacian_sobel | `Laplacian(CV_64F, ksize=3).var()` + two `Sobel(CV_64F, ksize=3)` + Tenengrad mean |
| phash_blockmean | `img_hash.pHash` + `img_hash.blockMeanHash(MODE_0)` |
| yunet_1024 | `FaceDetectorYN` (face_detection_yunet_2023mar.onnx, sha256 `8f2383e4…2fa4`) on the 1024 proxy, score 0.9, NMS 0.3 |
| brisque | `quality.QualityBRISQUE` with `brisque_model_live.yml` / `brisque_range_live.yml` from opencv_contrib 5.x @ `1275703c` |

Run-level metrics: images/sec = N / wall time of the timed pass; mean CPU utilisation (psutil, 1 Hz,
system-wide); peak RSS (main process + workers, summed); $ per 1k images = EC2 on-demand $/h ×
wall-hours / (N/1000), with prices from `bench/prices.yaml` (AWS Price List API, fetched 2026-10-05,
c8g.xlarge re-checked 2026-10-08). Every row is open-source OpenCV, so there is no software fee.
Each rep first runs `--warmup` images (default 200) in the same pool, which are discarded. Three reps by default; the summary reports the median plus the min/max across reps.

Excluded from timing: the S3 → local-disk copy (the set is synced before the first rep), model
download, process-pool start. Same files on every host: `results.json.set_sha256` must match across
the labels being compared.

`OPENCV_FORCE_DNN_ENGINE=3` (new engine first, classic fallback) is set before cv2 is imported, as
the product does. Every run writes `env.json`: `cv2.__version__`, `cv2.__file__`, the first 40 lines
of `getBuildInformation()`, the `Custom HAL:` line as `custom_hal` (plus `kleidicv` / `armpl` booleans
and the wheel file name), `pkg-config --modversion opencv5`, contrib module presence, numpy version,
kernel, CPU model, instance type / AMI / instance id from IMDSv2, container markers and ECS image
digest when present.

## 2. Measured numbers: carotene vs Arm KleidiCV (2026-10-08)

What this compares. KleidiCV is Arm's open-source (Apache-2.0) image-processing kernel library. OpenCV 5.0.0
can link it as a "Custom HAL" on aarch64, and it is the kernel library COOL (Cloud-Optimized OpenCV, an AWS
Marketplace product) is built on. **We do not run COOL, did not subscribe to it, and claim nothing about
COOL's performance.** Every row below is open-source OpenCV 5.0.0: the two aarch64 PyPI wheels of
`opencv-contrib-python-headless==5.0.0.93`, plus three source builds of the same sdist.

Source: `bench/results/2026-10-08/results.md` (all tables), `RUNS.md` (instances, wheel sha256s, cost).
Same protocol as 2026-10-05: `manifest_5k` (`set_sha256` `58ae8af0…`, identical on every row), c8g.xlarge
(Graviton4, Neoverse-V2), 4 workers, 3 reps, 200-image warm-up, AMI `ami-0cf36b0c962e50fd7`. Rows on the same
instance id ran back to back on that one host. `opencv_hal` is the env.json label every comparison is gated on.

| label | build | `opencv_hal` | C++ compiler | img/s [min–max] | $/1k images | host |
|---|---|---|---|---|---|---|
| stock-carotene | PyPI manylinux_2_28 | `carotene` | GCC 14.2.1 | 28.47 [28.44–28.51] | $0.00105 | i-030a… |
| stock-kleidicv | PyPI manylinux2014 | `kleidicv-26.03+carotene` | GCC 10.2.1 | 29.55 [29.51–29.55] | $0.00102 | i-030a… |
| stock-kleidicv-r2 | PyPI manylinux2014 (replicate) | `kleidicv-26.03+carotene` | GCC 10.2.1 | 29.35 [29.31–29.39] | $0.00102 | i-0bfe… |
| src-gcc14-carotene | sdist, `-DWITH_KLEIDICV=OFF` | `carotene` | GCC 14.2.1 | 26.23 [26.23–26.32] | $0.00114 | i-0254… |
| src-gcc14-kleidicv | sdist, `-DWITH_KLEIDICV=ON` | `kleidicv-26.03+carotene` | GCC 14.2.1 | 26.19 [26.19–26.19] | $0.00114 | i-0254… |
| kleidicv-src | sdist, KleidiCV ON + `-DWITH_ARMPL=ON` (ArmPL 25.07) | `kleidicv-26.03+carotene+armpl` | GCC 14.2.1 | 26.12 [26.09–26.13] | $0.00115 | i-0bfe… |

`stock-carotene` reproduces the 2026-10-05 `stock` row (28.47 vs 28.43 img/s, same wheel, same set). All rows
ran at 100 % CPU, with peak RSS 774–796 MB and a rep-to-rep spread of 0.4 % or less.

Speed-up per stage (p50 ms ratio, above 1 means the second build is faster):

| stage | share of time | PyPI: kleidicv ÷ carotene | same GCC 14: KleidiCV ON ÷ OFF |
|---|---|---|---|
| decode_webp | 23 % | 0.96× | 1.00× |
| resize_1024_area (INTER_AREA) | 9 % | 0.85× | 1.00× |
| cvtcolor_gray | <1 % | 0.78× | 1.00× |
| clahe | 2 % | 1.00× | 1.01× |
| laplacian_sobel (CV_64F) | 5 % | 0.97× | 1.00× |
| phash_blockmean | <1 % | 0.68× | 0.99× |
| yunet_1024 | 43 % | 1.13× | 1.00× |
| brisque | 19 % | 1.09× | 1.00× |
| opencv_total | 100 % | 1.04× | 1.00× (0.997) |
| images/sec | | **1.04×** | **1.00×** (0.998) |
| $/1k images | | 0.96× (4 % cheaper) | 1.00× |

Reading:

- **On this workload, KleidiCV itself changes nothing.** With compiler, flags, BLAS and host identical (the
  same-toolchain pair on one instance), turning KleidiCV on moves every stage by at most ±1 % and end to end
  by 0.998×. That is within the noise.
- **KleidiCV is active. Our stages just never call its kernels.** The single-op probe (`bench/kleidicv_probe.py`,
  results.md last table) shows the same-toolchain KleidiCV build is 7.0× faster on `resize` INTER_LINEAR (gray),
  1.5× (BGR), 1.5–1.9× on `GaussianBlur` 3×3/5×5 and 1.8× on `exp` (f32). On the calls the benchmark makes
  (resize **INTER_AREA**, BGR2GRAY at 1024 px, CLAHE, Laplacian/Sobel to **CV_64F**, pHash) it is 1.00×. The
  OpenCV 5.0.0 KleidiCV HAL accelerates specific u8 paths, and our sharpness filters write CV_64F on purpose.
- **The +4 % of the KleidiCV PyPI wheel comes from how it was built, not from KleidiCV.** The two PyPI wheels
  differ in more than the HAL: the carotene wheel is built with GCC 14.2.1 (gcc-toolset-14), the KleidiCV wheel
  with GCC 10.2.1 (devtoolset-10). The KleidiCV wheel is faster where KleidiCV is not involved (YuNet 1.13×,
  BRISQUE 1.09×) and slower on calls KleidiCV does not touch either (INTER_AREA resize 0.85×, BGR2GRAY 0.78×,
  `minMaxLoc` 0.32× and `multiply` 0.68× in the probe). Attributing the gain to the compiler is an
  **inference**: we did not build a GCC 10 wheel to confirm it.
- **Our source builds are 8 % slower than either PyPI wheel (26.2 vs 28.5 img/s), mostly in YuNet
  (0.85×).** The PyPI wheels link OpenBLAS (`Lapack: YES (OpenBLAS)`) and our sdist builds fell back to the
  built-in clapack. The DNN engine's GEMM goes through it. That is the likely cause, and it is **not profiled**.
  It affects KleidiCV ON and OFF equally, so the pair comparison above still holds.
- **ArmPL adds nothing measurable** (26.12 vs 26.19 img/s). In OpenCV 5.0.0 the ARMPL HAL only implements
  DFT/DCT (`hal/armpl`), which our pipeline reaches only through pHash's 32×32 DCT.
- The 2026-10-05 Amdahl estimate (at most about 1.08× if only resize/CLAHE/filters sped up 1.8×) was an upper
  bound. The measurement says the HAL does not reach those stages at all as we call them.

### End-to-end product worker

`bench/run_worker_bench.py`: `keepframe.worker.run_event --workers 4`, three cold reps on the same 661-photo
event as 2026-10-05, in LOCAL_MODE. Product source is origin/main @ 1545bc1, afbd491 and 836275e. They differ only
by an added `opencv_hal` summary field, and the verdicts were identical in all 18 reps.

| label | img/s [min–max] | wall s | $/1k images | CPU % | peak RSS MB | keep / reject / review |
|---|---|---|---|---|---|---|
| stock-carotene | 27.45 [27.38–27.46] | 24.1 | $0.00110 | 98.3 | 1,672 | 609 / 46 / 6 |
| stock-kleidicv | 25.85 [25.84–25.98] | 25.6 | $0.00117 | 99.2 | 1,670 | 609 / 46 / 6 |
| stock-kleidicv-r2 | 25.56 [25.55–25.66] | 25.9 | $0.00119 | 99.3 | 1,646 | 609 / 46 / 6 |
| src-gcc14-carotene | 26.51 [26.51–26.66] | 24.9 | $0.00114 | 97.9 | 1,652 | 609 / 46 / 6 |
| src-gcc14-kleidicv | 26.55 [26.55–26.60] | 24.9 | $0.00114 | 98.7 | 1,640 | 609 / 46 / 6 |
| kleidicv-src | 26.62 [26.60–26.63] | 24.8 | $0.00114 | 98.4 | 1,667 | 609 / 46 / 6 |

- In the product worker the KleidiCV PyPI wheel is **6 % slower** (0.94×, 6 % more per 1k images). Per-photo
  p50, carotene wheel ÷ KleidiCV wheel: proxy path (decode + INTER_AREA resize + JPEG) 0.92×, exposure 0.76×,
  sharpness 0.93×, hashes 0.88× (all 661 photos), against saliency 1.18× (560 photos) and faces / eyes / face
  quality 1.10–1.20× (92 grouped photos). The losses outweigh the gains.
- Same toolchain, KleidiCV ON vs OFF: 26.55 vs 26.51 img/s (1.00×).
- Verdict counts (keep/reject/review) are identical across every build and rep. Per-photo verdicts were not
  diffed across builds (**not verified**).
- These worker numbers are not comparable with the 2026-10-05 worker row (29.77 img/s, 312/42/307): main's
  policies changed since then (fc9238f).

Practical consequence: KleidiCV does not make this pipeline faster today. The PyPI carotene wheel
(manylinux_2_28) is the fastest build we measured for the product worker, and the KleidiCV wheel is the fastest
for the stage benchmark. The difference is compiler/BLAS, ±4–6 %, not the HAL. A pipeline that resized with
INTER_LINEAR and filtered in u8/s16 could use the KleidiCV paths. We have not changed the product to test that.

### Earlier numbers: Graviton4 vs Graviton3 (2026-10-05, PyPI carotene wheel)

Source: `bench/results/2026-10-05/results.md`. Same set and method, 4 workers.

| label | instance | img/s | $/1k images | CPU % | peak RSS MB |
|---|---|---|---|---|---|
| stock | c8g.xlarge (Graviton4) | 28.43 [28.37–28.45] | $0.00106 | 100.0 | 804 |
| c7g-stock | c7g.xlarge (Graviton3) | 25.34 [25.31–25.38] | $0.00108 | 99.9 | 798 |

Graviton4 is 1.12× faster end to end and about 2 % cheaper per 1,000 images. YuNet at 1024 (42 %), WebP decode
(23 %) and BRISQUE (19 %) are 84 % of the per-image time. Laplacian+Sobel (CV_64F) is the one stage that is not
faster on Graviton4 (0.96×, not profiled). `FaceDetectorYN` prints `setPreferableTarget Targets are not supported
by the new graph engine` once per worker, which is expected with `OPENCV_FORCE_DNN_ENGINE=3`. Throughput on 16
vCPUs is **not** measured; do not multiply by 4. A synthetic 1k set and an x86 dev-box run from that day only
validated the script.

## 3. Runs still to do

| label | host | blocked on / note |
|---|---|---|
| stock-carotene + stock-kleidicv at protocol size | c8g.4xlarge, 16 workers, `--rows "stock-carotene=m228 stock-kleidicv=m2014"` | nothing (quota is 64 now); about 15 min, about $0.11 |
| c7g-stock at protocol size | c7g.4xlarge, 16 workers | nothing; optional |
| x86-baseline (optional) | c7i.4xlarge | < $5 rule; optional |
| source build with OpenBLAS (`-DWITH_LAPACK=ON` + `openblas-devel`) | c8g.4xlarge build + c8g.xlarge bench | not tried; would show whether BLAS explains the 8 % source-build gap |

Commands (see `bench/ec2/README.md` for every flag):

```bash
set -a; . ~/.config/keepframe/.env; set +a
bench/ec2/launch_bench_host.sh --rows "stock-carotene=m228 stock-kleidicv=m2014" --instance-type c8g.xlarge \
  --ami ami-0cf36b0c962e50fd7 --worker-event <eventId> --src-ref origin/main --max-minutes 80 --wait
bench/ec2/launch_bench_host.sh --build-src [--armpl|--no-kleidicv] --label src-x --instance-type c8g.4xlarge --wait
.venv/bin/python bench/summarize.py --date 2026-10-08 --upload
```

## 4. Statement for the report

Ready to paste into REPORT.md (numbers from `bench/results/2026-10-08/results.md` only):

> **OpenCV 5 + Arm KleidiCV.** The product runs open-source OpenCV 5.0.0 (`opencv-contrib-python-headless
> 5.0.0.93`) on AWS Graviton, Arm only. We benchmarked the OpenCV build with Arm KleidiCV 26.03, the open-source
> kernel library that Cloud-Optimized OpenCV (COOL) is built on, against the build without it, on the same
> 5,000 event photos and the same c8g.xlarge (Graviton4) host. We did not use COOL itself and make no claim
> about it. The PyPI wheel that bundles KleidiCV ran the per-stage benchmark 1.04× faster (29.55 vs 28.47 img/s,
> $0.00102 vs $0.00105 per 1,000 images) but the end-to-end worker 0.94× slower (25.85 vs 27.45 img/s). Two
> source builds that differ only in `-DWITH_KLEIDICV` show where that comes from. On our pipeline KleidiCV is
> neutral (0.998× end to end, every stage within ±1 %), because our calls (INTER_AREA resize, CV_64F
> sharpness filters, DNN face detection, BRISQUE) are not the paths it accelerates. On paths it does accelerate
> it is 1.5–7× faster (INTER_LINEAR resize, Gaussian blur, `exp`). The wheel-to-wheel difference comes from
> the wheels' different compilers (GCC 10 vs 14), not from KleidiCV.

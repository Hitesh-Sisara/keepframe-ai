# Runs on 2026-10-08

All hosts: ap-south-1, AL2023 arm64 `ami-0cf36b0c962e50fd7` (same AMI as 2026-10-05), launched by
`bench/ec2/launch_bench_host.sh`, self-terminated (shutdown-behaviour terminate); no instances or volumes left
(checked with the two `describe-*` commands in `bench/ec2/README.md`). Minutes are RunInstances (CloudTrail) to the
host log upload; costs are **estimates** from on-demand per-second billing (`bench/prices.yaml`), EBS (30 GiB gp3)
under $0.01 in total. EC2 Standard vCPU quota was 64 by then, so build hosts ran in parallel with bench hosts.

| run_id | label folder(s) | instance | what | min | est. EC2 $ |
|---|---|---|---|---|---|
| 20261008T035042Z-stock-carotene | stock-carotene, stock-kleidicv | c8g.xlarge i-030a0098dfda7a05b | both PyPI wheels back to back: stage bench 5k x3 + worker bench x3 each | 22.6 | 0.041 |
| 20261008T035157Z-kleidicv-src | (wheel only) | c8g.4xlarge i-007fa502fa51c0971 | sdist build, KleidiCV ON + ArmPL 25.07, GCC 14.2.1, 513 s | 11.5 | 0.083 |
| 20261008T040643Z-kleidicv-src | kleidicv-src, stock-kleidicv-r2 | c8g.xlarge i-0bfeea4403e93a83c | source build (KleidiCV+ArmPL) + PyPI KleidiCV wheel as a second-host replicate | 23.2 | 0.042 |
| 20261008T041624Z-src-carotene | (wheel only) | c8g.4xlarge i-086160566fe416865 | sdist build, KleidiCV OFF, GCC 14.2.1, 468 s | 8.6 | 0.062 |
| 20261008T041627Z-src-kleidicv | (wheel only) | c8g.4xlarge i-0a6ddfcf53de42e81 | sdist build, KleidiCV ON, no ArmPL, GCC 14.2.1, 497 s | 9.0 | 0.065 |
| 20261008T043223Z-src-gcc14-carotene | src-gcc14-carotene, src-gcc14-kleidicv | c8g.xlarge i-0254d248ad15c956a | same-toolchain pair: stage bench + worker bench each | 24.1 | 0.043 |
| 20261008T050014Z-probe-stock-carotene | probe-* (4) | c8g.xlarge i-01a46d31f79b0d1fc | `bench/kleidicv_probe.py` single-op probe on all four builds | 0.8 | 0.001 |

Total ≈ 71 c8g.xlarge-minutes + 29 c8g.4xlarge-minutes ≈ **$0.34** (estimate).

Wheels (opencv-contrib-python-headless 5.0.0.93, sha256-checked before install on every host):

| build | file | sha256 | Custom HAL | C++ compiler | LAPACK |
|---|---|---|---|---|---|
| PyPI carotene | `…-cp37-abi3-manylinux_2_28_aarch64.whl` | `01331ae1a65a21d81310b5584900f73c3012ab4bdcf6aa26085c1870eea45da8` | carotene | GCC 14.2.1 (gcc-toolset-14) | OpenBLAS |
| PyPI KleidiCV | `…-cp37-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl` | `c035e12b6078b3351ad577e90a4ff4ead74155fa5470eb8df8f5b54a8ce7727f` | carotene + KleidiCV 26.03 | GCC 10.2.1 (devtoolset-10) | OpenBLAS |
| src KleidiCV OFF | `s3://…/bench/_wheels/20261008T041624Z-src-carotene/…-cp312-cp312-linux_aarch64.whl` | `a1ab279c1b1a3a6915d76fcbd6702c445df222773483de63786c82a6b0cc2b5e` | carotene | GCC 14.2.1 (AL2023 gcc14) | built-in clapack |
| src KleidiCV ON | `s3://…/bench/_wheels/20261008T041627Z-src-kleidicv/…` | `3a717c6c68f266b8f5f9363751680731c3a1a4bf58a767727748fcb0a5b4c695` | carotene + KleidiCV 26.03 | GCC 14.2.1 | built-in clapack |
| src KleidiCV + ArmPL | `s3://…/bench/_wheels/20261008T035157Z-kleidicv-src/…` | `34e3a5edfd962fe23881f8f328d5cbf815fca46615d5aca1974869d2b54b2b33` | carotene + KleidiCV 26.03 + ARMPL | GCC 14.2.1 | built-in clapack |

Source builds: sdist `opencv_contrib_python_headless-5.0.0.93.tar.gz` sha256
`6a8c34de905f59b038f6ab0475e73fb0a0f345c3582ec3aeaeb1c0912e80cddb`, `ENABLE_CONTRIB=1 ENABLE_HEADLESS=1`,
`CMAKE_ARGS="-DPYTHON3_LIMITED_API=ON -DWITH_KLEIDICV=ON|OFF [-DWITH_ARMPL=ON -DARMPL_ROOT_DIR=/opt/arm/armpl_25.07_gcc]"`.
KleidiCV 26.03 is fetched by OpenCV's CMake from gitlab.arm.com (md5 pinned in `hal/kleidicv/kleidicv.cmake`). ArmPL
25.07 is the free `arm-performance-libraries_25.07_rpm_gcc.tar` from developer.arm.com (serial `armpl_lp64`, no
OpenMP). Each `build_info.json` (full `getBuildInformation()`) and `build.log.gz` sit next to the wheel in S3.

The `opencv_hal` / `cv2_custom_hal` / `cv2_cpu_*` keys in env.json of the rows run before the merge of main
(4a6b721) were backfilled from the recorded build lines with `run_benchmark.cv2_hal()` (`opencv_hal_derived` says so).
The worker bench used product source origin/main @ 1545bc1 (A/B host), @ afbd491 (kleidicv-src host) and @ 836275e
(pair host). Between them only `opencv_hal` was added to the run summary (`run_event.py`, `run_record.py`); no compute
code changed, and the verdicts are identical in all 18 worker reps (keep/reject/review 609/46/6). The worker numbers
are therefore comparable across these rows, but not with the 2026-10-05 `stock` worker row (main @ fc9238f, older
policies: 312/42/307).

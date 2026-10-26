#!/usr/bin/env bash
# Runs ON the bench host (launched by bench/ec2/launch_bench_host.sh, which writes host.env and ships this
# file in the bundle). Two modes:
#   rows (default): for each `label|wheel-url|sha256` in ROW_SPECS, create /opt/venv-<label>, install that
#     exact OpenCV wheel (sha256-checked) + pinned deps, run bench/run_benchmark.py and (with WORKER_EVENT)
#     bench/run_worker_bench.py, upload bench/results/<date>/<label>/. Rows run back to back on one host.
#   BUILD_SRC=1: build the pinned opencv-contrib-python-headless sdist with -DWITH_KLEIDICV=$KLEIDICV (default ON)
#     (+ -DWITH_ARMPL=ON when ARMPL=1) and upload the wheel to s3://$BUCKET/bench/_wheels/<run_id>/.
# The caller's EXIT trap uploads the log and powers off (=> terminate).
# shellcheck disable=SC2153  # REPS, WARMUP, ... come from host.env
set -uo pipefail
# shellcheck disable=SC1090  # host.env is written by the launcher
. "$1"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" HOME=/root
export UV_PYTHON_INSTALL_DIR=/opt/uv-python UV_CACHE_DIR=/tmp/uv-cache
AWSCLI="$(command -v aws)"
date -u; uname -a
cd /opt/bench || exit 9

curl -sSfL -o /tmp/uv.tgz "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${UVARCH}-unknown-linux-gnu.tar.gz"
echo "${UV_SHA}  /tmp/uv.tgz" | sha256sum -c - || exit 10
tar -xzf /tmp/uv.tgz -C /tmp && install -m 0755 "/tmp/uv-${UVARCH}-unknown-linux-gnu/uv" /usr/local/bin/uv
uv python install 3.12 || exit 10
# libgfortran: runtime dependency of a wheel linked against Arm Performance Libraries (harmless otherwise)
command -v dnf >/dev/null && dnf -y -q install libgfortran >/dev/null 2>&1

DEPS=(numpy==2.3.3 psutil==7.2.2 pyyaml==6.0.3 boto3==1.43.108 pydantic==2.12.3)

fetch_checked() {  # url-or-s3 sha256 dest
  local src="$1" want="$2" dst="$3"
  if [[ "$src" == s3://* ]]; then "$AWSCLI" s3 cp --only-show-errors "$src" "$dst"; else curl -sSfL -o "$dst" "$src"; fi
  echo "${want}  ${dst}" | sha256sum -c - || return 1
}

# ------------------------------------------------------------------ source build
if [[ "${BUILD_SRC}" == 1 ]]; then
  dnf -y -q install gcc gcc-c++ make cmake git patchelf gcc-gfortran >/dev/null 2>&1 || dnf -y install gcc gcc-c++ make cmake git
  # AL2023 ships GCC 11 as the default and GCC 14 as gcc14-*; prefer 14 (closer to the PyPI wheels' toolset)
  if dnf -y -q install gcc14 gcc14-c++ >/dev/null 2>&1 && command -v gcc14-gcc >/dev/null; then
    export CC=gcc14-gcc CXX=gcc14-g++
  fi
  "${CC:-gcc}" --version | head -1; cmake --version | head -1
  uv venv --seed --python 3.12 /opt/venv-build
  fetch_checked "$SDIST" "$SDIST_SHA" /tmp/opencv-sdist.tar.gz || exit 20
  CM="-DPYTHON3_LIMITED_API=ON -DWITH_KLEIDICV=${KLEIDICV:-ON}"
  if [[ "${ARMPL}" == 1 ]]; then
    T0=$(date +%s); ARMPL_ROOT=""
    if curl -sSfL -o /tmp/armpl.tar "$ARMPL_URL"; then
      sha256sum /tmp/armpl.tar
      mkdir -p /tmp/armpl && tar -xf /tmp/armpl.tar -C /tmp/armpl
      INST="$(find /tmp/armpl -maxdepth 3 -name 'arm-performance-libraries_*.sh' | head -1)"
      echo "ArmPL installer: $INST"
      # -a: accept the Arm Performance Libraries EULA (free licence), -i: install prefix
      if bash "$INST" -a -i /opt/arm >/tmp/armpl-install.log 2>&1; then
        ARMPL_ROOT="$(find /opt/arm -maxdepth 1 -type d -name 'armpl_*_gcc*' | head -1)"
      else
        tail -20 /tmp/armpl-install.log
      fi
    fi
    if [[ -n "$ARMPL_ROOT" ]]; then
      echo "ArmPL root: $ARMPL_ROOT ($(( $(date +%s) - T0 ))s)"; ls -l "$ARMPL_ROOT"/lib/libarmpl_lp64.* || true
      CM="$CM -DWITH_ARMPL=ON -DARMPL_ROOT_DIR=$ARMPL_ROOT"
    else
      echo "ArmPL install failed; building KleidiCV only"; ARMPL=0
    fi
  fi
  build_wheel() {  # cmake-args log -> rc
    rm -rf /opt/wheel && mkdir -p /opt/wheel
    echo "CMAKE_ARGS=$1 CC=${CC:-} CXX=${CXX:-}"
    ENABLE_CONTRIB=1 ENABLE_HEADLESS=1 CMAKE_ARGS="$1" CMAKE_BUILD_PARALLEL_LEVEL="$(nproc)" MAKEFLAGS="-j$(nproc)" \
      /opt/venv-build/bin/python -m pip wheel --no-deps -v -w /opt/wheel /tmp/opencv-sdist.tar.gz >"$2" 2>&1
  }
  T0=$(date +%s)
  build_wheel "$CM" /tmp/build.log; rc=$?
  if [[ $rc != 0 && "${ARMPL}" == 1 ]]; then
    echo "build with ARMPL failed rc=$rc; tail:"; tail -30 /tmp/build.log
    mv /tmp/build.log /tmp/build-armpl-failed.log
    gzip -c /tmp/build-armpl-failed.log | "$AWSCLI" s3 cp --only-show-errors - "s3://${BUCKET}/bench/_wheels/${RUN_ID}/build-armpl-failed.log.gz"
    CM="-DPYTHON3_LIMITED_API=ON -DWITH_KLEIDICV=${KLEIDICV:-ON}"
    build_wheel "$CM" /tmp/build.log; rc=$?
  fi
  BUILD_S=$(( $(date +%s) - T0 ))
  echo "build rc=$rc in ${BUILD_S}s"
  grep -E "KleidiCV|KLEIDICV|ARMPL|ARM Perf|Custom HAL|CPU/HW|Baseline|Dispatched|C\+\+ Compiler:" /tmp/build.log | sort -u | head -40
  OUT="s3://${BUCKET}/bench/_wheels/${RUN_ID}"
  gzip -c /tmp/build.log > /tmp/build.log.gz && "$AWSCLI" s3 cp --only-show-errors /tmp/build.log.gz "$OUT/build.log.gz"
  [[ $rc == 0 ]] || { tail -60 /tmp/build.log; exit 23; }
  WHL="$(find /opt/wheel -maxdepth 1 -name '*.whl' | head -1)"
  uv venv --python 3.12 /opt/venv-check && uv pip install --python /opt/venv-check/bin/python "$WHL" numpy==2.3.3
  /opt/venv-check/bin/python -c "
import cv2, json, sys
b = cv2.getBuildInformation()
json.dump({'cv2_version': cv2.__version__, 'cv2_file': cv2.__file__, 'build_seconds': int(sys.argv[1]),
           'cmake_args': sys.argv[2], 'build_information': b.splitlines()}, open('/opt/wheel/build_info.json', 'w'), indent=1)
print([l for l in b.splitlines() if 'HAL' in l or 'ARM Perf' in l])" "$BUILD_S" "$CM"
  ( cd /opt/wheel && sha256sum ./*.whl > SHA256SUMS && cat SHA256SUMS )
  "$AWSCLI" s3 cp --only-show-errors --recursive /opt/wheel/ "$OUT/"
  echo "uploaded $OUT/"
  exit 0
fi

# ------------------------------------------------------------------ benchmark rows
"$AWSCLI" s3 cp --only-show-errors "s3://${BUCKET}/bench/_bundle/${RUN_ID}.manifest.jsonl" /opt/bench/manifest.jsonl
T0=$(date +%s); "$AWSCLI" s3 sync --only-show-errors "s3://${BUCKET}/${DATA_PREFIX}" /tmp/bench5k/
echo "s3 sync to local disk took $(( $(date +%s) - T0 ))s (not timed)"
if [[ -n "${WORKER_EVENT}" ]]; then
  mkdir -p /opt/src /tmp/wlocal/manifests
  "$AWSCLI" s3 cp --only-show-errors "s3://${BUCKET}/bench/_bundle/${RUN_ID}.src.tgz" /tmp/src.tgz && tar -xzf /tmp/src.tgz -C /opt/src
  echo "worker source commit ${SRC_COMMIT:-unknown}"
  T0=$(date +%s)
  for f in jsonl meta.json; do "$AWSCLI" s3 cp --only-show-errors "s3://${BUCKET}/manifests/${WORKER_EVENT}.$f" /tmp/wlocal/manifests/; done
  for pfx in raw exif originals; do "$AWSCLI" s3 sync --only-show-errors "s3://${BUCKET}/$pfx/${WORKER_EVENT}/" "/tmp/wlocal/$pfx/${WORKER_EVENT}/"; done
  echo "worker inputs synced in $(( $(date +%s) - T0 ))s (not timed)"
fi
NW="${WORKERS:-$(nproc)}"
M2014_SHA=c035e12b6078b3351ad577e90a4ff4ead74155fa5470eb8df8f5b54a8ce7727f
M228_SHA=01331ae1a65a21d81310b5584900f73c3012ab4bdcf6aa26085c1870eea45da8
FAIL13=0

while IFS='|' read -r -u 3 RL RURL RSHA; do
  [[ -n "$RL" ]] || continue
  echo "=== row $RL wheel=$RURL $(date -u +%FT%TZ)"
  V="/opt/venv-$RL"; PY="$V/bin/python"
  uv venv --python 3.12 "$V" || continue
  if [[ "$RURL" == pypi ]]; then
    uv pip install --python "$PY" opencv-contrib-python-headless==5.0.0.93 "${DEPS[@]}" || { echo "row $RL install failed"; continue; }
  else
    W="/tmp/wheels/$RL/$(basename "$RURL")"; mkdir -p "$(dirname "$W")"
    fetch_checked "$RURL" "$RSHA" "$W" || { echo "row $RL wheel sha256 mismatch"; continue; }
    if ! { uv pip install --python "$PY" --no-deps "$W" && uv pip install --python "$PY" "${DEPS[@]}"; }; then
      echo "row $RL install failed"; continue
    fi
  fi
  "$PY" -c "import cv2,numpy; print('cv2', cv2.__version__, cv2.__file__, 'numpy', numpy.__version__); print([l.strip() for l in cv2.getBuildInformation().splitlines() if 'Custom HAL' in l or 'ARM Perf' in l])"
  # PyPI wheel rows: same pinned file again through scripts/install_opencv_wheel.sh, which asserts the HAL
  # (KleidiCV present for m2014, absent for m228). A failed assertion skips the row and the host exits 13.
  case "$RSHA" in
    "$M2014_SHA") VAR=kleidicv;; "$M228_SHA") VAR=carotene;; *) VAR="";;
  esac
  if [[ -n "$VAR" ]]; then
    if ! CULL_CV2_WHEEL="$VAR" sh /opt/bench/scripts/install_opencv_wheel.sh "$PY" "$VAR"; then
      echo "row $RL HAL assertion failed"; FAIL13=1; continue
    fi
  fi
  export CULL_CV2_WHEEL="${VAR:-$(basename "$RURL")}"
  # single-op probe (synthetic frame, ~1 min): which calls the KleidiCV HAL reaches on this build
  mkdir -p "/opt/bench/bench/results/${DATE}/${RL}"
  "$PY" bench/kleidicv_probe.py --out "/opt/bench/bench/results/${DATE}/${RL}/probe.json" || echo "row $RL probe failed"
  if [[ "${SKIP_STAGE}" != 1 ]]; then
    "$PY" bench/run_benchmark.py --host-label "$RL" --manifest /opt/bench/manifest.jsonl --local-dir /tmp/bench5k \
      --source "s3://${BUCKET}/${DATA_PREFIX}" --workers "$NW" --reps "${REPS}" --warmup "${WARMUP}" \
      --dataset-name "${DSNAME}" --date "${DATE}" --wheel "$(basename "$RURL")" --models-dir /opt/models-bench \
      || echo "row $RL stage bench failed rc=$?"
  fi
  if [[ -n "${WORKER_EVENT}" ]]; then
    [[ -f /opt/models/.fetched ]] || { "$PY" /opt/src/scripts/fetch_models.py --lock /opt/src/config/models.lock --out /opt/models && touch /opt/models/.fetched; }
    "$PY" bench/run_worker_bench.py --host-label "$RL" --event-id "${WORKER_EVENT}" --local-root /tmp/wlocal \
      --src /opt/src --models-dir /opt/models --workers "$NW" --reps "${WORKER_REPS}" --date "${DATE}" \
      || echo "row $RL worker bench failed rc=$?"
  fi
  if [[ -d "/opt/bench/bench/results/${DATE}/${RL}" ]]; then
    "$AWSCLI" s3 cp --only-show-errors --recursive --exclude 'raw/*' --exclude 'worker/raw/*' \
      "/opt/bench/bench/results/${DATE}/${RL}/" "s3://${BUCKET}/bench/results/${DATE}/${RL}/"
  fi
done 3<<< "$ROW_SPECS"
echo "all rows done $(date -u +%FT%TZ)"
[[ $FAIL13 == 0 ]] || exit 13

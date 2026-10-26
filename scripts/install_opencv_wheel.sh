#!/bin/sh
# Force the exact opencv-contrib-python-headless 5.0.0.93 aarch64 wheel and assert its HAL (docs/CV_CORE.md
# "Arm acceleration"). Run AFTER the normal `pip install -r requirements*.txt`; it reinstalls cv2 only (--no-deps).
#
#   scripts/install_opencv_wheel.sh [PYTHON] [carotene|kleidicv]      (defaults: python, carotene)
#
# Why: on glibc >= 2.28 pip/uv prefer the manylinux_2_28 aarch64 contrib wheel, which was built WITHOUT KleidiCV
# (Custom HAL: carotene only). The manylinux2014 aarch64 contrib wheel has "carotene + KleidiCV (ver 26.03)".
#   carotene  manylinux_2_28 wheel, asserts KleidiCV is absent      (product images: faster on our workload,
#             bench/results/2026-10-08/results.md)
#   kleidicv  manylinux2014 wheel, asserts 'KleidiCV' in getBuildInformation()   (bench rows only)
# Both are pinned by sha256. Non-aarch64 hosts are a no-op (x86 keeps the uv.lock / requirements.txt wheel).
set -eu
PY="${1:-python}"
VARIANT="${2:-carotene}"
case "$(uname -m)" in
  aarch64|arm64) ;;
  *) echo "install_opencv_wheel: $(uname -m) is not aarch64, keeping the locked wheel"; exit 0;;
esac
BASE=https://files.pythonhosted.org/packages
case "$VARIANT" in
  kleidicv)
    URL="$BASE/61/14/f12a54f7e5e8783adf905bb863ef2257e41d73d44793851d318e35ee8121/opencv_contrib_python_headless-5.0.0.93-cp37-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl"
    SHA=c035e12b6078b3351ad577e90a4ff4ead74155fa5470eb8df8f5b54a8ce7727f;;
  carotene)
    URL="$BASE/51/78/11f0704e94ebf92f748c789cf5d430d4459ae3649263c712d81772cc4360/opencv_contrib_python_headless-5.0.0.93-cp37-abi3-manylinux_2_28_aarch64.whl"
    SHA=01331ae1a65a21d81310b5584900f73c3012ab4bdcf6aa26085c1870eea45da8;;
  *) echo "install_opencv_wheel: variant must be kleidicv|carotene, got $VARIANT" >&2; exit 2;;
esac
REQ="$(mktemp)"
trap 'rm -f "$REQ"' EXIT
printf 'opencv-contrib-python-headless @ %s --hash=sha256:%s\n' "$URL" "$SHA" > "$REQ"
if "$PY" -m pip --version >/dev/null 2>&1; then
  "$PY" -m pip install --no-cache-dir --no-deps --force-reinstall --require-hashes -r "$REQ"
else  # uv-created venvs have no pip
  uv pip install --python "$PY" --no-deps --reinstall-package opencv-contrib-python-headless --require-hashes -r "$REQ"
fi
"$PY" - "$VARIANT" <<'PY'
import sys

import cv2

bi = cv2.getBuildInformation()
hal = [ln.strip() for ln in bi.splitlines() if "Custom HAL:" in ln]
want = sys.argv[1] == "kleidicv"
assert ("KleidiCV" in bi) == want, bi
assert hasattr(cv2, "img_hash") and hasattr(cv2, "quality"), "contrib modules missing"
print("install_opencv_wheel:", sys.argv[1], cv2.__version__, cv2.__file__, hal)
PY

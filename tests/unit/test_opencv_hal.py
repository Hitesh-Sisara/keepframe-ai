"""opencv_hal / Custom HAL extraction (docs/CV_CORE.md "Arm acceleration").

The fixture lines are verbatim from the cv2.abi3.so of each opencv-contrib-python-headless 5.0.0.93 wheel
(aarch64 strings, 2026-10-08) and the x86_64 dev venv."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from keepframe import hal_lines, hal_short, opencv_info

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench"))
from run_benchmark import cv2_hal  # noqa: E402

ARM_2014 = """  CPU/HW features:
    Baseline:                    NEON FP16
      requested:                 DETECT
    Dispatched code generation:  NEON_DOTPROD NEON_FP16 NEON_BF16
      requested:                 NEON_FP16 NEON_BF16 NEON_DOTPROD
  Other third-party libraries:
    Custom HAL:                  YES (carotene (ver 0.0.1) KleidiCV (ver 26.03))
"""
ARM_228 = ARM_2014.replace(" KleidiCV (ver 26.03)", "")
X86 = """    Baseline:                    SSE SSE2 SSE3
    Dispatched code generation:  SSE4_1 SSE4_2 AVX FP16 AVX2 AVX512_SKX
    Custom HAL:                  YES (ipp (ver 0.0.1))
"""


@pytest.mark.parametrize(("info", "short"), [(ARM_2014, "kleidicv-26.03+carotene"), (ARM_228, "carotene"),
                                             (X86, "ipp"), ("    Custom HAL:   NO\n", "none"), ("", "none")])
def test_short_label_package_and_bench_agree(info: str, short: str) -> None:
    assert hal_short(hal_lines(info).get("Custom HAL")) == short
    assert cv2_hal(info)["opencv_hal"] == short


def test_lines_extracted_first_match_only() -> None:
    h = hal_lines(ARM_2014 + "    Baseline:   SHOULD_NOT_WIN\n")
    assert h == {"Baseline": "NEON FP16", "Dispatched code generation": "NEON_DOTPROD NEON_FP16 NEON_BF16",
                 "Custom HAL": "YES (carotene (ver 0.0.1) KleidiCV (ver 26.03))"}
    env = cv2_hal(ARM_2014)
    assert env["cv2_custom_hal"] == h["Custom HAL"] and env["cv2_cpu_baseline"] == "NEON FP16"
    assert cv2_hal("")["cv2_custom_hal"] is None  # key always present, None when the build has no such line


def test_runtime_info_has_hal_fields() -> None:
    info = opencv_info()
    for k in ("opencv_hal", "cv2_custom_hal", "cv2_cpu_baseline", "cv2_cpu_dispatch"):
        assert k in info, k
    assert info["opencv_hal"] not in ("", "unknown")

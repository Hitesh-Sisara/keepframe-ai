# syntax=docker/dockerfile:1
# Stock worker image: multi-stage, linux/arm64 only, python:3.12-slim, stock OpenCV 5 wheel.
#   docker buildx build --builder arm64builder --platform linux/arm64 -t keepframe:dev --load .
# OPENCV_FORCE_DNN_ENGINE=3 is required (value 1 breaks cv2.FaceDetectorYN.create on 5.0.0.93).
# Base pinned by digest (SECURITY_REVIEW #6): python:3.12-slim (Debian 13 trixie) multi-arch index, same digest as
# Docker Hub's, pulled from ECR Public. Recorded 2026-10-09. Refresh: docker buildx imagetools inspect <tag>.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENCV_FORCE_DNN_ENGINE=3
# OS security updates on top of the pinned base (#5). No curl: nothing in the build or the runtime uses it
# (fetch_models.py uses urllib, install_opencv_wheel.sh uses pip), and it carried the image's CRITICAL CVEs.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends libglib2.0-0 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

FROM base AS deps
COPY requirements.txt /tmp/requirements.txt
COPY scripts/install_opencv_wheel.sh /tmp/install_opencv_wheel.sh
# requirements.txt = hash-pinned uv.lock export (opencv-contrib-python-headless==5.0.0.93). The script then
# reinstalls the sha256-pinned manylinux_2_28 aarch64 wheel (carotene HAL, no KleidiCV: the faster build on our
# workload, bench/results/2026-10-08/results.md) and fails the build if getBuildInformation() lists KleidiCV
# (docs/CV_CORE.md "Arm acceleration").
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && sh /tmp/install_opencv_wheel.sh python carotene

FROM deps AS models
COPY config/models.lock /tmp/models.lock
COPY scripts/fetch_models.py /tmp/fetch_models.py
# stdlib only; verifies every sha256 in the lock
RUN python /tmp/fetch_models.py --lock /tmp/models.lock --out /opt/models

FROM deps AS runtime
COPY --from=models /opt/models /opt/models
COPY keepframe /app/keepframe
COPY config /app/config
WORKDIR /app
RUN useradd -u 1000 -m cull && chown -R cull /app
USER cull
RUN python -c "import cv2, sys; assert cv2.__version__.startswith('5.'), cv2.__version__; assert hasattr(cv2,'img_hash') and hasattr(cv2,'quality'); bi=cv2.getBuildInformation(); assert 'carotene' in bi and 'KleidiCV' not in bi, 'want the carotene-only wheel'; print(cv2.__version__, cv2.__file__)"
ENTRYPOINT ["python", "-m", "keepframe.worker.run_event"]

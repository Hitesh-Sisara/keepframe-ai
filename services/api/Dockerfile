# syntax=docker/dockerfile:1
# KeepFrame API: Lambda (arm64) container image. Build from the repo root:
#   docker buildx build --builder arm64builder --platform linux/arm64 -f services/api/Dockerfile -t keepframe-api .
# Handler: app.main.handler (FastAPI via Mangum). No secrets are baked in; tokens come from Secrets Manager.
# Base pinned by digest (SECURITY_REVIEW #6): public.ecr.aws/lambda/python:3.12-arm64, recorded 2026-10-09.
FROM public.ecr.aws/lambda/python:3.12-arm64@sha256:8d0da08fe5200f248c5e1d94f83163b051bbe0e4846058d5484d826e2d10555b

# OS security updates on top of the pinned base (#5); the Lambda base ships microdnf.
RUN microdnf -y upgrade && microdnf clean all

COPY services/api/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir --no-deps -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY services/api/app ${LAMBDA_TASK_ROOT}/app

CMD ["app.main.handler"]

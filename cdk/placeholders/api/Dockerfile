# syntax=docker/dockerfile:1
# PLACEHOLDER API image, used by ApiStack ONLY when <srcRoot>/services/api/Dockerfile is absent (an infra-only
# checkout). It serves nothing: every request gets 503, so no route is ever unauthenticated-and-useful.
FROM public.ecr.aws/lambda/python:3.12-arm64
COPY handler.py ${LAMBDA_TASK_ROOT}/handler.py
CMD ["handler.handler"]

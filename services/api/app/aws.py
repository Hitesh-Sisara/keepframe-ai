"""Cached boto3 clients. Endpoint overrides (moto in local dev) come from AWS_ENDPOINT_URL."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config

from .config import settings

_CONFIGS = {
    # The agent resume must not hold the API Lambda; the agent acknowledges and continues in the background.
    "bedrock-agentcore": Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 1}),
    "s3": Config(signature_version="s3v4", s3={"addressing_style": "virtual"}, retries={"max_attempts": 3}),
}


@lru_cache(maxsize=16)
def client(service: str) -> Any:
    cfg = _CONFIGS.get(service, Config(retries={"max_attempts": 3, "mode": "standard"}))
    return boto3.client(service, region_name=settings().region, config=cfg)


def reset_clients() -> None:
    client.cache_clear()

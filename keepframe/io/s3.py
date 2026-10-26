"""Work-bucket I/O (S3) plus a read-only client for an optional S3-compatible import source.

* ``WORK_BUCKET`` set   -> objects live in s3://$WORK_BUCKET/<key>.
* ``WORK_BUCKET`` unset -> LOCAL_MODE: objects live under ``$CULL_LOCAL_ROOT/<key>``
  (default ``./data/local``), so unit tests and laptop runs need no AWS.

All keys are bucket-relative (``manifests/<eventId>.jsonl``, ``raw/<eventId>/<photoId>.webp``,
see docs/ARCHITECTURE.md "S3 layout"). ``s3://bucket/key`` URIs are accepted too.

The import source (optional SyncSource step) is READ ONLY: ``source_client()`` is only for GET / HEAD / LIST.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

# keepframe-work-<account>-<region>; the deployed stacks always set WORK_BUCKET, so this is only a hint.
DEFAULT_WORK_BUCKET = (
    f"keepframe-work-{os.environ.get('AWS_ACCOUNT_ID', '<AWS_ACCOUNT_ID>')}-{os.environ.get('AWS_REGION', 'ap-south-1')}"
)


def work_bucket() -> str | None:
    """Configured work bucket name, or None in LOCAL_MODE."""
    return os.environ.get("WORK_BUCKET") or None


def local_mode() -> bool:
    return work_bucket() is None


def local_root() -> Path:
    return Path(os.environ.get("CULL_LOCAL_ROOT", "data/local"))


def _split(key: str) -> tuple[str | None, str]:
    """Return (bucket, key). ``s3://b/k`` overrides the configured bucket."""
    if key.startswith("s3://"):
        bucket, _, rest = key[5:].partition("/")
        return bucket, rest
    return work_bucket(), key.lstrip("/")


@lru_cache(maxsize=1)
def s3_client() -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=os.environ.get("AWS_REGION", "ap-south-1"),
        config=Config(max_pool_connections=64, retries={"max_attempts": 8, "mode": "adaptive"}),
    )


@lru_cache(maxsize=1)
def source_client() -> Any:
    """boto3 S3 client for the optional import source (READ ONLY use: GET/HEAD/LIST).

    Any S3-compatible store. Env names: SOURCE_S3_ENDPOINT_URL (empty = AWS S3), SOURCE_REGION (default
    AWS_REGION), SOURCE_ACCESS_KEY_ID + SOURCE_SECRET_ACCESS_KEY (both empty = the default AWS credential chain,
    e.g. a cross-account bucket the task role can read).
    """
    import boto3
    from botocore.config import Config

    key, secret = os.environ.get("SOURCE_ACCESS_KEY_ID") or None, os.environ.get("SOURCE_SECRET_ACCESS_KEY") or None
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("SOURCE_S3_ENDPOINT_URL") or None,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name=os.environ.get("SOURCE_REGION") or os.environ.get("AWS_REGION", "ap-south-1"),
        config=Config(
            signature_version="s3v4",
            max_pool_connections=64,
            retries={"max_attempts": 8, "mode": "adaptive"},
        ),
    )


def _local_path(key: str) -> Path | None:
    """Local file for ``key`` in LOCAL_MODE, else None. ``s3://b/k`` maps to ``<root>/b/k``."""
    if not local_mode():
        return None
    bucket, k = _split(key)
    root = local_root()
    return root / bucket / k if key.startswith("s3://") and bucket else root / k


def get_bytes(key: str) -> bytes:
    lp = _local_path(key)
    if lp is not None:
        return lp.read_bytes()
    bucket, k = _split(key)
    body: bytes = s3_client().get_object(Bucket=bucket, Key=k)["Body"].read()
    return body


def put_bytes(key: str, data: bytes, content_type: str | None = None) -> None:
    lp = _local_path(key)
    if lp is not None:
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_bytes(data)
        return
    bucket, k = _split(key)
    extra: dict[str, Any] = {"ContentType": content_type} if content_type else {}
    s3_client().put_object(Bucket=bucket, Key=k, Body=data, **extra)


def exists(key: str) -> bool:
    lp = _local_path(key)
    if lp is not None:
        return lp.exists()
    bucket, k = _split(key)
    from botocore.exceptions import ClientError

    try:
        s3_client().head_object(Bucket=bucket, Key=k)
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def list_keys(prefix: str) -> Iterator[str]:
    """Yield bucket-relative keys under ``prefix`` (recursive, sorted in LOCAL_MODE)."""
    bucket, p = _split(prefix)
    if local_mode():
        root = local_root() / bucket if prefix.startswith("s3://") and bucket else local_root()
        base = root / p
        search = base if base.is_dir() else base.parent
        if not search.exists():
            return
        for f in sorted(search.rglob("*")):
            if f.is_file():
                rel = f.relative_to(root).as_posix()
                if rel.startswith(p):
                    yield rel
        return
    paginator = s3_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=p):
        for o in page.get("Contents", []):
            yield o["Key"]


# ``list`` is the name used in the contract; keep list_keys for readability.
list = list_keys  # noqa: A001


def presign(key: str, expires_s: int = 900) -> str:
    """Presigned GET URL (max 15 min per docs/RESPONSIBLE_USE.md). LOCAL_MODE returns a file:// URI."""
    expires_s = min(expires_s, 900)
    lp = _local_path(key)
    if lp is not None:
        return lp.resolve().as_uri()
    bucket, k = _split(key)
    url: str = s3_client().generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": k}, ExpiresIn=expires_s
    )
    return url


def put_json(key: str, obj: Any) -> None:
    put_bytes(key, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json")


def get_json(key: str) -> Any:
    return json.loads(get_bytes(key))

"""Object-store backends used by proxies/ingest/worker.

The authoritative S3 helper is `keepframe.io.s3`. Interface
expected from it (module-level functions, keys relative to $WORK_BUCKET):

    get_bytes(key: str) -> bytes          # raises KeyError / FileNotFoundError if absent
    put_bytes(key: str, data: bytes, content_type: str | None = None) -> None
    exists(key: str) -> bool
    list(prefix: str) -> list[str]        # full keys under prefix

This module provides:
  * LocalStore  - the same interface over a directory ($CULL_LOCAL_ROOT) for LOCAL_MODE/tests;
  * BotoStore   - a minimal boto3 fallback used only if keepframe.io.s3 is not importable;
  * ModuleStore - adapts the data agent's module to the object interface.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol


class Store(Protocol):
    def get_bytes(self, key: str) -> bytes: ...
    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    def exists(self, key: str) -> bool: ...
    def list(self, prefix: str) -> list[str]: ...


class LocalStore:
    """Directory-backed store mirroring the S3 layout (docs/ARCHITECTURE.md "S3 layout") under `root`."""

    kind = "local"

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key.lstrip("/")).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError(f"key escapes local root: {key!r}")
        return p

    def get_bytes(self, key: str) -> bytes:
        p = self._path(key)
        if not p.is_file():
            raise FileNotFoundError(key)
        return p.read_bytes()

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix) if prefix else self.root
        start = base if base.is_dir() else base.parent
        if not start.exists():
            return []
        out = []
        for p in start.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                k = p.relative_to(self.root).as_posix()
                if k.startswith(prefix.lstrip("/")):
                    out.append(k)
        return sorted(out)


class BotoStore:
    """Fallback S3 store (used only when keepframe.io.s3 is missing)."""

    kind = "s3"

    def __init__(self, bucket: str):
        import boto3

        self.bucket = bucket
        self._s3: Any = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-south-1"))

    def get_bytes(self, key: str) -> bytes:
        try:
            obj = self._s3.get_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            if is_not_found(e):
                raise FileNotFoundError(key) from e
            raise
        return bytes(obj["Body"].read())

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else {}
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        for page in self._s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(o["Key"] for o in page.get("Contents", []))
        return sorted(keys)


def is_not_found(e: BaseException) -> bool:
    """botocore NoSuchKey / 404 ClientError (or FileNotFoundError) -> True."""
    if isinstance(e, FileNotFoundError | KeyError):
        return True
    code = str(getattr(e, "response", {}).get("Error", {}).get("Code", "")) if hasattr(e, "response") else ""
    return code in ("NoSuchKey", "404", "NotFound") or type(e).__name__ == "NoSuchKey"


class ModuleStore:
    """Adapter over the data agent's keepframe.io.s3 module functions. Missing keys raise FileNotFoundError."""

    kind = "s3"

    def __init__(self, mod: ModuleType):
        self.mod = mod

    def get_bytes(self, key: str) -> bytes:
        try:
            return bytes(self.mod.get_bytes(key))
        except Exception as e:
            if is_not_found(e):
                raise FileNotFoundError(key) from e
            raise

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        try:
            self.mod.put_bytes(key, data, content_type=content_type)
        except TypeError:
            self.mod.put_bytes(key, data)

    def exists(self, key: str) -> bool:
        return bool(self.mod.exists(key))

    def list(self, prefix: str) -> list[str]:
        return sorted(self.mod.list(prefix))


class CountingStore:
    """Wraps a store and counts calls (summary.json s3_*_count; thread-safe)."""

    def __init__(self, inner: Store):
        import threading

        self.inner = inner
        self.kind = getattr(inner, "kind", "s3")
        self._lock = threading.Lock()
        self.counts = {"get": 0, "put": 0, "head": 0, "list": 0, "get_bytes": 0, "put_bytes": 0}

    def _inc(self, k: str, n: int = 1) -> None:
        with self._lock:
            self.counts[k] += n

    def get_bytes(self, key: str) -> bytes:
        data = self.inner.get_bytes(key)
        self._inc("get")
        self._inc("get_bytes", len(data))
        return data

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self.inner.put_bytes(key, data, content_type=content_type)
        self._inc("put")
        self._inc("put_bytes", len(data))

    def exists(self, key: str) -> bool:
        self._inc("head")
        return self.inner.exists(key)

    def list(self, prefix: str) -> list[str]:
        self._inc("list")
        return self.inner.list(prefix)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self.counts)

"""Settings for the optional source import (SyncSource): any S3-compatible store KeepFrame reads from, READ ONLY.

Every source-specific name lives outside the code: bucket names, CDN hosts, the database keyspace and the
operator's env file are environment variables with NO defaults. Set them from the operator-held env file
(see ``config/source.example.env`` for the names) before running a sync or an export. A missing value
raises immediately, so nothing silently falls back to a guessed name.

  SOURCE_BUCKET_PROCESSED   bucket of processed WebPs   (<eventId>/<photoId>.webp)
  SOURCE_BUCKET_ORIGINALS   bucket of originals          (<eventId>/<photoId>.<ext>)
  SOURCE_BUCKET_EXIF_RICH   bucket of rich EXIF sidecars (~4.4 KB, nested, has sub-second time)
  SOURCE_BUCKET_EXIF_SMALL  bucket of small EXIF sidecars (~370 B, flat, no sub-second)
  SOURCE_CDN_PROCESSED      base URL for processed images (manifest URL strings only, never fetched)
  SOURCE_CDN_ORIGINALS      base URL for originals (manifest URL strings only, never fetched)
  SOURCE_DB_KEYSPACE        Couchbase bucket / Postgres table that holds the source documents
"""

from __future__ import annotations

import os
import re

BUCKET_ENV = {
    "processed": "SOURCE_BUCKET_PROCESSED",
    "originals": "SOURCE_BUCKET_ORIGINALS",
    "exif_rich": "SOURCE_BUCKET_EXIF_RICH",
    "exif_small": "SOURCE_BUCKET_EXIF_SMALL",
}
CDN_ENV = {"processed": "SOURCE_CDN_PROCESSED", "originals": "SOURCE_CDN_ORIGINALS"}
KEYSPACE_ENV = "SOURCE_DB_KEYSPACE"
_IDENT = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class SourceNotConfigured(RuntimeError):
    """A required SOURCE_* environment variable is unset."""


def require(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise SourceNotConfigured(
            f"{name} is not set. Source names are operator-held config "
            "(see config/source.example.env); they have no default."
        )
    return v


def bucket(kind: str) -> str:
    """Bucket name for ``processed`` | ``originals`` | ``exif_rich`` | ``exif_small``."""
    return require(BUCKET_ENV[kind])


def exif_buckets() -> tuple[str, str]:
    """(rich, small): the exporter prefers the rich sidecar when both exist."""
    return bucket("exif_rich"), bucket("exif_small")


def cdn_base(kind: str) -> str:
    """CDN base URL for ``processed`` | ``originals``, always ending in '/'."""
    v = require(CDN_ENV[kind])
    return v if v.endswith("/") else v + "/"


def keyspace() -> str:
    """Database keyspace / table name, validated so it is safe to place in a statement."""
    v = require(KEYSPACE_ENV)
    if not _IDENT.match(v):
        raise ValueError(f"{KEYSPACE_ENV} must match {_IDENT.pattern}")
    return v

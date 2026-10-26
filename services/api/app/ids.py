"""Identifier validation, opaque cursors and run-id generation."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import Any

from fastapi import HTTPException

# Source event ids are UUIDs; photo/group/run ids are short tokens. Nothing may contain '/', '#' or '..'
# because ids are interpolated into S3 keys and DynamoDB key prefixes.
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def check_id(value: str, what: str) -> str:
    if not _ID.match(value) or ".." in value:
        raise HTTPException(status_code=422, detail=f"Invalid {what}")
    return value


def new_run_id() -> str:
    """ULID-style id: 48-bit ms timestamp + 80 random bits, Crockford base32, prefixed `run_`."""
    n = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[n & 31])
        n >>= 5
    return "run_" + "".join(reversed(chars))


def encode_cursor(obj: Any) -> str | None:
    if obj is None:
        return None
    raw = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> Any:
    if not cursor:
        return None
    try:
        pad = "=" * (-len(cursor) % 4)
        return json.loads(base64.urlsafe_b64decode(cursor + pad))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc


def key_cursor(cursor: str | None, hash_key: str, hash_value: str) -> dict[str, str] | None:
    """Decode a DynamoDB ExclusiveStartKey cursor and check it belongs to this query."""
    key = decode_cursor(cursor)
    if key is None:
        return None
    if (
        not isinstance(key, dict)
        or key.get(hash_key) != hash_value
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in key.items())
        or len(key) > 4
    ):
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return key


def offset_cursor(cursor: str | None) -> int:
    value = decode_cursor(cursor)
    if value is None:
        return 0
    if not isinstance(value, dict) or not isinstance(value.get("o"), int) or value["o"] < 0:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return int(value["o"])

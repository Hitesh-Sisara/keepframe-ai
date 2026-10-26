"""Storage access for the API: DynamoDB + S3 in AWS, or a folder of JSON/JSONL files in LOCAL_MODE.

Both backends expose the same small set of primitives with DynamoDB semantics (hash key equality, range
key `begins_with` / `>` / `=`, Limit + ExclusiveStartKey paging), so every route runs the same code path
locally and in Lambda. The API is a reader of the agent-owned tables; the only writes are the RUN# item on
POST /runs and the approval resolution on POST /approvals/{id}.

LOCAL_MODE layout under CULL_LOCAL_ROOT:
    s3/<key>                                   objects (manifests/, proxy/, sheets/, results/, bench/)
    dynamodb/<table-name>.jsonl                one item per line (plain JSON, numbers as numbers)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from boto3.dynamodb.conditions import Attr, Key

from .aws import client
from .config import settings

Item = dict[str, Any]


class ConditionFailed(Exception):
    """A conditional write lost (item missing, already exists, or no longer pending)."""


@dataclass(frozen=True)
class Index:
    name: str | None
    hash_key: str
    range_key: str | None


def table_index(which: str) -> Index:
    s = settings()
    return {
        "table": Index(None, "pk", "sk"),
        "gsi1": Index(s.gsi1_name, "gsi1pk", "sk"),
        "gsi2": Index(s.gsi2_name, "gsi2pk", "sk"),
        "gsi3": Index(s.gsi3_name, "gsi3pk", "gsi3sk"),
    }[which]


@dataclass(frozen=True)
class RangeCond:
    op: str  # "begins_with" | "gt" | "eq"
    value: str


class Backend(Protocol):
    def query(
        self,
        table: str,
        index: Index,
        hash_value: str,
        cond: RangeCond | None = None,
        *,
        limit: int = 100,
        start_key: Item | None = None,
        forward: bool = True,
    ) -> tuple[list[Item], Item | None]: ...

    def get_item(self, table: str, pk: str, sk: str) -> Item | None: ...

    def batch_get(self, table: str, keys: list[tuple[str, str]]) -> list[Item]: ...

    def update_item(
        self,
        table: str,
        pk: str,
        sk: str,
        *,
        set_values: Item,
        remove: tuple[str, ...] = (),
        condition: str | None = None,
        cond_value: str = "",
    ) -> Item: ...

    def count(self, table: str, index: Index, hash_value: str) -> int: ...

    def get_object(self, key: str) -> bytes | None: ...

    def list_keys(self, prefix: str, suffix: str = "", max_keys: int = 1000) -> list[str]: ...

    def presign(self, key: str) -> str: ...


# Conditions understood by update_item (kept to a closed set so both backends agree exactly).
COND_NOT_EXISTS = "not_exists"  # create-only
COND_EXISTS = "exists"
COND_PENDING = "pending"  # item still sits on the GSI3 approval queue
COND_PENDING_UNLOCKED = "pending_unlocked"  # pending and no resume in flight (lock older than cond_value)
COND_UNCHANGED = "unchanged"  # optimistic write: item exists and its updated_at still equals cond_value


def plain(value: Any) -> Any:
    """DynamoDB Decimals → int/float, recursively."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, set):
        return sorted(plain(v) for v in value)
    return value


def to_dynamo(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, dict):
        return {k: to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dynamo(v) for v in value]
    return value


class AwsBackend:
    def _table(self, name: str) -> Any:
        return _dynamodb_resource().Table(name)

    def query(self, table, index, hash_value, cond=None, *, limit=100, start_key=None, forward=True):
        expr = Key(index.hash_key).eq(hash_value)
        if cond is not None and index.range_key:
            rk = Key(index.range_key)
            expr = expr & {"begins_with": rk.begins_with, "gt": rk.gt, "eq": rk.eq}[cond.op](cond.value)
        kwargs: dict[str, Any] = {"KeyConditionExpression": expr, "Limit": limit, "ScanIndexForward": forward}
        if index.name:
            kwargs["IndexName"] = index.name
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        resp = self._table(table).query(**kwargs)
        return [plain(i) for i in resp.get("Items", [])], plain(resp.get("LastEvaluatedKey"))

    def get_item(self, table, pk, sk):
        resp = self._table(table).get_item(Key={"pk": pk, "sk": sk})
        item = resp.get("Item")
        return plain(item) if item else None

    def batch_get(self, table, keys):
        out: list[Item] = []
        res = _dynamodb_resource()
        unique = list(dict.fromkeys(keys))
        for i in range(0, len(unique), 100):
            request: dict[str, Any] = {
                table: {"Keys": [{"pk": pk, "sk": sk} for pk, sk in unique[i : i + 100]]}
            }
            for _attempt in range(5):
                resp = res.batch_get_item(RequestItems=request)
                out.extend(plain(it) for it in resp.get("Responses", {}).get(table, []))
                request = resp.get("UnprocessedKeys") or {}
                if not request:
                    break
                time.sleep(0.05 * (2**_attempt))
        return out

    def update_item(self, table, pk, sk, *, set_values, remove=(), condition=None, cond_value=""):
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        sets = []
        for i, (k, v) in enumerate(set_values.items()):
            names[f"#s{i}"] = k
            values[f":s{i}"] = to_dynamo(v)
            sets.append(f"#s{i} = :s{i}")
        removes = []
        for i, k in enumerate(remove):
            names[f"#r{i}"] = k
            removes.append(f"#r{i}")
        update = ""
        if sets:
            update += "SET " + ", ".join(sets)
        if removes:
            update += " REMOVE " + ", ".join(removes)
        kwargs: dict[str, Any] = {
            "Key": {"pk": pk, "sk": sk},
            "UpdateExpression": update.strip(),
            "ExpressionAttributeNames": names,
            "ReturnValues": "ALL_NEW",
        }
        if values:
            kwargs["ExpressionAttributeValues"] = values
        if condition == COND_NOT_EXISTS:
            kwargs["ConditionExpression"] = Attr("pk").not_exists()
        elif condition == COND_EXISTS:
            kwargs["ConditionExpression"] = Attr("pk").exists()
        elif condition == COND_PENDING:
            kwargs["ConditionExpression"] = Attr("gsi3pk").eq("PENDING")
        elif condition == COND_PENDING_UNLOCKED:
            kwargs["ConditionExpression"] = Attr("gsi3pk").eq("PENDING") & (
                Attr("resume_lock").not_exists() | Attr("resume_lock").lt(cond_value)
            )
        elif condition == COND_UNCHANGED:
            kwargs["ConditionExpression"] = Attr("pk").exists() & Attr("updated_at").eq(cond_value)
        try:
            resp = self._table(table).update_item(**kwargs)
        except client("dynamodb").exceptions.ConditionalCheckFailedException as exc:
            raise ConditionFailed(str(exc)) from exc
        return plain(resp.get("Attributes", {}))

    def count(self, table, index, hash_value):
        total = 0
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key(index.hash_key).eq(hash_value),
            "Select": "COUNT",
        }
        if index.name:
            kwargs["IndexName"] = index.name
        while True:
            resp = self._table(table).query(**kwargs)
            total += int(resp.get("Count", 0))
            if "LastEvaluatedKey" not in resp:
                return total
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def get_object(self, key):
        s3 = client("s3")
        try:
            return s3.get_object(Bucket=settings().work_bucket, Key=key)["Body"].read()
        except s3.exceptions.NoSuchKey:
            return None
        except Exception as exc:  # AccessDenied on a missing key when ListBucket is not granted
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "404", "AccessDenied", "403"}:
                return None
            raise

    def list_keys(self, prefix, suffix="", max_keys=1000):
        out: list[str] = []
        paginator = client("s3").get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings().work_bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(suffix):
                    out.append(obj["Key"])
                    if len(out) >= max_keys:
                        return out
        return out

    def presign(self, key):
        return str(
            client("s3").generate_presigned_url(
                "get_object",
                Params={"Bucket": settings().work_bucket, "Key": key},
                ExpiresIn=settings().presign_ttl_s,
            )
        )


_resource_lock = threading.Lock()
_resource: Any = None


def _dynamodb_resource() -> Any:
    global _resource
    with _resource_lock:
        if _resource is None:
            import boto3

            _resource = boto3.resource("dynamodb", region_name=settings().region)
        return _resource


class LocalBackend:
    """Folder-backed stand-in with the DynamoDB query semantics the routes rely on."""

    def __init__(self, root: str) -> None:
        if not root:
            raise RuntimeError("LOCAL_MODE needs CULL_LOCAL_ROOT pointing at a results folder")
        self.root = Path(root).resolve()
        self._lock = threading.Lock()
        self._tables: dict[str, list[Item]] = {}
        self._mtimes: dict[str, int] = {}
        # Per-process signing key for local "presigned" URLs; never leaves the process.
        self._sign_key = os.urandom(32)

    def _rows(self, table: str) -> list[Item]:
        # Reload when the file changed on disk: a local agent process writes the same JSONL tables.
        path = self.root / "dynamodb" / f"{table}.jsonl"
        mtime = path.stat().st_mtime_ns if path.exists() else 0
        if table not in self._tables or self._mtimes.get(table) != mtime:
            rows: list[Item] = []
            if path.exists():
                rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            self._tables[table] = rows
            self._mtimes[table] = mtime
        return self._tables[table]

    def _persist(self, table: str) -> None:
        path = self.root / "dynamodb" / f"{table}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".jsonl.api.tmp")
        tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in self._tables[table]))
        tmp.replace(path)
        self._mtimes[table] = path.stat().st_mtime_ns

    @staticmethod
    def _key_of(item: Item, index: Index) -> Item:
        key = {"pk": item["pk"], "sk": item["sk"]}
        key[index.hash_key] = item[index.hash_key]
        if index.range_key:
            key[index.range_key] = item[index.range_key]
        return key

    def query(self, table, index, hash_value, cond=None, *, limit=100, start_key=None, forward=True):
        with self._lock:
            rows = [r for r in self._rows(table) if r.get(index.hash_key) == hash_value]
            rk = index.range_key
            if rk:
                rows = [r for r in rows if rk in r]
                if cond is not None:
                    test = {
                        "begins_with": lambda v: str(v).startswith(cond.value),
                        "gt": lambda v: str(v) > cond.value,
                        "eq": lambda v: str(v) == cond.value,
                    }[cond.op]
                    rows = [r for r in rows if test(r[rk])]
                rows.sort(key=lambda r: (str(r[rk]), r["pk"], r["sk"]), reverse=not forward)
            if start_key:

                def sort_key(r: Item) -> tuple[str, str, str]:
                    return (str(r[rk]) if rk else "", r["pk"], r["sk"])

                marker = (str(start_key.get(rk, "")) if rk else "", start_key["pk"], start_key["sk"])
                rows = [r for r in rows if (sort_key(r) > marker if forward else sort_key(r) < marker)]
            page = rows[:limit]
            last = self._key_of(page[-1], index) if len(rows) > limit and page else None
            return json.loads(json.dumps(page)), last

    def get_item(self, table, pk, sk):
        with self._lock:
            for r in self._rows(table):
                if r["pk"] == pk and r["sk"] == sk:
                    return json.loads(json.dumps(r))
        return None

    def batch_get(self, table, keys):
        wanted = set(keys)
        with self._lock:
            return json.loads(json.dumps([r for r in self._rows(table) if (r["pk"], r["sk"]) in wanted]))

    def update_item(self, table, pk, sk, *, set_values, remove=(), condition=None, cond_value=""):
        with self._lock:
            rows = self._rows(table)
            current = next((r for r in rows if r["pk"] == pk and r["sk"] == sk), None)
            if condition == COND_NOT_EXISTS and current is not None:
                raise ConditionFailed("item exists")
            if condition == COND_EXISTS and current is None:
                raise ConditionFailed("item missing")
            if condition in (COND_PENDING, COND_PENDING_UNLOCKED) and (
                current is None or current.get("gsi3pk") != "PENDING"
            ):
                raise ConditionFailed("not pending")
            lock = current.get("resume_lock") if current else None
            if condition == COND_PENDING_UNLOCKED and lock is not None and str(lock) >= cond_value:
                raise ConditionFailed("resume in flight")
            if condition == COND_UNCHANGED and (current is None or current.get("updated_at") != cond_value):
                raise ConditionFailed("item changed")
            if current is None:
                current = {"pk": pk, "sk": sk}
                rows.append(current)
            current.update(json.loads(json.dumps(set_values)))
            for k in remove:
                current.pop(k, None)
            self._persist(table)
            return json.loads(json.dumps(current))

    def count(self, table, index, hash_value):
        with self._lock:
            return sum(1 for r in self._rows(table) if r.get(index.hash_key) == hash_value)

    def _path(self, key: str) -> Path:
        path = (self.root / "s3" / key).resolve()
        if not path.is_relative_to(self.root / "s3"):
            raise ValueError("key escapes the local root")
        return path

    def get_object(self, key):
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def list_keys(self, prefix, suffix="", max_keys=1000):
        base = self.root / "s3"
        out = sorted(
            str(p.relative_to(base))
            for p in base.rglob("*")
            if p.is_file() and str(p.relative_to(base)).startswith(prefix) and p.name.endswith(suffix)
        )
        return out[:max_keys]

    # Local presign: an HMAC over key+expiry, verified by the /_local/objects route (LOCAL_MODE only).
    def sign(self, key: str, expires: int) -> str:
        mac = hmac.new(self._sign_key, f"{key}\n{expires}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).decode().rstrip("=")

    def verify(self, key: str, expires: int, sig: str) -> bool:
        return expires >= int(time.time()) and hmac.compare_digest(self.sign(key, expires), sig)

    def presign(self, key):
        expires = int(time.time()) + settings().presign_ttl_s
        base = os.environ.get("CULL_LOCAL_PUBLIC_BASE", "http://127.0.0.1:8000").rstrip("/")
        return f"{base}/_local/objects/{quote(key)}?expires={expires}&sig={self.sign(key, expires)}"


_backend: Backend | None = None
_backend_lock = threading.Lock()


def backend() -> Backend:
    global _backend
    with _backend_lock:
        if _backend is None:
            s = settings()
            _backend = LocalBackend(s.local_root) if s.local_mode else AwsBackend()
        return _backend


def reset_backend() -> None:
    global _backend, _resource
    with _backend_lock:
        _backend = None
    with _resource_lock:
        _resource = None

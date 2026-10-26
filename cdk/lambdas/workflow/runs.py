"""Shared helpers for the CullEvent workflow Lambdas (one zip asset, one handler module per function).

RUN# item: pk=EVENT#<eventId>, sk=RUN#<runId> in keepframe-decisions. The API creates it on
POST /runs; these functions only update status fields on the same item (never create a second one).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any

import boto3

TABLE = os.environ.get("CULL_DECISIONS_TABLE", "keepframe-decisions")
BUCKET = os.environ["WORK_BUCKET"]
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

_ddb = boto3.client("dynamodb")
_s3 = boto3.client("s3")


def now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def check_id(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise ValueError(f"invalid {name}")
    return value


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"ts": now_iso(), "event": event, **fields}, default=str))


def update_run(event_id: str, run_id: str, fields: dict[str, Any]) -> None:
    """SET the given top-level attributes on the RUN# item. Creates the item only if the API did not."""
    names, values, sets = {}, {}, []
    for i, (k, v) in enumerate(fields.items()):
        names[f"#k{i}"] = k
        values[f":v{i}"] = to_ddb(v)
        sets.append(f"#k{i} = :v{i}")
    _ddb.update_item(
        TableName=TABLE,
        Key={"pk": {"S": f"EVENT#{event_id}"}, "sk": {"S": f"RUN#{run_id}"}},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def get_run_status(event_id: str, run_id: str) -> str | None:
    item = _ddb.get_item(
        TableName=TABLE,
        Key={"pk": {"S": f"EVENT#{event_id}"}, "sk": {"S": f"RUN#{run_id}"}},
        ProjectionExpression="#s",
        ExpressionAttributeNames={"#s": "status"},
        ConsistentRead=True,
    ).get("Item")
    return item["status"]["S"] if item and "S" in item.get("status", {}) else None


def to_ddb(v: Any) -> dict[str, Any]:
    if v is None:
        return {"NULL": True}
    if isinstance(v, bool):
        return {"BOOL": v}
    if isinstance(v, (int, float)):
        return {"N": repr(v)}
    if isinstance(v, str):
        return {"S": v}
    if isinstance(v, dict):
        return {"M": {str(k): to_ddb(x) for k, x in v.items()}}
    if isinstance(v, (list, tuple)):
        return {"L": [to_ddb(x) for x in v]}
    return {"S": str(v)}


def result_keys(event_id: str, run_id: str, name: str) -> tuple[str, str]:
    """(versioned, latest) result keys, same rule as keepframe.worker.run_record.result_keys: the worker writes
    results/<eventId>/<runId>/<name> first and the unversioned results/<eventId>/<name> copy for one release."""
    return f"results/{event_id}/{run_id}/{name}", f"results/{event_id}/{name}"


def first_existing(keys: tuple[str, ...], read: Any) -> tuple[Any, str | None]:
    """Return (read(key), key) for the first key that exists, else (None, None). `read` returns None on NoSuchKey."""
    for key in keys:
        value = read(key)
        if value is not None:
            return value, key
    return None, None


def get_json(key: str) -> Any | None:
    try:
        body = _s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    except _s3.exceptions.NoSuchKey:
        return None
    return json.loads(body)


def head(key: str) -> dict[str, Any] | None:
    try:
        return _s3.head_object(Bucket=BUCKET, Key=key)
    except Exception as exc:  # 404 surfaces as ClientError
        if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def count_lines(key: str) -> int:
    body = _s3.get_object(Bucket=BUCKET, Key=key)["Body"]
    n = 0
    for chunk in iter(lambda: body.read(1 << 20), b""):
        n += chunk.count(b"\n")
    return n

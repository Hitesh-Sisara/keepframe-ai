"""DynamoDB table definitions and an idempotent creator.

Infra's CDK adopts these tables via ``Table.fromTableName``; this module is the
source of truth for names, keys and GSIs until then.

    python -m keepframe.store.tables            # create if missing (on-demand, PITR, TTL)
    python -m keepframe.store.tables --check    # describe only
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

DECISIONS_TABLE = os.environ.get("DECISIONS_TABLE", "keepframe-decisions")
TRACES_TABLE = os.environ.get("TRACES_TABLE", "keepframe-traces")

GSI_VERDICT = "gsi1"  # gsi1pk = EVENT#<eventId>#VERDICT#<verdict>, sk
GSI_GROUP = "gsi2"  # gsi2pk = GROUP#<groupId>, sk
GSI_PENDING = "gsi3"  # gsi3pk = PENDING, gsi3sk = <ts>#<eventId>#<id> (sparse)


def _s(name: str) -> dict[str, str]:
    return {"AttributeName": name, "AttributeType": "S"}


def _gsi(name: str, hash_key: str, range_key: str) -> dict[str, Any]:
    return {
        "IndexName": name,
        "KeySchema": [
            {"AttributeName": hash_key, "KeyType": "HASH"},
            {"AttributeName": range_key, "KeyType": "RANGE"},
        ],
        "Projection": {"ProjectionType": "ALL"},
    }


def decisions_spec() -> dict[str, Any]:
    return {
        "TableName": DECISIONS_TABLE,
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [
            _s("pk"), _s("sk"), _s("gsi1pk"), _s("gsi2pk"), _s("gsi3pk"), _s("gsi3sk"),
        ],
        "KeySchema": [
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        "GlobalSecondaryIndexes": [
            _gsi(GSI_VERDICT, "gsi1pk", "sk"),
            _gsi(GSI_GROUP, "gsi2pk", "sk"),
            _gsi(GSI_PENDING, "gsi3pk", "gsi3sk"),
        ],
        "SSESpecification": {"Enabled": True},
        "Tags": [{"Key": "project", "Value": "keepframe"}],
    }


def traces_spec() -> dict[str, Any]:
    return {
        "TableName": TRACES_TABLE,
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [_s("pk"), _s("sk")],
        "KeySchema": [
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        "SSESpecification": {"Enabled": True},
        "Tags": [{"Key": "project", "Value": "keepframe"}],
    }


def ensure_tables(client: Any) -> dict[str, str]:
    """Create both tables if missing; enable PITR on decisions and TTL on traces. Idempotent."""
    out: dict[str, str] = {}
    existing = set(client.list_tables().get("TableNames", []))
    for spec in (decisions_spec(), traces_spec()):
        name = spec["TableName"]
        if name in existing:
            out[name] = "exists"
        else:
            client.create_table(**spec)
            out[name] = "created"
        client.get_waiter("table_exists").wait(TableName=name)
    client.update_continuous_backups(
        TableName=DECISIONS_TABLE,
        PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
    )
    ttl = client.describe_time_to_live(TableName=TRACES_TABLE)["TimeToLiveDescription"]
    if ttl.get("TimeToLiveStatus") not in ("ENABLED", "ENABLING"):
        client.update_time_to_live(
            TableName=TRACES_TABLE,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
        )
    return out


def main() -> None:
    import boto3

    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    client = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
    if not a.check:
        print(json.dumps(ensure_tables(client)))
    for name in (DECISIONS_TABLE, TRACES_TABLE):
        t = client.describe_table(TableName=name)["Table"]
        print(json.dumps({
            "table": name,
            "arn": t["TableArn"],
            "status": t["TableStatus"],
            "keys": t["KeySchema"],
            "gsis": [g["IndexName"] for g in t.get("GlobalSecondaryIndexes", [])],
            "pitr": client.describe_continuous_backups(TableName=name)["ContinuousBackupsDescription"][
                "PointInTimeRecoveryDescription"]["PointInTimeRecoveryStatus"],
            "ttl": client.describe_time_to_live(TableName=name)["TimeToLiveDescription"],
        }, default=str))


if __name__ == "__main__":
    main()

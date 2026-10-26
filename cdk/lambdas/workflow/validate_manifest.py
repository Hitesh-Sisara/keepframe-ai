"""ValidateManifest (CullEvent, after Ingest): input {event_id, run_id, flavor?, sync_source?, skip_agent?}.

`flavor` is optional and only `stock` is valid (COOL was removed 2026-10-08: Marketplace-billed).

Checks the ids, that manifests/<eventId>.jsonl (+ .meta.json) exist in the work bucket, counts rows, that
the credentials the later steps need are not still the CDK placeholder, and marks the RUN# item `running`.
Raises InvalidInput / ManifestMissing / SecretNotSet so the state machine's Catch marks the run failed
before any ECS task starts.

Secret check: keepframe/anthropic when the agent step runs and the provider (SSM /keepframe/model-provider)
is the optional `anthropic` one. The default provider (`bedrock`) uses the runtime role and needs no secret.
Values are only compared with the placeholder string and never logged or returned.
"""

from __future__ import annotations

from typing import Any

import json
import os

import boto3

from runs import check_id, count_lines, get_json, head, log, now_iso, update_run

PLACEHOLDER = "SET_OUT_OF_BAND"
ANTHROPIC_SECRET = os.environ.get("CULL_ANTHROPIC_SECRET_ID", "keepframe/anthropic")
PROVIDER_PARAM = os.environ.get("CULL_MODEL_PROVIDER_PARAM", "/keepframe/model-provider")
_sm = boto3.client("secretsmanager")
_ssm = boto3.client("ssm")


class InvalidInput(Exception):
    pass


class ManifestMissing(Exception):
    pass


class SecretNotSet(Exception):
    pass


def unset_keys(secret_id: str) -> list[str]:
    """Key NAMES whose value is empty or still the CDK placeholder. Never exposes values."""
    raw = _sm.get_secret_value(SecretId=secret_id)["SecretString"]
    try:
        doc = json.loads(raw)
    except ValueError:
        return ["<not JSON>"]
    if not isinstance(doc, dict) or not doc:
        return ["<empty>"]
    return sorted(k for k, v in doc.items() if not isinstance(v, str) or not v.strip() or v == PLACEHOLDER)


def check_secrets(event: dict[str, Any]) -> None:
    required = []
    if not event.get("skip_agent"):
        provider = _ssm.get_parameter(Name=PROVIDER_PARAM)["Parameter"]["Value"].strip().lower()
        if provider == "anthropic":
            required.append(ANTHROPIC_SECRET)
    for sid in required:
        missing = unset_keys(sid)
        if missing:
            raise SecretNotSet(
                f"secret {sid} has no value for {', '.join(missing)}; set it with "
                f"`aws secretsmanager put-secret-value --secret-id {sid} ...` (docs/DEPLOY.md)"
            )


def handler(event: dict[str, Any], _ctx: Any) -> dict[str, Any]:
    try:
        event_id = check_id("event_id", event.get("event_id"))
        run_id = check_id("run_id", event.get("run_id"))
    except ValueError as exc:
        raise InvalidInput(str(exc)) from None
    flavor = event.get("flavor", "stock")
    if flavor != "stock":
        raise InvalidInput("flavor must be stock (COOL was removed)")

    check_secrets(event)

    key = f"manifests/{event_id}.jsonl"
    if head(key) is None:
        raise ManifestMissing(f"s3 object {key} not found")
    n_rows = count_lines(key)
    if n_rows == 0:
        raise ManifestMissing(f"{key} is empty")
    meta = get_json(f"manifests/{event_id}.meta.json") or {}

    update_run(event_id, run_id, {
        "status": "running",
        "stage": "validated",
        "flavor": flavor,
        "manifest_rows": n_rows,
        "updated_at": now_iso(),
    })
    log("manifest_validated", event_id=event_id, run_id=run_id, rows=n_rows, flavor=flavor)
    return {
        "event_id": event_id,
        "run_id": run_id,
        "flavor": flavor,
        "manifest_rows": n_rows,
        "n_photos_meta": meta.get("n_photos"),
    }

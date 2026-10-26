"""Shared AgentCore helpers for the CullEvent RunAgent loop (invoke_agent.py, agent_status.py).

The runtime ARN comes from SSM ``/keepframe/agent-runtime-arn`` (owned by AgentCoreStack), read at call time.
There is no other agent host (the Fargate fallback was removed 2026-10-08). The session id follows ``services/api/app/agent_resume.session_id_for`` exactly, so start, status polls
and the API's approval resume share one AgentCore session while it is alive.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.config import Config

from runs import TABLE, _ddb

RUNTIME_PARAM = os.environ.get("CULL_AGENT_RUNTIME_PARAM", "/keepframe/agent-runtime-arn")
MAX_ACK = 1 << 20  # status replies can carry a run summary

_ssm = boto3.client("ssm")
_agentcore: Any = None


def agentcore_client() -> Any:
    global _agentcore
    if _agentcore is None:
        # A cold session start pulls the image into a new microVM; leave room within the 90 s Lambda timeout.
        _agentcore = boto3.client("bedrock-agentcore", config=Config(
            connect_timeout=10, read_timeout=75, retries={"max_attempts": 3, "mode": "standard"}))
    return _agentcore


class AgentError(Exception):
    """The agent runtime reported status=error or an unusable response."""


def runtime_arn() -> str:
    value = _ssm.get_parameter(Name=RUNTIME_PARAM)["Parameter"]["Value"].strip()
    if not value.startswith("arn:aws:bedrock-agentcore:"):
        raise AgentError(f"{RUNTIME_PARAM} is not an AgentCore runtime ARN")
    return value


def session_id_for(run: dict[str, Any] | None, run_id: str) -> str:
    """Mirror of services/api/app/agent_resume.session_id_for (AgentCore needs >= 33 chars)."""
    if run and isinstance(run.get("agent_session_id"), str) and len(run["agent_session_id"]) >= 33:
        return str(run["agent_session_id"])
    sid = f"keepframe-{run_id}"
    return sid if len(sid) >= 33 else sid.ljust(33, "0")


def run_item(event_id: str, run_id: str) -> dict[str, Any] | None:
    """RUN# item as plain strings (only the string attributes session_id_for reads)."""
    item = _ddb.get_item(
        TableName=TABLE,
        Key={"pk": {"S": f"EVENT#{event_id}"}, "sk": {"S": f"RUN#{run_id}"}},
        ProjectionExpression="agent_session_id",
        ConsistentRead=True,
    ).get("Item")
    if not item:
        return None
    return {k: v["S"] for k, v in item.items() if "S" in v}


def invoke(arn: str, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    resp = agentcore_client().invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id,
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode(),
    )
    stream = resp.get("response")
    raw = stream.read(MAX_ACK) if hasattr(stream, "read") else b""
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise AgentError(f"agent returned non-JSON ({len(raw)} bytes, HTTP {resp.get('statusCode')})") from None
    if not isinstance(doc, dict):
        raise AgentError("agent returned a non-object JSON body")
    return doc

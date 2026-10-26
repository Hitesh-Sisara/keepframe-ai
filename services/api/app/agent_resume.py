"""Resume the paused Strands agent after a human answers an approval, and probe its status.

Tried in order, first configured wins:
1. AgentCore: bedrock-agentcore InvokeAgentRuntime on CULL_AGENT_RUNTIME_ARN, payload
   {"mode": "resume", "run_id", "decisions"}. The runtime answers with one JSON blob on the `response`
   stream: {"status": running|paused|done|error, "run_id", ...}. That status is surfaced so the API can
   update RUN# at once instead of waiting for the agent's own write.
2. HTTP: POST the same payload to CULL_AGENT_URL (local agent_runtime container's /invocations).
3. None configured: raise NotConfigured; the API then applies the human verdicts itself (PHOTO# items,
   decided_by=human) so the review flow still completes without an agent.

The Fargate RunTask fallback was removed on 2026-10-09 (infra deleted the cull-agent task definition).
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .aws import client
from .config import settings

log = logging.getLogger(__name__)

# The runtime's answer is a small JSON document; never buffer more than this from the stream.
MAX_RESPONSE_BYTES = 64 * 1024
AGENT_STATUSES = ("running", "paused", "done", "error")
# Fields copied from the agent's JSON answer into the API response (never the whole document).
ANSWER_FIELDS = ("run_id", "event_id", "error", "note", "pending_approvals", "accepted")
SESSION_PREFIX = "keepframe-"
SESSION_MIN_LEN = 33  # InvokeAgentRuntime runtimeSessionId: min 33, max 256


class ResumeUnavailable(Exception):
    """A resume path is configured but could not be used."""


class NotConfigured(ResumeUnavailable):
    """No resume path is configured at all."""


def session_id_for(run: dict[str, Any] | None, run_id: str) -> str:
    """The AgentCore runtimeSessionId for a run. SINGLE SOURCE OF TRUTH (docs/AGENT.md "AgentCore Runtime"):
    the workflow's invoke_agent / agent_status Lambdas must produce the same string so start, status polls
    and resumes share one session.

    Rule: a RUN# `agent_session_id` of ≥ 33 chars wins; else f"keepframe-{run_id}" right-padded with '0'
    to 33 chars (longer ids are used as is).
    """
    pinned = run.get("agent_session_id") if run else None
    if isinstance(pinned, str) and len(pinned) >= SESSION_MIN_LEN:
        return pinned
    return f"{SESSION_PREFIX}{run_id}".ljust(SESSION_MIN_LEN, "0")


def configured_path() -> str | None:
    s = settings()
    if s.agent_runtime_arn:
        return "agentcore"
    if s.agent_url:
        return "http"
    return None


def resume(run_id: str, decisions: list[dict[str, Any]], run: dict[str, Any] | None) -> dict[str, Any]:
    path = configured_path()
    if path == "agentcore":
        return _agentcore({"mode": "resume", "run_id": run_id, "decisions": decisions}, run, run_id)
    if path == "http":
        return _http(run_id, decisions)
    raise NotConfigured("No agent resume path configured")


def status(run_id: str, run: dict[str, Any] | None) -> dict[str, Any]:
    """Live `{"mode": "status"}` on the AgentCore runtime. Raises NotConfigured for the other paths."""
    if configured_path() != "agentcore":
        raise NotConfigured("Live status needs the AgentCore path")
    return _agentcore({"mode": "status", "run_id": run_id}, run, run_id)


def _payload(run_id: str, decisions: list[dict[str, Any]]) -> bytes:
    return json.dumps({"mode": "resume", "run_id": run_id, "decisions": decisions}).encode()


def _agentcore(body: dict[str, Any], run: dict[str, Any] | None, run_id: str) -> dict[str, Any]:
    s = settings()
    kwargs: dict[str, Any] = {
        "agentRuntimeArn": s.agent_runtime_arn,
        "runtimeSessionId": session_id_for(run, run_id),
        "contentType": "application/json",
        "accept": "application/json",
        "payload": json.dumps(body).encode(),
    }
    if s.agent_runtime_qualifier:
        kwargs["qualifier"] = s.agent_runtime_qualifier
    try:
        resp = client("bedrock-agentcore").invoke_agent_runtime(**kwargs)
        raw = _read_bounded(resp.get("response"))
    except ClientError as exc:
        # RuntimeClientError (container 4xx/5xx), ThrottlingException, ServiceQuotaExceeded, AccessDenied,
        # ResourceNotFound, Validation, InternalServer: all mean "the agent did not take the request".
        code = exc.response.get("Error", {}).get("Code", "ClientError")
        raise ResumeUnavailable(f"AgentCore {code}") from exc
    except BotoCoreError as exc:  # timeouts, connection and stream read errors
        raise ResumeUnavailable(f"AgentCore {type(exc).__name__}") from exc
    http_status = int(resp.get("statusCode", 200))
    if http_status >= 400:
        raise ResumeUnavailable(f"AgentCore HTTP {http_status}")
    answer = _answer(raw) or {}
    agent_status = answer.pop("agent_status", None)
    if body["mode"] == "resume" and agent_status == "error":
        # The agent refused the resume (e.g. a decision failed validation): nothing was resumed.
        raise ResumeUnavailable(f"Agent error: {answer.get('error') or 'no detail'}"[:200])
    out: dict[str, Any] = {
        "via": "agentcore",
        "status": agent_status or "unknown",
        "http_status": http_status,
        "run_id": answer.pop("run_id", run_id),
    }
    if answer:
        out["ack"] = answer
    return out


def _read_bounded(stream: Any) -> bytes:
    if stream is None or not hasattr(stream, "read"):
        return b""
    try:
        raw = stream.read(MAX_RESPONSE_BYTES + 1)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    return raw[:MAX_RESPONSE_BYTES] if isinstance(raw, bytes) else b""


def _answer(raw: bytes) -> dict[str, Any] | None:
    """The agent's JSON answer reduced to known fields; `agent_status` only when it is a known status."""
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    out: dict[str, Any] = {}
    st = doc.get("status")
    if isinstance(st, str) and st in AGENT_STATUSES:
        out["agent_status"] = st
    for k in ANSWER_FIELDS:
        v = doc.get(k)
        if k == "error" and isinstance(v, str):
            out[k] = v[:300]
        elif k == "pending_approvals" and isinstance(v, (int, list)):
            out[k] = len(v) if isinstance(v, list) else v
        elif v is not None and isinstance(v, (str, int, float, bool)):
            out[k] = v
    return out


def _http(run_id: str, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    url = settings().agent_url
    if not url.startswith(("http://127.0.0.1", "http://localhost", "https://")):
        raise ResumeUnavailable("CULL_AGENT_URL must be https:// or a loopback http:// URL")
    req = urllib.request.Request(
        url, data=_payload(run_id, decisions), method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - scheme checked above
        return {"via": "http", "status": r.status, "ack": _short(r.read(4096))}


def _short(raw: bytes) -> Any:
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(doc, dict):
        return {k: doc[k] for k in ("status", "run_id", "accepted") if k in doc}
    return None

"""Model provider switch: ``KEEPFRAME_LLM_PROVIDER=bedrock|anthropic|fake`` (default ``bedrock``).

bedrock (default)    Claude on Amazon Bedrock through Strands ``BedrockModel`` (bedrock-runtime Converse API,
                     SigV4 with the task/runtime role). Planner model: KEEPFRAME_BEDROCK_MODEL_ID (default an
                     inference profile id, see DEFAULTS); reasons model: KEEPFRAME_BEDROCK_REASONS_MODEL_ID.
                     Region: KEEPFRAME_BEDROCK_REGION, else AWS_REGION. No API key is involved.
anthropic (optional) Any Anthropic-compatible endpoint through Strands ``AnthropicModel`` over the official
                     ``anthropic`` SDK: ANTHROPIC_BASE_URL (default https://api.anthropic.com) + ANTHROPIC_API_KEY.
                     Model names: CULL_PLANNER_MODEL / CULL_REASONS_MODEL.
fake                 keepframe.agent.fake_model.FakeModel (zero network).

``CULL_MODEL_PROVIDER`` is still read as a fallback name for the switch.
Planner thinking is disabled (deterministic tool loop); tool_choice is never forced.

Secrets (anthropic provider only): ECS can inject ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL as container secrets.
AgentCore Runtime has no secret injection, so ``bootstrap_anthropic_secret()`` (called once at process start by
the runtime app and the CLI) reads Secrets Manager ``$CULL_ANTHROPIC_SECRET_ID`` (JSON keys ANTHROPIC_API_KEY,
ANTHROPIC_BASE_URL) when the key is not already in the environment. Values are never logged.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

#: Bedrock defaults are inference profile ids valid in ap-south-1 (the ``in.`` geo profiles route within
#: ap-south-1 / ap-south-2). Override per region with KEEPFRAME_BEDROCK_MODEL_ID / _REASONS_MODEL_ID.
DEFAULTS = {
    "bedrock": {"planner": "in.anthropic.claude-sonnet-5", "reasons": "in.anthropic.claude-haiku-4-5-20251001-v1:0"},
    "anthropic": {"planner": "claude-sonnet-5", "reasons": "claude-haiku-4.5"},
    "fake": {"planner": "fake-planner-v1", "reasons": "fake-reasons-v1"},
}
USER_AGENT = "keepframe/0.1.0"


PROVIDER_ENV = ("KEEPFRAME_LLM_PROVIDER", "CULL_MODEL_PROVIDER")
DEFAULT_PROVIDER = "bedrock"


def provider() -> str:
    raw = next((os.environ[k] for k in PROVIDER_ENV if os.environ.get(k, "").strip()), DEFAULT_PROVIDER)
    p = raw.strip().lower()
    if p not in DEFAULTS:
        raise ValueError(f"KEEPFRAME_LLM_PROVIDER must be bedrock|anthropic|fake, got {p!r}")
    return p


def _model_env(p: str, role: str) -> tuple[str, ...]:
    if p == "bedrock":
        return ("KEEPFRAME_BEDROCK_MODEL_ID",) if role == "planner" else ("KEEPFRAME_BEDROCK_REASONS_MODEL_ID",)
    return ("CULL_PLANNER_MODEL",) if role == "planner" else ("CULL_REASONS_MODEL",)


def planner_model_id(p: str | None = None) -> str:
    p = p or provider()
    return next((os.environ[k] for k in _model_env(p, "planner") if os.environ.get(k)), DEFAULTS[p]["planner"])


def reasons_model_id(p: str | None = None) -> str:
    p = p or provider()
    return next((os.environ[k] for k in _model_env(p, "reasons") if os.environ.get(k)), DEFAULTS[p]["reasons"])


def bedrock_region() -> str:
    return (os.environ.get("KEEPFRAME_BEDROCK_REGION") or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1")


SECRET_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")


def bootstrap_anthropic_secret(client: Any = None) -> str:
    """Fill ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL from Secrets Manager once, if needed.

    Returns what happened: ``env`` (key already set), ``secret`` (read from CULL_ANTHROPIC_SECRET_ID),
    ``not_needed`` (provider is not anthropic). Raises RuntimeError when provider=anthropic and neither the
    env var nor a readable secret is available. Never logs or returns secret values.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "env"
    if provider() != "anthropic":
        return "not_needed"
    secret_id = os.environ.get("CULL_ANTHROPIC_SECRET_ID", "").strip()
    if not secret_id:
        raise RuntimeError("KEEPFRAME_LLM_PROVIDER=anthropic needs ANTHROPIC_API_KEY or CULL_ANTHROPIC_SECRET_ID "
                           "(Secrets Manager id of keepframe/anthropic)")
    if client is None:
        import boto3

        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
        client = boto3.client("secretsmanager", region_name=region)
    try:
        raw = client.get_secret_value(SecretId=secret_id)["SecretString"]
        doc = json.loads(raw)
    except Exception as e:  # noqa: BLE001 - message carries the type only, never the payload
        raise RuntimeError(f"could not read Anthropic secret {secret_id!r}: {type(e).__name__}") from None
    if not isinstance(doc, dict) or not doc.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(f"secret {secret_id!r} has no ANTHROPIC_API_KEY")
    for k in SECRET_KEYS:
        if doc.get(k) and not os.environ.get(k):
            os.environ[k] = str(doc[k])
    sys.stderr.write(json.dumps({"event": "anthropic_secret_loaded", "secret_id": secret_id,
                                 "keys": [k for k in SECRET_KEYS if doc.get(k)]}) + "\n")
    return "secret"


def anthropic_base_url() -> str:
    return (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")


def _anthropic_client_args() -> dict[str, Any]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("KEEPFRAME_LLM_PROVIDER=anthropic needs ANTHROPIC_API_KEY (set it, or set "
                           "CULL_ANTHROPIC_SECRET_ID so bootstrap_anthropic_secret() reads Secrets Manager)")
    return {"base_url": anthropic_base_url(), "api_key": key, "max_retries": 4, "timeout": 120.0,
            "default_headers": {"User-Agent": USER_AGENT}}


def build_planner_model(p: str | None = None) -> Any:
    p = p or provider()
    mid = planner_model_id(p)
    max_tokens = int(os.environ.get("CULL_PLANNER_MAX_TOKENS", "2048"))
    if p == "fake":
        from keepframe.agent.fake_model import FakeModel

        return FakeModel(mid)
    if p == "bedrock":
        from botocore.config import Config
        from strands.models.bedrock import BedrockModel

        return BedrockModel(model_id=mid, max_tokens=max_tokens, region_name=bedrock_region(),
                            boto_client_config=Config(user_agent_extra=USER_AGENT, read_timeout=120,
                                                      retries={"max_attempts": 4, "mode": "adaptive"}))
    if p == "anthropic":
        from strands.models.anthropic import AnthropicModel

        # anthropic>=1.x messages.stream() has no sampling kwargs; thinking off keeps the tool loop terse.
        params: dict[str, Any] = {}
        if os.environ.get("CULL_PLANNER_THINKING", "disabled") == "disabled":
            params = {"thinking": {"type": "disabled"}}
        return AnthropicModel(client_args=_anthropic_client_args(), model_id=mid, max_tokens=max_tokens,
                              params=params)
    raise ValueError(f"unknown provider {p!r}")


def reasons_complete(prompt: str) -> str:
    """One short completion from the reasons model (used only when CULL_REASONS_LLM=1)."""
    p = provider()
    mid = reasons_model_id(p)
    if p == "fake":
        return ""
    if p == "bedrock":
        import boto3

        rt = boto3.client("bedrock-runtime", region_name=bedrock_region())
        resp = rt.converse(modelId=mid, messages=[{"role": "user", "content": [{"text": prompt}]}],
                           inferenceConfig={"maxTokens": 120})
        return "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
    import anthropic
    from anthropic.types import TextBlock

    c = anthropic.Anthropic(**_anthropic_client_args())
    msg = c.messages.create(model=mid, max_tokens=120, messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if isinstance(b, TextBlock))

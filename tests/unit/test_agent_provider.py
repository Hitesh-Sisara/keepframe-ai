"""Model provider selection (keepframe.agent.models): bedrock default, optional anthropic, fake.

No test calls a model: Bedrock objects are only constructed (boto3 builds a client offline), and the
reasons path is checked with a stubbed bedrock-runtime client.
"""

from __future__ import annotations

from typing import Any

import pytest

from keepframe.agent import models

ENV = ("KEEPFRAME_LLM_PROVIDER", "CULL_MODEL_PROVIDER", "KEEPFRAME_BEDROCK_MODEL_ID",
       "KEEPFRAME_BEDROCK_REASONS_MODEL_ID", "KEEPFRAME_BEDROCK_REGION", "CULL_PLANNER_MODEL",
       "CULL_REASONS_MODEL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CULL_ANTHROPIC_SECRET_ID")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    return monkeypatch


def test_default_is_bedrock_with_inference_profile_ids() -> None:
    assert models.provider() == "bedrock"
    assert models.planner_model_id() == "in.anthropic.claude-sonnet-5"
    assert models.reasons_model_id() == "in.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert models.bedrock_region() == "ap-south-1"


def test_new_name_wins_over_legacy_name(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CULL_MODEL_PROVIDER", "fake")
    assert models.provider() == "fake"
    clean_env.setenv("KEEPFRAME_LLM_PROVIDER", " Anthropic ")
    assert models.provider() == "anthropic"
    clean_env.setenv("KEEPFRAME_LLM_PROVIDER", "")
    assert models.provider() == "fake"  # empty value falls through to the legacy name


def test_bedrock_model_ids_configurable(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("KEEPFRAME_BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-5")
    clean_env.setenv("KEEPFRAME_BEDROCK_REASONS_MODEL_ID", "global.anthropic.claude-haiku-5-5")
    clean_env.setenv("CULL_PLANNER_MODEL", "ignored-for-bedrock")
    assert models.planner_model_id() == "global.anthropic.claude-sonnet-5"
    assert models.reasons_model_id() == "global.anthropic.claude-haiku-5-5"
    clean_env.setenv("KEEPFRAME_BEDROCK_REGION", "us-east-1")
    assert models.bedrock_region() == "us-east-1"


def test_anthropic_model_names_and_base_url(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("KEEPFRAME_LLM_PROVIDER", "anthropic")
    assert models.planner_model_id() == "claude-sonnet-5"
    clean_env.setenv("CULL_PLANNER_MODEL", "claude-x")
    clean_env.setenv("KEEPFRAME_BEDROCK_MODEL_ID", "ignored-for-anthropic")
    assert models.planner_model_id() == "claude-x"
    assert models.anthropic_base_url() == "https://api.anthropic.com"
    clean_env.setenv("ANTHROPIC_BASE_URL", "https://llm.example.invalid/v1/")
    assert models.anthropic_base_url() == "https://llm.example.invalid/v1"  # used as given, no suffix


def test_build_planner_bedrock(clean_env: pytest.MonkeyPatch) -> None:
    pytest.importorskip("strands")
    from strands.models.bedrock import BedrockModel

    clean_env.setenv("CULL_PLANNER_MAX_TOKENS", "1024")
    m = models.build_planner_model()
    assert isinstance(m, BedrockModel)
    cfg = m.get_config()
    assert cfg["model_id"] == "in.anthropic.claude-sonnet-5" and cfg["max_tokens"] == 1024
    assert m.client.meta.region_name == "ap-south-1"


def test_build_planner_anthropic_needs_key(clean_env: pytest.MonkeyPatch) -> None:
    pytest.importorskip("strands")
    clean_env.setenv("KEEPFRAME_LLM_PROVIDER", "anthropic")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        models.build_planner_model()
    clean_env.setenv("ANTHROPIC_API_KEY", "k-test")
    from strands.models.anthropic import AnthropicModel

    assert isinstance(models.build_planner_model(), AnthropicModel)


def test_bedrock_needs_no_secret() -> None:
    class NoCall:
        def get_secret_value(self, **_: Any) -> None:
            raise AssertionError("bedrock must not read a secret")

    assert models.bootstrap_anthropic_secret(NoCall()) == "not_needed"


def test_reasons_bedrock_uses_converse(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class StubRuntime:
        def converse(self, **kw: Any) -> dict[str, Any]:
            calls.append(kw)
            return {"output": {"message": {"content": [{"text": "Eyes closed "}, {"text": "on the main face."}]}}}

    import boto3

    monkeypatch.setattr(boto3, "client", lambda name, **kw: StubRuntime() if name == "bedrock-runtime" else None)
    assert models.reasons_complete("why?") == "Eyes closed on the main face."
    assert calls[0]["modelId"] == "in.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert calls[0]["inferenceConfig"] == {"maxTokens": 120}

"""Unit tests for the ValidateManifest secret placeholder check (no AWS: clients are stubbed)."""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("WORK_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
sys.path.insert(0, str(Path(__file__).parent))

import validate_manifest as vm  # noqa: E402


class FakeSM:
    def __init__(self, docs: dict[str, object]) -> None:
        self.docs = docs

    def get_secret_value(self, SecretId: str) -> dict[str, str]:  # noqa: N803 (boto3 casing)
        return {"SecretString": json.dumps(self.docs[SecretId])}


def fake_ssm(provider: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(get_parameter=lambda Name: {"Parameter": {"Value": provider}})  # noqa: N803


LLM_OK = {"ANTHROPIC_API_KEY": "k" * 40, "ANTHROPIC_BASE_URL": "https://example.invalid"}


def setup(monkeypatch: pytest.MonkeyPatch, llm: dict, provider: str = "anthropic") -> None:
    monkeypatch.setattr(vm, "_sm", FakeSM({vm.ANTHROPIC_SECRET: llm}))
    monkeypatch.setattr(vm, "_ssm", fake_ssm(provider))


def test_all_set_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    setup(monkeypatch, LLM_OK)
    vm.check_secrets({})


def test_placeholder_fails_with_key_names_only(monkeypatch: pytest.MonkeyPatch) -> None:
    setup(monkeypatch, {**LLM_OK, "ANTHROPIC_API_KEY": vm.PLACEHOLDER})
    with pytest.raises(vm.SecretNotSet) as e:
        vm.check_secrets({})
    assert "ANTHROPIC_API_KEY" in str(e.value)
    assert "example.invalid" not in str(e.value)


def test_default_bedrock_provider_needs_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_llm = {k: vm.PLACEHOLDER for k in LLM_OK}
    setup(monkeypatch, bad_llm, provider="bedrock")
    vm.check_secrets({})
    vm.check_secrets({"sync_source": True})
    setup(monkeypatch, bad_llm, provider="anthropic")
    with pytest.raises(vm.SecretNotSet, match="ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL"):
        vm.check_secrets({})
    vm.check_secrets({"skip_agent": True})


def test_empty_value_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    setup(monkeypatch, {**LLM_OK, "ANTHROPIC_BASE_URL": "  "})
    with pytest.raises(vm.SecretNotSet, match="ANTHROPIC_BASE_URL"):
        vm.check_secrets({})

"""Reason text (P9): a <= 140-char explanation built from the tool numbers.

Template first (keepframe.worker.policies.reason_text, shared with the worker). An optional rewrite by the
reasons model (Haiku 4.5 via the same provider switch) runs only with ``CULL_REASONS_LLM=1``; any failure,
empty answer or over-length answer falls back to the template, so a verdict always has a valid text.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from keepframe.worker import policies as wp

MAX_LEN = 140


def template_reason(verdict: str, codes: Sequence[str], scores: Mapping[str, Any] | None = None,
                    group_size: int | None = None) -> str:
    return wp.reason_text(verdict, list(codes), dict(scores or {}), group_size)[:MAX_LEN]


def rewrite_enabled() -> bool:
    return os.environ.get("CULL_REASONS_LLM", "0") == "1"


def rewrite(text: str, verdict: str, codes: Sequence[str]) -> str:
    """One short call to the reasons model; returns the template on any problem."""
    if not rewrite_enabled():
        return text
    try:
        from keepframe.agent.models import reasons_complete

        prompt = (
            "Rewrite this photo-culling verdict for a photographer in plain words, at most 140 characters, "
            "keep every number, no preamble, no quotes.\n"
            f"Verdict: {verdict}. Codes: {', '.join(codes)}.\nTemplate: {text}"
        )
        out = (reasons_complete(prompt) or "").strip().strip('"')
        return out if 0 < len(out) <= MAX_LEN else text
    except Exception:  # noqa: BLE001 - the template is always a valid answer
        return text

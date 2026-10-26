"""EMF metrics via aws-embedded-metrics (namespace ``KeepFrame``).

Metrics (docs/ARCHITECTURE.md "Components"): photos_processed, stage_latency_ms{stage}, tool_calls{tool}, escalations,
run_duration_s, opencv_flavor (1 per run, dimension flavor).

The library's stdout sink writes one EMF JSON document per flush, which CloudWatch Logs turns into metrics
with no agent and no PutMetricData call (``AWS_EMF_ENVIRONMENT=Local`` forces stdout). ``CULL_METRICS=off``
disables emission (unit tests).
"""

from __future__ import annotations

import os
from typing import Any

NAMESPACE = os.environ.get("CULL_METRICS_NAMESPACE", "KeepFrame")


def enabled() -> bool:
    return os.environ.get("CULL_METRICS", "on") != "off"


def _emit(metrics: dict[str, tuple[float, str]], dims: dict[str, str], props: dict[str, Any] | None = None) -> None:
    if not enabled():
        return
    os.environ.setdefault("AWS_EMF_ENVIRONMENT", "Local")  # stdout sink (Fargate awslogs / AgentCore logs)
    from aws_embedded_metrics.logger.metrics_logger_factory import create_metrics_logger

    m = create_metrics_logger()
    m.set_namespace(NAMESPACE)
    m.set_dimensions(dims)
    for name, (value, unit) in metrics.items():
        m.put_metric(name, float(value), unit)
    for k, v in (props or {}).items():
        m.set_property(k, v)
    m.flush_sync()


def tool_call(tool: str, latency_ms: float, run_id: str, ok: bool = True) -> None:
    _emit({"tool_calls": (1, "Count"), "stage_latency_ms": (latency_ms, "Milliseconds")},
          {"tool": tool}, {"run_id": run_id, "ok": ok})


def stage_latency(stage: str, latency_ms: float, run_id: str) -> None:
    _emit({"stage_latency_ms": (latency_ms, "Milliseconds")}, {"stage": stage}, {"run_id": run_id})


def escalation(kind: str, run_id: str, n: int = 1) -> None:
    _emit({"escalations": (n, "Count")}, {"kind": kind}, {"run_id": run_id})


def run_done(run_id: str, flavor: str, photos: int, duration_s: float, provider: str) -> None:
    _emit({"photos_processed": (photos, "Count"), "run_duration_s": (duration_s, "Seconds"),
           "opencv_flavor": (1, "Count")}, {"flavor": flavor}, {"run_id": run_id, "llm_provider": provider})

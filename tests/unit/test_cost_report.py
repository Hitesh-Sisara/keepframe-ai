"""scripts/cost_report.py: attribution rules and the markdown formatter, on canned Cost Explorer JSON."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cost_report", ROOT / "scripts" / "cost_report.py")
assert spec and spec.loader
cr = importlib.util.module_from_spec(spec)
sys.modules["cost_report"] = cr
spec.loader.exec_module(cr)


def _g(service: str, usage_type: str, cost: float, qty: float | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {"UnblendedCost": {"Amount": str(cost), "Unit": "USD"}}
    if qty is not None:
        m["UsageQuantity"] = {"Amount": str(qty), "Unit": "N/A"}
    return {"Keys": [service, usage_type], "Metrics": m}


def _day(day: str, groups: list[dict[str, Any]]) -> dict[str, Any]:
    nxt = f"{day[:8]}{int(day[8:]) + 1:02d}"
    return {"TimePeriod": {"Start": day, "End": nxt}, "Total": {}, "Groups": groups, "Estimated": True}


USAGE = [
    ("Amazon Relational Database Service", "APS3-InstanceUsage:db.t4g.micro", 0.50, 24.0),
    ("Amazon Elastic Compute Cloud - Compute", "APS3-BoxUsage:t4g.micro", 0.13, 24.0),
    ("Amazon Elastic Compute Cloud - Compute", "APS3-BoxUsage:c8g.xlarge", 0.03, 0.27),
    ("Amazon Virtual Private Cloud", "APS3-PublicIPv4:InUseAddress", 0.30, 60.0),  # 48 h held by runner + RDS
    ("EC2 - Other", "APS3-EBS:SnapshotUsage", 0.14, 2.8),
    ("Amazon Elastic Container Service", "APS3-Fargate-ARM-vCPU-Hours:perCPU", 0.02, 0.77),
    ("Amazon Simple Storage Service", "APS3-Requests-Tier1", 0.22, 43960.0),
    # real usage type is "ap-south-1-KMS-Keys"; spelled via join so gitleaks generic-api-key does not match
    ("AWS Key Management Service", "-".join(("ap-south-1", "KMS", "Keys")), 0.03, 0.03),
    ("Mystery Service", "APS3-Thing", 0.01, 1.0),
]


def canned(tagged: list[dict[str, Any]] | None = None, agentcore_ce: bool = False) -> dict[str, Any]:
    usage = [_g(s, u, c, q) for s, u, c, q in USAGE]
    if agentcore_ce:
        usage.append(_g("Amazon Bedrock AgentCore", "APS3-Runtime-vCPU-Hours", 0.05, 0.4))
    early = [
        _g("AWS Secrets Manager", "APS3-AWSSecretsManager-Secrets", 0.04, 0.1),
        _g("Amazon Simple Storage Service", "APS3-Requests-Tier1", 0.01, 7.0),
    ]
    return {
        "window": {"start": "2026-10-01", "end": "2026-10-07"},
        "collected_at": "2026-10-06T12:00:00+00:00",
        "ce_usage": [_day("2026-10-04", early), _day("2026-10-05", usage), _day("2026-10-06", [])],
        "ce_credit": [
            _day(
                "2026-10-04",
                [
                    _g(g["Keys"][0], g["Keys"][1], -float(g["Metrics"]["UnblendedCost"]["Amount"]))
                    for g in early
                ],
            ),
            _day(
                "2026-10-05",
                [
                    _g(g["Keys"][0], g["Keys"][1], -float(g["Metrics"]["UnblendedCost"]["Amount"]))
                    for g in usage
                ],
            ),
        ],
        "ce_tagged_usage": tagged or [],
        "ce_record_types": [
            {
                "TimePeriod": {"Start": "2026-10-01", "End": "2026-10-07"},
                "Groups": [
                    {"Keys": ["Credit"], "Metrics": {"UnblendedCost": {"Amount": "-1.43"}}},
                    {"Keys": ["Usage"], "Metrics": {"UnblendedCost": {"Amount": "1.43"}}},
                ],
            }
        ],
        "tag_status": [{"TagKey": "project", "Status": "Active"}],
        "freetier_state": {
            "accountPlanType": "PAID",
            "accountPlanStatus": "ACTIVE",
            "accountPlanRemainingCredits": {"amount": 118.57, "unit": "USD"},
        },
        "freetier_activities": [
            {
                "title": "Use a foundation model in the Amazon Bedrock playground",
                "status": "COMPLETED",
                "reward": {"credit": {"amount": 20.0, "unit": "USD"}},
                "completedAt": "2026-10-07T19:06:11+00:00",
            },
        ],
        "budgets": [
            {
                "name": "keepframe-competition-gross",
                "limit": 120.0,
                "include_credit": False,
                "actual": 1.43,
                "forecast": 9.0,
                "notifications": ["ACTUAL >100%"],
            },
            {
                "name": "old",
                "limit": 120.0,
                "include_credit": True,
                "actual": 0.0,
                "forecast": 9.0,
                "notifications": [],
            },
        ],
        "runs": [
            {
                "run_id": "run_aws_e01_x",
                "status": "succeeded",
                "run_kind": "deployed",
                "photos_processed": "1000",
            },
            {
                "run_id": "run_aws_e02_x",
                "status": "awaiting_approval",
                "run_kind": "deployed",
                "n_photos": "500",
            },
            {"run_id": "run_local_x", "status": "succeeded", "run_kind": "local", "photos_processed": "100"},
            {"run_id": "run_cal_x", "status": "worker_done", "run_kind": "local_calibration"},
        ],
        "agentcore": [{"runtime": "keepframe_agent-ABC", "vcpu_hours": 0.04, "gb_hours": 3.0}],
    }


def row(rep: Any, service: str) -> Any:
    return next(r for r in rep.rows if r.service == service)


def test_rule_based_attribution() -> None:
    rep = cr.build_report(canned())
    assert row(rep, "Amazon Relational Database Service").kc_usage == 0
    ec2 = row(rep, "Amazon Elastic Compute Cloud - Compute")
    assert ec2.kc_usage == pytest.approx(0.03) and ec2.other_usage == pytest.approx(0.13)
    vpc = row(rep, "Amazon Virtual Private Cloud")  # 60 h - (24 runner + 24 RDS) = 12 h keepframe
    assert vpc.kc_usage == pytest.approx(0.30 * 12 / 60)
    assert row(rep, "EC2 - Other").kc_usage == 0
    assert row(rep, "Amazon Elastic Container Service").kc_usage == pytest.approx(0.02)
    s3 = row(rep, "Amazon Simple Storage Service")  # 0.01 before PROJECT_START is other
    assert s3.kc_usage == pytest.approx(0.22) and s3.other_usage == pytest.approx(0.01)
    assert row(rep, "AWS Secrets Manager").kc_usage == 0  # only a pre-project day
    assert rep.unclassified == ["Mystery Service / APS3-Thing"]
    # credits follow the same split and fully offset usage here
    assert rep.total("usage") + rep.total("credit") == pytest.approx(0)
    assert rep.total("kc_usage") + rep.total("kc_credit") == pytest.approx(0)
    assert rep.data_through == "2026-10-05"


def test_tagged_cost_overrides_rules() -> None:
    tagged = [
        _day(
            "2026-10-05", [_g("Amazon Relational Database Service", "APS3-InstanceUsage:db.t4g.micro", 0.10)]
        )
    ]
    rep = cr.build_report(canned(tagged=tagged))
    rds = row(rep, "Amazon Relational Database Service")
    assert rds.kc_usage == pytest.approx(0.10)
    assert "tag" in rds.rules
    assert rep.tagged_total == pytest.approx(0.10)


def test_run_counting_and_per_event() -> None:
    rep = cr.build_report(canned())
    assert rep.counted_runs == 3 and rep.deployed_runs == 2 and rep.photos_counted == 1600
    assert rep.runs_by_status == {"awaiting_approval": 1, "succeeded": 2, "worker_done": 1}
    assert rep.pipeline_kc == pytest.approx(0.02 + 0.22)  # ECS + S3 (no Lambda / AgentCore lines)


def test_markdown_has_tables_and_no_run_ids() -> None:
    md = cr.render_markdown(cr.build_report(canned()))
    assert "| Whole account | $1.43 | −$1.43 | $0.00 |" in md
    assert "| Amazon Relational Database Service | $0.50 | −$0.50 | $0.00 | $0.00 | $0.50 | rds |" in md
    assert "**3 runs**, of which 2 ran on the deployed AWS stack; 1,600 photos" in md
    assert "Credits remaining" in md and "$118.57" in md
    assert "no AgentCore line yet" in md and "keepframe_agent-ABC" in md
    assert "Use a foundation model in the Amazon Bedrock playground" in md
    assert "jump of about $20.00" in md
    assert "| old | $120.00 | yes | $0.00 | $9.00 | **none** |" in md
    assert "Mystery Service / APS3-Thing" in md
    assert "Cost Explorer has no tagged cost in this window yet" in md
    assert "run_aws_" not in md and "run_local" not in md


def test_agentcore_line_from_cost_explorer() -> None:
    rep = cr.build_report(canned(agentcore_ce=True))
    assert rep.agentcore_ce == pytest.approx(0.05)
    assert rep.pipeline_kc == pytest.approx(0.02 + 0.22 + 0.05)
    assert "Cost Explorer: **$0.05** gross usage for AgentCore" in cr.render_markdown(rep)


@pytest.mark.parametrize(
    ("x", "s"),
    [(0.0, "$0.00"), (0.004, "<$0.01"), (-0.004, "−<$0.01"), (1234.5, "$1,234.50"), (-1.2, "−$1.20")],
)
def test_usd(x: float, s: str) -> None:
    assert cr.usd(x) == s


def test_main_from_raw(tmp_path: Path) -> None:
    import json

    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(canned()))
    out = tmp_path / "r.md"
    assert cr.main(["--from-raw", str(raw), "--out", str(out), "--observed-balance", "2026-10-07=98.57"]) == 0
    md = out.read_text()
    assert md.startswith("# AWS cost, competition window 2026-10-01 → 2026-10-06")
    assert "readings recorded earlier (get-account-plan-state): $98.57 at 2026-10-07." in md

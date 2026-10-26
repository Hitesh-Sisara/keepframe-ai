#!/usr/bin/env python3
"""Measured AWS cost for the competition window, keepframe vs the rest of the account.

    set -a; . ~/.config/keepframe/.env; set +a
    python scripts/cost_report.py                              # 2026-10-01 -> today, writes bench/results/cost/<today>.md
    python scripts/cost_report.py --save-raw /tmp/cost_raw.json
    python scripts/cost_report.py --from-raw /tmp/cost_raw.json # re-render offline, no API calls

Sources: Cost Explorer (us-east-1; RECORD_TYPE Usage vs Credit, daily, SERVICE x USAGE_TYPE, plus the
same query filtered on the `project=keepframe` cost-allocation tag), Free Tier plan state and credit
activities, AWS Budgets, the RUN# items in keepframe-decisions (gsi1 "RUNS"), and AgentCore Runtime
usage metrics from CloudWatch. Every Cost Explorer request is billed at $0.01; one run makes ~6.

Attribution. The account also hosts resources that are not keepframe (a gh-runner t4g.micro with a
public IPv4, a sellphotos RDS instance and its snapshot, archived EBS snapshots and a customer KMS key).
Each daily cost line is split by the `project` tag where Cost Explorer has tagged data, and the untagged
remainder by the usage-type rules in RULES. The report says which basis each service used.

The committed report holds counts and dollars only: RUN# ids embed real event ids, so the raw dump
(--save-raw) must stay out of the repo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Set AWS_ACCOUNT_ID to the project account (budgets API needs it explicitly).
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "<AWS_ACCOUNT_ID>")
PROJECT = "keepframe"
PROJECT_REGION = "ap-south-1"
WINDOW_START = "2026-10-01"
#: keepframe resources first existed on this day; untagged shared-service lines before it are "other".
PROJECT_START = "2026-10-05"
DECISIONS_TABLE = "keepframe-decisions"
COUNTED_STATUSES = ("succeeded", "awaiting_approval")
#: Services whose keepframe share forms the per-event pipeline cost (worker, agent, API, storage).
PIPELINE_SERVICES = ("Amazon Elastic Container Service", "AWS Lambda", "Amazon Simple Storage Service")
AGENTCORE_RE = re.compile(r"agentcore", re.IGNORECASE)
#: AgentCore Runtime list prices (aws.amazon.com/bedrock/agentcore/pricing, read 2026-10-08), used only
#: when Cost Explorer has no AgentCore line yet: (label, $/vCPU-hour, $/GB-hour).
AGENTCORE_PRICES = (("v1", 0.0895, 0.00945), ("v2 consumption", 0.1276, 0.0169))

KC = "keepframe"
OTHER = "other"

#: rule id -> description, rendered in the report.
RULES: dict[str, str] = {
    "tag": "Cost Explorer reports the line under the `project=keepframe` cost-allocation tag.",
    "rds": "Amazon RDS: the sellphotos instance (deleted 2026-10-04) and its final snapshot. keepframe has no RDS.",
    "runner": "EC2 t4g.micro instance hours: the pre-existing gh-runner orchestrator.",
    "bench-ec2": "Other EC2 instance types (c8g/c7g/c7i): self-terminating keepframe benchmark hosts.",
    "ebs-snapshot": "EBS snapshot storage: archived snapshots moved in from another account (not keepframe).",
    "ebs-volume": "EBS volume storage: the gh-runner root volume (bench root volumes live minutes, < $0.01).",
    "ipv4": "Public IPv4 hours: the gh-runner and the RDS instance hold one address while they run "
    "(hours = that day's t4g.micro + db.* instance hours); the remainder is keepframe "
    "(Fargate tasks in public subnets, bench hosts).",
    "kms-cmk": "KMS key-months: the only customer-managed key is the archived-snapshot key (AWS-managed keys are free).",
    "ses": "Amazon SES: not used by keepframe (sellphotos).",
    "not-project": "Lightsail, Glue, Bedrock model invocations (two playground calls that earned an activity credit).",
    "project-service": "Services only keepframe uses (Secrets Manager keepframe/*, CloudTrail keepframe-trail, "
    "ECS, ECR, DynamoDB, Step Functions, Lambda, API Gateway, CloudWatch, SNS, X-Ray, AgentCore), from "
    f"{PROJECT_START} on; earlier lines are other.",
    "date": f"Shared services (S3, Cost Explorer API, data transfer incl. EC2 bytes, CloudFormation): keepframe from "
    f"{PROJECT_START}, "
    "other before.",
    "unclassified": "No rule matched: counted as other and listed under caveats.",
}

PROJECT_ONLY_SERVICES = {
    "AWS Secrets Manager",
    "AWS CloudTrail",
    "Amazon Elastic Container Service",
    "Amazon EC2 Container Registry (ECR)",
    "Amazon DynamoDB",
    "AWS Step Functions",
    "AWS Lambda",
    "Amazon API Gateway",
    "AmazonCloudWatch",
    "Amazon Simple Notification Service",
    "AWS X-Ray",
}
DATE_SPLIT_SERVICES = {
    "Amazon Simple Storage Service",
    "AWS Cost Explorer",
    "AWS Data Transfer",
    "AWS CloudFormation",
}
NOT_PROJECT_SERVICES = {"Amazon Lightsail", "AWS Glue", "Amazon Bedrock"}


# ----------------------------------------------------------------------------------------- collection


def _ce_pages(ce: Any, **kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        args = dict(kw, NextPageToken=token) if token else kw
        resp: dict[str, Any] = ce.get_cost_and_usage(**args)
        out.extend(resp["ResultsByTime"])
        token = resp.get("NextPageToken")
        if not token:
            return out


def collect(start: str, end: str) -> dict[str, Any]:
    """Every API call the report needs; returns a JSON-serialisable bundle."""
    import boto3  # deferred so --from-raw and the unit tests need no AWS SDK session

    ce: Any = boto3.client("ce", region_name="us-east-1")
    ft: Any = boto3.client("freetier", region_name="us-east-1")
    bud: Any = boto3.client("budgets", region_name="us-east-1")
    ddb: Any = boto3.client("dynamodb", region_name=PROJECT_REGION)
    cw: Any = boto3.client("cloudwatch", region_name=PROJECT_REGION)

    period = {"Start": start, "End": end}
    by_line = [{"Type": "DIMENSION", "Key": "SERVICE"}, {"Type": "DIMENSION", "Key": "USAGE_TYPE"}]

    def rt(v: str) -> dict[str, Any]:
        return {"Dimensions": {"Key": "RECORD_TYPE", "Values": [v]}}

    tag_filter = {"Tags": {"Key": "project", "Values": [PROJECT]}}
    raw: dict[str, Any] = {
        "window": {"start": start, "end": end},
        "collected_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "ce_usage": _ce_pages(
            ce,
            TimePeriod=period,
            Granularity="DAILY",
            Metrics=["UnblendedCost", "UsageQuantity"],
            Filter=rt("Usage"),
            GroupBy=by_line,
        ),
        "ce_credit": _ce_pages(
            ce,
            TimePeriod=period,
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            Filter=rt("Credit"),
            GroupBy=by_line,
        ),
        "ce_tagged_usage": _ce_pages(
            ce,
            TimePeriod=period,
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            Filter={"And": [rt("Usage"), tag_filter]},
            GroupBy=by_line,
        ),
        "ce_record_types": _ce_pages(
            ce,
            TimePeriod={"Start": "2026-08-01", "End": end},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "RECORD_TYPE"}],
        ),
    }
    tags = ce.list_cost_allocation_tags(TagKeys=["project"])["CostAllocationTags"]
    raw["tag_status"] = [{"TagKey": t["TagKey"], "Status": t["Status"]} for t in tags]
    raw["freetier_state"] = {k: v for k, v in ft.get_account_plan_state().items() if k != "ResponseMetadata"}
    acts = []
    for a in ft.list_account_activities()["activities"]:
        d = ft.get_account_activity(activityId=a["activityId"])
        acts.append(
            {
                k: (v.isoformat() if isinstance(v, dt.datetime) else v)
                for k, v in d.items()
                if k in ("title", "status", "reward", "startedAt", "completedAt")
            }
        )
    raw["freetier_activities"] = acts
    raw["budgets"] = _budgets(bud)
    raw["runs"] = _runs(ddb)
    raw["agentcore"] = _agentcore(cw, start, end)
    return raw


def _budgets(bud: Any) -> list[dict[str, Any]]:
    out = []
    for b in bud.describe_budgets(AccountId=ACCOUNT_ID).get("Budgets", []):
        notes = bud.describe_notifications_for_budget(AccountId=ACCOUNT_ID, BudgetName=b["BudgetName"])
        out.append(
            {
                "name": b["BudgetName"],
                "limit": float(b["BudgetLimit"]["Amount"]),
                "include_credit": bool(b["CostTypes"].get("IncludeCredit", True)),
                "actual": float(b.get("CalculatedSpend", {}).get("ActualSpend", {}).get("Amount", 0)),
                "forecast": float(b.get("CalculatedSpend", {}).get("ForecastedSpend", {}).get("Amount", 0)),
                "notifications": [
                    f"{n['NotificationType']} >{n['Threshold']:g}%" for n in notes.get("Notifications", [])
                ],
            }
        )
    return out


def _runs(ddb: Any) -> list[dict[str, Any]]:
    """RUN# items, minimal fields (ids are dropped by the formatter; they embed real event ids)."""
    items: list[dict[str, Any]] = []
    kw: dict[str, Any] = {
        "TableName": DECISIONS_TABLE,
        "IndexName": "gsi1",
        "KeyConditionExpression": "gsi1pk = :r",
        "ExpressionAttributeValues": {":r": {"S": "RUNS"}},
        "ProjectionExpression": "run_id, #s, run_kind, agent_host, photos_processed, n_photos, started_at",
        "ExpressionAttributeNames": {"#s": "status"},
    }
    while True:
        resp = ddb.query(**kw)
        for it in resp["Items"]:
            items.append({k: next(iter(v.values())) for k, v in it.items()})
        if "LastEvaluatedKey" not in resp:
            return items
        kw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _agentcore(cw: Any, start: str, end: str) -> list[dict[str, Any]]:
    out = []
    t0 = dt.datetime.fromisoformat(start).replace(tzinfo=dt.UTC)
    t1 = dt.datetime.fromisoformat(end).replace(tzinfo=dt.UTC)
    seen: set[str] = set()
    for m in cw.list_metrics(Namespace="AWS/Bedrock-AgentCore", MetricName="CPUUsed-vCPUHours")["Metrics"]:
        dims = {d["Name"]: d["Value"] for d in m["Dimensions"]}
        if set(dims) != {"Resource", "Service"} or dims["Resource"] in seen:
            continue
        seen.add(dims["Resource"])
        row: dict[str, Any] = {"runtime": dims["Resource"].rsplit("/", 1)[-1]}
        for metric, key in (("CPUUsed-vCPUHours", "vcpu_hours"), ("MemoryUsed-GBHours", "gb_hours")):
            pts = cw.get_metric_statistics(
                Namespace="AWS/Bedrock-AgentCore",
                MetricName=metric,
                Dimensions=m["Dimensions"],
                StartTime=t0,
                EndTime=t1,
                Period=86400,
                Statistics=["Sum"],
            )["Datapoints"]
            row[key] = sum(float(p["Sum"]) for p in pts)
        out.append(row)
    return sorted(out, key=lambda r: str(r["runtime"]))


# ------------------------------------------------------------------------------------------- analysis


@dataclass
class Line:
    date: str
    service: str
    usage_type: str
    usage: float = 0.0
    qty: float = 0.0
    credit: float = 0.0
    tagged: float = 0.0
    #: bucket -> share of `usage` (sums to 1 when usage != 0)
    split: dict[str, float] = field(default_factory=dict)
    rules: set[str] = field(default_factory=set)


@dataclass
class ServiceRow:
    service: str
    usage: float = 0.0
    credit: float = 0.0
    kc_usage: float = 0.0
    kc_credit: float = 0.0
    rules: set[str] = field(default_factory=set)

    @property
    def net(self) -> float:
        return self.usage + self.credit

    @property
    def other_usage(self) -> float:
        return self.usage - self.kc_usage


@dataclass
class Report:
    start: str
    end: str
    collected_at: str
    data_through: str | None
    rows: list[ServiceRow]
    tag_active: bool
    tagged_total: float
    runs_by_status: dict[str, int]
    counted_runs: int
    deployed_runs: int
    photos_counted: int
    agentcore_ce: float | None
    agentcore_cw: list[dict[str, Any]]
    freetier: dict[str, Any]
    activities: list[dict[str, Any]]
    credits_by_day: list[tuple[str, float, float]]
    lifetime_credit: float
    budgets: list[dict[str, Any]]
    unclassified: list[str]
    observed_balances: list[tuple[str, float]] = field(default_factory=list)

    def total(self, attr: str) -> float:
        return sum(float(getattr(r, attr)) for r in self.rows)

    @property
    def pipeline_kc(self) -> float:
        return sum(
            r.kc_usage for r in self.rows if r.service in PIPELINE_SERVICES or AGENTCORE_RE.search(r.service)
        )


def _amount(g: dict[str, Any], metric: str = "UnblendedCost") -> float:
    return float(g["Metrics"].get(metric, {}).get("Amount", 0) or 0)


def _lines(raw: dict[str, Any]) -> dict[tuple[str, str, str], Line]:
    lines: dict[tuple[str, str, str], Line] = {}

    def get(day: str, keys: list[str]) -> Line:
        k = (day, keys[0], keys[1] if len(keys) > 1 else "")
        if k not in lines:
            lines[k] = Line(day, k[1], k[2])
        return lines[k]

    for res in raw["ce_usage"]:
        for g in res.get("Groups", []):
            ln = get(res["TimePeriod"]["Start"], g["Keys"])
            ln.usage += _amount(g)
            ln.qty += _amount(g, "UsageQuantity")
    for res in raw["ce_credit"]:
        for g in res.get("Groups", []):
            get(res["TimePeriod"]["Start"], g["Keys"]).credit += _amount(g)
    for res in raw.get("ce_tagged_usage", []):
        for g in res.get("Groups", []):
            get(res["TimePeriod"]["Start"], g["Keys"]).tagged += _amount(g)
    return lines


def _ip_holder_hours(lines: dict[tuple[str, str, str], Line]) -> dict[str, float]:
    """Per day, instance hours of the non-project resources that hold a public IPv4 while running."""
    hours: dict[str, float] = defaultdict(float)
    for ln in lines.values():
        if ln.usage_type.endswith("BoxUsage:t4g.micro") or (
            ln.service == "Amazon Relational Database Service" and ":db." in ln.usage_type
        ):
            hours[ln.date] += ln.qty
    return hours


def classify(ln: Line, ip_other_hours: float) -> dict[str, float]:
    """Split one untagged line between keepframe and other; records the rule on the line."""
    s, u, after = ln.service, ln.usage_type, ln.date >= PROJECT_START

    def one(bucket: str, rule: str) -> dict[str, float]:
        ln.rules.add(rule)
        return {bucket: 1.0}

    if s == "Amazon Relational Database Service":
        return one(OTHER, "rds")
    if s == "Amazon Elastic Compute Cloud - Compute":
        return one(OTHER, "runner") if u.endswith("BoxUsage:t4g.micro") else one(KC, "bench-ec2")
    if s == "EC2 - Other":
        if "SnapshotUsage" in u:
            return one(OTHER, "ebs-snapshot")
        if "VolumeUsage" in u:
            return one(OTHER, "ebs-volume")
        if "Bytes" in u:
            return one(KC if after else OTHER, "date")
    if s == "Amazon Virtual Private Cloud" and "PublicIPv4" in u:
        ln.rules.add("ipv4")
        if ln.qty <= 0:
            return {OTHER: 1.0}
        kc = max(0.0, ln.qty - ip_other_hours) / ln.qty
        return {KC: kc, OTHER: 1.0 - kc}
    if s == "AWS Key Management Service":
        return one(OTHER, "kms-cmk")
    if s == "Amazon Simple Email Service":
        return one(OTHER, "ses")
    if s in NOT_PROJECT_SERVICES:
        return one(OTHER, "not-project")
    if s in PROJECT_ONLY_SERVICES or AGENTCORE_RE.search(s):
        return one(KC if after else OTHER, "project-service")
    if s in DATE_SPLIT_SERVICES:
        return one(KC if after else OTHER, "date")
    return one(OTHER, "unclassified")


def build_report(raw: dict[str, Any]) -> Report:
    lines = _lines(raw)
    ip_hours = _ip_holder_hours(lines)
    rows: dict[str, ServiceRow] = {}
    unclassified: set[str] = set()
    for ln in lines.values():
        if ln.service == "Tax" and ln.usage == 0 and ln.credit == 0:
            continue
        tagged = min(max(ln.tagged, 0.0), ln.usage) if ln.usage > 0 else 0.0
        rest = classify(ln, ip_hours.get(ln.date, 0.0))
        if ln.usage:
            t_share = tagged / ln.usage
            if t_share:
                ln.rules.add("tag")
            ln.split = {b: (1 - t_share) * f for b, f in rest.items()}
            ln.split[KC] = ln.split.get(KC, 0.0) + t_share
        else:
            ln.split = rest  # credit-only line: same rule, no usage to weight by
        if "unclassified" in ln.rules:
            unclassified.add(f"{ln.service} / {ln.usage_type}")
        row = rows.setdefault(ln.service, ServiceRow(ln.service))
        kc = ln.split.get(KC, 0.0)
        row.usage += ln.usage
        row.credit += ln.credit
        row.kc_usage += ln.usage * kc
        row.kc_credit += ln.credit * kc
        if ln.usage or ln.credit:
            row.rules |= ln.rules

    days = sorted({ln.date for ln in lines.values() if ln.usage > 0})
    by_day: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for ln in lines.values():
        by_day[ln.date][0] += ln.usage
        by_day[ln.date][1] += ln.credit

    runs = raw.get("runs", [])
    status_counts: dict[str, int] = defaultdict(int)
    for r in runs:
        status_counts[str(r.get("status", "?"))] += 1
    counted = [r for r in runs if r.get("status") in COUNTED_STATUSES]
    deployed = [
        r
        for r in counted
        if r.get("run_kind") == "deployed" or str(r.get("run_id", "")).startswith("run_aws_")
    ]

    def photos(r: dict[str, Any]) -> int:
        return int(float(r.get("photos_processed") or r.get("n_photos") or 0))

    ac_services = [r for r in rows.values() if AGENTCORE_RE.search(r.service)]
    lifetime = sum(
        _amount(g)
        for res in raw.get("ce_record_types", [])
        for g in res.get("Groups", [])
        if g["Keys"] == ["Credit"]
    )
    return Report(
        start=raw["window"]["start"],
        end=raw["window"]["end"],
        collected_at=raw.get("collected_at", "?"),
        data_through=days[-1] if days else None,
        rows=sorted((r for r in rows.values() if r.usage or r.credit), key=lambda r: (-r.usage, r.service)),
        tag_active=any(t["Status"] == "Active" for t in raw.get("tag_status", [])),
        tagged_total=sum(ln.tagged for ln in lines.values()),
        runs_by_status=dict(sorted(status_counts.items())),
        counted_runs=len(counted),
        deployed_runs=len(deployed),
        photos_counted=sum(photos(r) for r in counted),
        agentcore_ce=sum(r.usage for r in ac_services) if ac_services else None,
        agentcore_cw=list(raw.get("agentcore", [])),
        freetier=dict(raw.get("freetier_state", {})),
        activities=sorted(raw.get("freetier_activities", []), key=lambda a: str(a.get("completedAt", ""))),
        credits_by_day=[(d, v[0], v[1]) for d, v in sorted(by_day.items())],
        lifetime_credit=lifetime,
        budgets=list(raw.get("budgets", [])),
        unclassified=sorted(unclassified),
        observed_balances=sorted((str(k), float(v)) for k, v in raw.get("observed_balances", {}).items()),
    )


# ------------------------------------------------------------------------------------------ rendering


def usd(x: float, signed: bool = False) -> str:
    if x == 0:
        return "$0.00"
    if abs(x) < 0.005:
        return "<$0.01" if x > 0 else "−<$0.01"
    s = f"${abs(x):,.2f}"
    return ("−" if x < 0 else ("+" if signed else "")) + s


def per(x: float, n: int) -> str:
    return usd(x / n) if n else "n/a (0 runs)"


def render_markdown(rep: Report) -> str:
    g, c = rep.total("usage"), rep.total("credit")
    kg, kcr = rep.total("kc_usage"), rep.total("kc_credit")
    end_incl = (dt.date.fromisoformat(rep.end) - dt.timedelta(days=1)).isoformat()
    out: list[str] = [
        f"# AWS cost, competition window {rep.start} → {end_incl}",
        "",
        f"Account {ACCOUNT_ID}. Generated by `scripts/cost_report.py` at {rep.collected_at}. "
        f"Cost Explorer figures are unblended and still marked estimated; the newest day with posted usage is "
        f"**{rep.data_through or 'none'}** (Cost Explorer lags about a day, so later usage is not in these totals).",
        "",
        "## Summary",
        "",
        "| Scope | Gross usage | Credits applied | Net (billed) |",
        "|---|---:|---:|---:|",
        f"| Whole account | {usd(g)} | {usd(c)} | {usd(g + c)} |",
        f"| keepframe | {usd(kg)} | {usd(kcr)} | {usd(kg + kcr)} |",
        f"| Other (not keepframe) | {usd(g - kg)} | {usd(c - kcr)} | {usd(g - kg + c - kcr)} |",
        "",
    ]
    rem = rep.freetier.get("accountPlanRemainingCredits", {})
    if rem:
        out += [
            f"Credits remaining (Free Tier plan state, {rep.freetier.get('accountPlanType', '?')} plan, "
            f"{rep.freetier.get('accountPlanStatus', '?')}): **{usd(float(rem.get('amount', 0)))}**.",
            "",
        ]

    out += [
        "## By service",
        "",
        "| Service | Gross usage | Credits | Net | keepframe (gross) | Other (gross) | Attribution basis |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in rep.rows:
        out.append(
            f"| {r.service} | {usd(r.usage)} | {usd(r.credit)} | {usd(r.net)} | {usd(r.kc_usage)} | "
            f"{usd(r.other_usage)} | {', '.join(sorted(r.rules)) or '—'} |"
        )
    out += [
        f"| **Total** | **{usd(g)}** | **{usd(c)}** | **{usd(g + c)}** | **{usd(kg)}** | **{usd(g - kg)}** | |",
        "",
    ]

    tag_note = f"The `project` cost-allocation tag is **{'active' if rep.tag_active else 'inactive'}**; " + (
        f"Cost Explorer reports {usd(rep.tagged_total)} under `project=keepframe` in this window."
        if rep.tagged_total
        else "Cost Explorer has no tagged cost in this window yet (a newly activated tag needs up to 24 h, and "
        "history appears only after the backfill completes), so every split below uses the usage-type rules."
    )
    out += ["## Attribution", "", tag_note, "", "| Rule | Meaning |", "|---|---|"]
    fired = set().union(*(r.rules for r in rep.rows)) if rep.rows else set()
    out += [f"| {k} | {v} |" for k, v in RULES.items() if k in fired]
    out.append("")

    pipe = rep.pipeline_kc
    out += [
        "## Per culled event",
        "",
        f"RUN# items in `{DECISIONS_TABLE}` (gsi1 `RUNS`): "
        + ", ".join(f"{k} {v}" for k, v in rep.runs_by_status.items())
        + f". Counted (status {' or '.join(COUNTED_STATUSES)}): **{rep.counted_runs} runs**, of which "
        f"{rep.deployed_runs} ran on the deployed AWS stack; {rep.photos_counted:,} photos processed in the counted runs.",
        "",
        "| Measure | Total | Per counted run | Per deployed run |",
        "|---|---:|---:|---:|",
        f"| keepframe gross usage (all services) | {usd(kg)} | {per(kg, rep.counted_runs)} | "
        f"{per(kg, rep.deployed_runs)} |",
        f"| Pipeline share: ECS/Fargate + AgentCore + Lambda + S3 (keepframe, gross) | {usd(pipe)} | "
        f"{per(pipe, rep.counted_runs)} | {per(pipe, rep.deployed_runs)} |",
        "",
        "The all-services figure includes fixed costs (secrets, CloudTrail data events, benchmark hosts) that do not "
        "scale with events, so it overstates the marginal cost of one more event. Counted runs include local and "
        "smoke runs that wrote RUN# items but used little or no AWS compute.",
        "",
        "## AgentCore Runtime consumption",
        "",
    ]
    if rep.agentcore_ce is not None:
        out.append(f"Cost Explorer: **{usd(rep.agentcore_ce)}** gross usage for AgentCore in the window.")
    else:
        out.append(
            "Cost Explorer has **no AgentCore line yet** (its usage began on 2026-10-08, after the newest "
            "posted day). Measured consumption from CloudWatch `AWS/Bedrock-AgentCore`, priced at list rates "
            "(estimate):"
        )
    if rep.agentcore_cw:
        labels = " | ".join(f"Est. {lab}" for lab, _, _ in AGENTCORE_PRICES)
        out += [
            "",
            f"| Runtime | vCPU-hours | GB-hours | {labels} |",
            "|---|---:|---:|" + "---:|" * len(AGENTCORE_PRICES),
        ]
        tv = tg = 0.0
        for a in rep.agentcore_cw:
            v, gb = float(a["vcpu_hours"]), float(a["gb_hours"])
            tv, tg = tv + v, tg + gb
            ests = " | ".join(usd(v * pc + gb * pm) for _, pc, pm in AGENTCORE_PRICES)
            out.append(f"| {a['runtime']} | {v:.4f} | {gb:.3f} | {ests} |")
        ests = " | ".join(usd(tv * pc + tg * pm) for _, pc, pm in AGENTCORE_PRICES)
        out.append(f"| **Total** | {tv:.4f} | {tg:.3f} | {ests} |")
    out.append("")

    out += ["## Credits", "", "| Day | Usage | Credit applied |", "|---|---:|---:|"]
    out += [f"| {d} | {usd(u)} | {usd(cr)} |" for d, u, cr in rep.credits_by_day if u or cr]
    out += ["", f"Credit applied since 2026-08-01 (all posted months): {usd(rep.lifetime_credit)}.", ""]
    if rep.activities:
        earned = sum(
            float(a.get("reward", {}).get("credit", {}).get("amount", 0))
            for a in rep.activities
            if a.get("status") == "COMPLETED"
        )
        out += [
            "Free Tier credit activities (each completed one adds its reward to the credit pool):",
            "",
            "| Completed (UTC) | Activity | Reward |",
            "|---|---|---:|",
        ]
        for a in rep.activities:
            amt = float(a.get("reward", {}).get("credit", {}).get("amount", 0))
            out.append(
                f"| {str(a.get('completedAt', '—'))[:16].replace('T', ' ')} | {a.get('title', '?')} | "
                f"{usd(amt)} |"
            )
        out += ["", f"Activity credits earned: {usd(earned)}."]
        if rep.observed_balances:
            obs = ", ".join(f"{usd(v)} at {k}" for k, v in rep.observed_balances)
            out.append(f"Remaining-credit readings recorded earlier (get-account-plan-state): {obs}.")
        last = rep.activities[-1]
        if last.get("status") == "COMPLETED":
            amt = float(last.get("reward", {}).get("credit", {}).get("amount", 0))
            out.append(
                f'The latest one, "{last.get("title", "?")}" ({usd(amt)}, completed '
                f"{str(last.get('completedAt', '?'))[:16].replace('T', ' ')} UTC), explains a jump of about {usd(amt)} in "
                "the remaining balance after that time: it is new promotional credit, not a refund or lower spend."
            )
        if rem:
            pool = float(rem.get("amount", 0))
            out.append(
                f"Reconciliation (inferred): a {usd(100.0)} sign-up credit plus {usd(earned)} of activities is "
                f"{usd(100.0 + earned)}; minus {usd(-rep.lifetime_credit)} of posted credit leaves "
                f"{usd(100.0 + earned + rep.lifetime_credit)}, against {usd(pool)} reported remaining. The difference "
                f"({usd(100.0 + earned + rep.lifetime_credit - pool)}) is usage the credit pool has already absorbed "
                "but Cost Explorer has not posted yet. The sign-up amount is not exposed by any API; it is the "
                "standard Free Tier figure."
            )
        out.append("")

    if rep.budgets:
        out += [
            "## AWS Budgets",
            "",
            "| Budget | Limit | Credits netted | Actual | Forecast | Notifications |",
            "|---|---:|---|---:|---:|---|",
        ]
        for b in rep.budgets:
            out.append(
                f"| {b['name']} | {usd(float(b['limit']))} | {'yes' if b['include_credit'] else 'no'} | "
                f"{usd(float(b['actual']))} | {usd(float(b['forecast']))} | "
                f"{', '.join(b['notifications']) or '**none**'} |"
            )
        out += [
            "",
            "A budget that nets credits reads $0 while credits pay the bill, so it cannot warn before the "
            "pool runs out; `keepframe-competition-gross` excludes credits for that reason. Budget forecasts "
            "ignore credits.",
            "",
        ]

    caveats = [
        "Splits are rule-based where the tag has no data; the rules are listed above and each service row names its basis.",
        "Benchmark EC2 hosts are attributed to keepframe; the gh-runner, the sellphotos RDS, archived snapshots and the "
        "customer KMS key are not.",
        "Amounts under one cent show as <$0.01; totals are summed before rounding.",
    ]
    if rep.unclassified:
        caveats.append("Unclassified lines (counted as other): " + "; ".join(rep.unclassified) + ".")
    out += ["## Caveats", ""] + [f"- {x}" for x in caveats] + [""]
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    today = dt.datetime.now(dt.UTC).date()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--start", default=WINDOW_START)
    ap.add_argument(
        "--end", default=(today + dt.timedelta(days=1)).isoformat(), help="exclusive (Cost Explorer End)"
    )
    ap.add_argument("--out", type=Path, default=None, help="default bench/results/cost/<today>.md")
    ap.add_argument(
        "--save-raw", type=Path, default=None, help="dump API responses (contains run ids: not for the repo)"
    )
    ap.add_argument("--from-raw", type=Path, default=None, help="render from a --save-raw dump, no API calls")
    ap.add_argument(
        "--observed-balance",
        action="append",
        default=[],
        metavar="WHEN=USD",
        help="earlier remaining-credit reading to show next to today's, e.g. '2026-10-07=175.06'",
    )
    a = ap.parse_args(argv)

    raw = json.loads(a.from_raw.read_text()) if a.from_raw else collect(a.start, a.end)
    for ob in a.observed_balance:
        when, _, amount = str(ob).partition("=")
        raw.setdefault("observed_balances", {})[when.strip()] = float(amount)
    if a.save_raw:
        a.save_raw.write_text(json.dumps(raw, indent=1, default=str))
    out = (
        a.out
        or Path(__file__).resolve().parents[1] / "bench" / "results" / "cost" / f"{today.isoformat()}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(build_report(raw)))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

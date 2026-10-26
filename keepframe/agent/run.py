"""CLI for the agent loop (also the Fargate ARM64 task entrypoint).

    python -m keepframe.agent.run --event-id X --run-id Y                 # start
    python -m keepframe.agent.run --event-id X --run-id Y --resume decisions.json
    python -m keepframe.agent.run --run-id Y --resume                     # answers from DynamoDB (API/ECS)
    python -m keepframe.agent.run --demo --run-id Y [--approve-all]       # stub scenario end to end

Env: KEEPFRAME_LLM_PROVIDER=bedrock|anthropic|fake (default bedrock), CULL_TOOLS=real|stub, WORK_BUCKET (unset =
LOCAL_MODE), KEEPFRAME_BEDROCK_MODEL_ID / KEEPFRAME_BEDROCK_REASONS_MODEL_ID (bedrock), CULL_PLANNER_MODEL /
CULL_REASONS_MODEL / CULL_ANTHROPIC_SECRET_ID (optional anthropic provider; the secret is read once when
ANTHROPIC_API_KEY is unset).
Prints the result JSON on stdout's last line; exit 1 on error (incl. malformed --resume decisions).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


def _approved_verdict(item: dict[str, Any]) -> Any:
    """The proposal as an answer validate_answers accepts (normalise_proposed): the {keep|reject|review: [ids]}
    map for a group, the bare verdict string for a photo ("reject", {"verdict": "review"}, {"review": ["p_1"]}).
    Sessions saved before proposals were normalised still carry the raw shape, hence the mapping here too.
    An unreadable photo proposal becomes "review"."""
    from keepframe.tools.approval import normalise_proposed

    hv = normalise_proposed(item.get("proposed"), bool(item.get("group_id")))
    return hv if hv is not None else ("review" if not item.get("group_id") else {})


def _auto_answers(result: dict[str, Any], note: str) -> list[dict[str, Any]]:
    """--approve-all: approve every pending proposal as proposed (demo / smoke tests only; source=scripted)."""
    out = []
    for p in result.get("pending_approvals", []):
        for it in p.get("items", []):
            out.append({"id": it.get("group_id") or it.get("photo_id"), "group_id": it.get("group_id"),
                        "photo_id": it.get("photo_id"), "interrupt_id": p["interrupt_id"],
                        "human_verdict": _approved_verdict(it), "note": note})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KeepFrame agent loop")
    ap.add_argument("--event-id")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--resume", nargs="?", const="-", default=None,
                    help="resume a paused run; optional path to decisions JSON (default: read from DynamoDB)")
    ap.add_argument("--max-items", type=int, default=None, help="cap the work set (smoke runs)")
    ap.add_argument("--photos", default=None,
                    help="comma-separated photo ids to (re)check even if the worker settled them")
    ap.add_argument("--only", action="store_true", help="work only --photos (and their groups)")
    ap.add_argument("--demo", action="store_true", help="stub scenario (CULL_TOOLS=stub, synthetic ids)")
    ap.add_argument("--approve-all", action="store_true", help="auto-approve every pause (demo/smoke only)")
    a = ap.parse_args(argv)

    from keepframe.agent import models
    from keepframe.agent import strands_agent as sa

    models.bootstrap_anthropic_secret()

    scenario = None
    if a.demo:
        os.environ["CULL_TOOLS"] = "stub"
        from keepframe.agent._tool_stubs import demo_scenario

        scenario = demo_scenario(a.event_id or "ev_demo")
        a.event_id = scenario["event_id"]
    if a.resume is not None:
        decisions = None
        if a.resume != "-":
            with open(a.resume) as fh:
                doc = json.load(fh)
            decisions = doc.get("decisions", doc) if isinstance(doc, dict) else doc
        # file -> "file"; no file -> answers from DynamoDB, whose stored human_source (ui) is honoured;
        # --demo is always scripted.
        src = "scripted" if a.demo else ("file" if decisions is not None else None)
        res = sa.resume_with_decisions(a.run_id, decisions, event_id=a.event_id, scenario=scenario, source=src)
    else:
        if not a.event_id:
            ap.error("--event-id is required to start a run")
        res = sa.run_event_agent(a.event_id, a.run_id, scenario=scenario, max_items=a.max_items,
                                 include=[p for p in (a.photos or "").split(",") if p] or None, only_include=a.only)
    while a.approve_all and res.get("status") == "paused":
        sys.stderr.write(json.dumps({"event": "auto_approve", "pending": len(res["pending_approvals"])}) + "\n")
        res = sa.resume_with_decisions(a.run_id, _auto_answers(res, "auto-approved (smoke run)"),
                                       event_id=a.event_id, scenario=scenario, source="scripted")
    print(json.dumps(res, default=str))
    return 0 if res.get("status") in ("done", "paused") else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""get_run_budget helper tool: how much of the run's hard caps is left (docs/REPORT.md §4 P7)."""

from __future__ import annotations

from typing import Any

from keepframe.agent.context import GROUP_CALL_CAP, RETRY_CAP, WALL_CLOCK_CAP_S, RunContext
from keepframe.worker import policies as wp


def get_run_budget(ctx: RunContext) -> dict[str, Any]:
    """Return the run budget: tool calls used / cap, wall clock, the current item's per-group calls."""
    frac = ctx.remaining_frac()
    cur = ctx.current_item
    return {
        "tool_calls_used": ctx.tool_calls,
        "tool_calls_cap": ctx.event_call_cap(),
        "wall_s": round(ctx.wall_s(), 1),
        "wall_cap_s": WALL_CLOCK_CAP_S,
        "remaining_frac": round(frac, 3),
        "optional_tools_allowed": wp.optional_tools_allowed(frac),
        "current_item": cur,
        "current_item_calls": ctx.group_calls.get(cur, 0) if cur else 0,
        "per_group_cap": GROUP_CALL_CAP,
        "retry_cap": RETRY_CAP,
        "items_open": len(ctx.open_items()),
        "items_total": len(ctx.items),
        "escalations": ctx.escalations,
    }

"""RunContext: everything the hooks and policies need to know about one agent run.

Plain JSON-serialisable data so it can live in ``agent.state`` (persisted with the session snapshot) and
survive a pause/resume in another process.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

GROUP_CALL_CAP = 25          # <= 25 tool calls per group (docs/REPORT.md §4 hard caps)
EVENT_CALL_FACTOR = 1.5      # <= 1.5 x N_photos tool calls per event
WALL_CLOCK_CAP_S = 3600.0    # <= 60 minutes wall-clock per event (agent working time; human wait excluded)
RETRY_CAP = 3                # <= 3 retries per failing tool


@dataclass
class WorkItem:
    item_id: str                      # group_id, or the photo_id for a singleton
    kind: str                         # "group" | "singleton"
    photo_ids: list[str]
    group_id: str | None = None
    why: str = ""                     # why the agent looks at it (worker flag)
    baseline: dict[str, Any] = field(default_factory=dict)  # worker verdicts {photo_id: {verdict, codes, conf}}
    status: str = "open"              # open | active | done | escalated
    span_s: float = 0.0


@dataclass
class RunContext:
    event_id: str
    run_id: str
    n_photos: int                                  # whole event (for the 1.5 x N cap)
    items: list[WorkItem] = field(default_factory=list)
    photos: dict[str, dict[str, Any]] = field(default_factory=dict)  # photo_id -> static facts + evidence
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)  # group_id -> {members, best, ...}
    decided: dict[str, dict[str, Any]] = field(default_factory=dict)  # photo_id -> decision written this run
    human_answers: dict[str, dict[str, Any]] = field(default_factory=dict)  # item_id -> {photo_id: verdict}
    human_notes: dict[str, str] = field(default_factory=dict)               # item_id -> reviewer note
    human_sources: dict[str, str] = field(default_factory=dict)             # item_id -> ui|file|http|scripted
    human_source: str | None = None                  # source of the answers in the current resume
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)  # interrupt_name -> {items, interrupt_id}
    tool_calls: int = 0
    group_calls: dict[str, int] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)            # "tool|args" -> failed attempts
    escalations: int = 0
    active_s: float = 0.0                            # accumulated working wall-clock across invocations
    agent_started_at: str | None = None              # ISO, first agent invocation of this run
    invocation_started: float | None = None          # monotonic start of the current invocation
    current_item: str | None = None
    traced_tool_uses: list[str] = field(default_factory=list)
    stopped: str | None = None                       # cap that stopped the run, if any
    model_id: str = ""
    model_calls: int = 0
    seq_blocked: int = 0                             # consecutive cancelled tool calls (loop guard)
    provider: str = ""
    tokens_in: int = 0                               # planner input tokens, summed over every invocation/resume
    tokens_out: int = 0                              # planner output tokens (some endpoints report 0 on tool turns)

    # ------------------------------------------------------------------ serialisation

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["invocation_started"] = None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunContext:
        d = dict(d)
        d["items"] = [WorkItem(**i) for i in d.get("items", [])]
        return cls(**d)

    # ------------------------------------------------------------------ lookups

    def item(self, item_id: str | None) -> WorkItem | None:
        for it in self.items:
            if it.item_id == item_id:
                return it
        return None

    def item_of_photo(self, photo_id: str) -> WorkItem | None:
        for it in self.items:
            if photo_id in it.photo_ids:
                return it
        return None

    def group_of(self, photo_id: str) -> str | None:
        p = self.photos.get(photo_id)
        return p.get("group_id") if p else None

    def scope_of_call(self, args: dict[str, Any]) -> str | None:
        """Group/item the tool call is about (for the per-group cap)."""
        if args.get("group_id"):
            return str(args["group_id"])
        pids = args.get("photo_ids") or ([args["photo_id"]] if args.get("photo_id") else [])
        if isinstance(pids, str):
            pids = [pids]
        for pid in pids:
            it = self.item_of_photo(str(pid))
            if it:
                return it.item_id
        decs = args.get("decisions") or args.get("items") or []
        for d in decs if isinstance(decs, list) else []:
            if isinstance(d, dict):
                if d.get("group_id"):
                    return str(d["group_id"])
                if d.get("photo_id"):
                    it = self.item_of_photo(str(d["photo_id"]))
                    if it:
                        return it.item_id
        return self.current_item

    # ------------------------------------------------------------------ budget

    def event_call_cap(self) -> int:
        return max(1, int(EVENT_CALL_FACTOR * self.n_photos))

    def wall_s(self) -> float:
        extra = time.monotonic() - self.invocation_started if self.invocation_started else 0.0
        return self.active_s + extra

    def remaining_frac(self) -> float:
        calls = 1.0 - self.tool_calls / self.event_call_cap()
        wall = 1.0 - self.wall_s() / WALL_CLOCK_CAP_S
        return max(0.0, min(calls, wall))

    def open_items(self) -> list[WorkItem]:
        return [i for i in self.items if i.status in ("open", "active")]

    def undecided(self, item: WorkItem) -> list[str]:
        return [p for p in item.photo_ids if p not in self.decided]

    def refresh_item(self, item: WorkItem) -> None:
        if item.status in ("open", "active") and not self.undecided(item):
            item.status = "done"

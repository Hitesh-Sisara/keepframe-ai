"""Build, run and resume the Strands agent for one event run.

    build_agent(event_id, run_id, model=None)        -> (Agent, Session)
    run_event_agent(event_id, run_id, model=None)    -> {status: running|paused|done, pending_approvals?, summary?}
    resume_with_decisions(run_id, decisions, ...)    -> same shape, after a human answered

Session persistence: the agent's snapshot (messages, agent.state incl. the RunContext, interrupt state) is one
JSON object at ``sessions/<run_id>.json`` in the work bucket (docs/ARCHITECTURE.md "S3 layout"), or under $CULL_LOCAL_ROOT in
LOCAL_MODE. It is written after every invocation, so a different process (a Fargate task started by the API,
the HTTP runtime, AgentCore) can resume the paused run. We use Strands' own ``take_snapshot`` /
``load_snapshot`` (the format SnapshotSessionManager/S3SessionManager persist) and write it ourselves so the
key is exactly ``sessions/<run_id>.json``.

Starting context: the batch worker's ``results/<eventId>/<runId>/{groups.json,decisions.jsonl}`` (falling back to the
unversioned latest copy ``results/<eventId>/...`` for worker runs written before per-run keys). The agent works
every group the worker flagged plus every photo the worker left at review/needs_approval, re-deriving the
evidence with its own tool calls. The rest of the worker's verdicts stand (decided_by=worker).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.tools.executors import SequentialToolExecutor
from strands.types._snapshot import Snapshot

if TYPE_CHECKING:
    from strands.types.interrupt import InterruptResponseContent

from keepframe import opencv_info
from keepframe.agent import models
from keepframe.agent.context import RunContext, WorkItem
from keepframe.agent.hooks import STATE_KEY, KeepFrameHooks
from keepframe.agent.system_prompt import build_system_prompt
from keepframe.agent.toolset import Toolset, tools_backend
from keepframe.config import thresholds_sha
from keepframe.io import proxies
from keepframe.metrics import emf
from keepframe.store import dynamodb
from keepframe.store.traces import TraceWriter, now_iso
from keepframe.tools.approval import validate_answers
from keepframe.worker.run_record import result_keys

START_PROMPT = ("Cull event {event_id} (run {run_id}). {n_items} items need you ({n_groups} groups, "
                "{n_single} single photos) out of {n_photos} photos; the batch worker settled the rest. "
                "Start with next_work_item.")


def session_key(run_id: str) -> str:
    return f"sessions/{run_id}.json"


# --------------------------------------------------------------------------- work set from the worker


def _read_result(event_id: str, run_id: str, name: str) -> tuple[str, bytes] | None:
    """(key, bytes) of this run's result file: results/<E>/<runId>/<name> if present, else the latest copy."""
    store = proxies.get_store()
    for key in result_keys(event_id, run_id, name):
        if store.exists(key):
            return key, store.get_bytes(key)
    return None


def load_work(event_id: str, run_id: str, scenario: dict[str, Any] | None = None,
              max_items: int | None = None, include: list[str] | None = None,
              only_include: bool = False) -> RunContext:
    """Build the RunContext from the worker's results (or a stub scenario).

    include: photo ids to work even if the worker settled them (re-check / audit / demo of a real frame);
    their whole group comes along. only_include: work ONLY those (and their groups).
    """
    from keepframe.io.manifest import read_manifest_dicts

    if scenario is not None:
        records = scenario["records"]
        groups_doc = {"groups": scenario["groups"], "singletons": scenario["singletons"]}
        worker = scenario["decisions"]
        exif = {p: {k: v.get(k) for k in ("exposure_time_s", "hour")} for p, v in scenario["photos"].items()}
    else:
        records = read_manifest_dicts(event_id)
        found = _read_result(event_id, run_id, "groups.json")
        if found is None:
            raise FileNotFoundError(f"results/{event_id}/{run_id}/groups.json (or results/{event_id}/groups.json) "
                                    f"missing: run the worker first "
                                    f"(python -m keepframe.worker.run_event --event-id {event_id} --run-id {run_id})")
        groups_doc = json.loads(found[1])
        dec = _read_result(event_id, run_id, "decisions.jsonl")
        latest: dict[str, dict[str, Any]] = {}
        for row in ([json.loads(x) for x in dec[1].decode().splitlines() if x.strip()] if dec else []):
            if row.get("decided_by", "worker") == "worker":
                latest[row["photo_id"]] = row
        worker = list(latest.values())
        exif = {}
    rec = {r["photo_id"]: r for r in records}
    base = {d["photo_id"]: d for d in worker}
    ctx = RunContext(event_id=event_id, run_id=run_id, n_photos=len(rec))

    def photo(pid: str, gid: str | None) -> dict[str, Any]:
        r = rec.get(pid, {})
        status = (r.get("frs_status") or "").lower()
        known = len(r.get("frs_faces") or []) if status == "indexed" else (0 if status == "no_faces" else None)
        return {"group_id": gid, "known_faces": known, "has_original": bool(r.get("original_key")),
                "width": r.get("width"), "height": r.get("height"), "ev": {}, **exif.get(pid, {})}

    def baseline(pids: list[str]) -> dict[str, Any]:
        return {p: {k: base[p].get(k) for k in ("verdict", "reason_codes", "confidence", "needs_approval")}
                for p in pids if p in base}

    inc = set(include or [])
    for g in groups_doc["groups"]:
        gid = g["group_id"]
        members = list(g.get("members") or g.get("photo_ids") or [])
        flagged = g.get("needs_approval") or any(base.get(p, {}).get("verdict") == "review" or
                                                 base.get(p, {}).get("needs_approval") for p in members)
        picked = bool(inc & set(members))
        if only_include:
            flagged = picked
        elif picked:
            flagged = True
        for p in members:
            ctx.photos[p] = photo(p, gid)
        ctx.groups[gid] = {"members": members, "span_s": g.get("span_s", 0.0),
                           "contact_sheet_key": g.get("contact_sheet_key")}
        if flagged:
            why = ("re-check requested for a frame of this group" if picked and not g.get("needs_approval") else
                   "worker flagged the group for a human check") + (
                f" (margin {g['margin']:.3f})" if g.get("margin") is not None else "")
            ctx.items.append(WorkItem(item_id=gid, kind="group", photo_ids=members, group_id=gid, why=why,
                                      baseline=baseline(members), span_s=float(g.get("span_s") or 0.0)))
    for pid in groups_doc.get("singletons", []):
        b = base.get(pid, {})
        wanted = pid in inc if only_include else (pid in inc or b.get("verdict") == "review" or b.get("needs_approval"))
        if wanted:
            ctx.photos[pid] = photo(pid, None)
            ctx.items.append(WorkItem(item_id=pid, kind="singleton", photo_ids=[pid],
                                      why=("re-check requested" if pid in inc else
                                           f"worker verdict {b.get('verdict')} {b.get('reason_codes')}"),
                                      baseline=baseline([pid])))
    # ensure every group member of a worked item is addressable; drop items beyond max_items
    if max_items is not None:
        ctx.items = ctx.items[:max_items]
    keep = {p for it in ctx.items for p in it.photo_ids}
    ctx.photos = {p: v for p, v in ctx.photos.items() if p in keep}
    ctx.groups = {g: v for g, v in ctx.groups.items() if any(p in keep for p in v["members"])}
    if scenario is None:
        _attach_exif(ctx)
    return ctx


def _attach_exif(ctx: RunContext) -> None:
    """ExposureTime + local hour for the P6 artistic-intent guard (only for the work set)."""
    try:
        from keepframe.io.exif import load_exif
        from keepframe.schemas import PhotoRecord
        from keepframe.worker import policies as wp
    except ImportError:  # pragma: no cover
        return
    from keepframe.io.manifest import read_manifest_dicts

    recs = {r["photo_id"]: r for r in read_manifest_dicts(ctx.event_id) if r["photo_id"] in ctx.photos}
    for pid, p in ctx.photos.items():
        try:
            ex = load_exif(ctx.event_id, pid)
        except Exception:  # noqa: BLE001
            ex = None
        ts = PhotoRecord(**recs[pid]).clicked_ts() if pid in recs else None
        p["exposure_time_s"] = ex.exposure_time_s if ex else None
        p["hour"] = wp.local_hour(ts, ex.local_hour if ex and ex.tz_known else None)


# --------------------------------------------------------------------------- session


@dataclass
class Session:
    run_id: str
    event_id: str
    ctx: RunContext
    trace: TraceWriter
    toolset: Toolset
    hooks: KeepFrameHooks
    provider: str
    model_id: str

    def save(self, agent: Agent) -> None:
        agent.state.set(STATE_KEY, self.ctx.to_dict())
        snap = agent.take_snapshot(preset="session", app_data={
            "event_id": self.event_id, "run_id": self.run_id, "provider": self.provider,
            "model_id": self.model_id, "saved_at": now_iso()})
        proxies.get_store().put_bytes(session_key(self.run_id), json.dumps(snap.to_dict()).encode(),
                                      "application/json")


def load_snapshot(run_id: str) -> Snapshot | None:
    store = proxies.get_store()
    key = session_key(run_id)
    if not store.exists(key):
        return None
    return Snapshot.from_dict(json.loads(store.get_bytes(key)))


def build_agent(event_id: str, run_id: str, model: Any = None, scenario: dict[str, Any] | None = None,
                ctx: RunContext | None = None, max_items: int | None = None,
                tools: Any = None, include: list[str] | None = None,
                only_include: bool = False) -> tuple[Agent, Session]:
    """Agent with the toolset, hooks and (if one exists) the saved session restored."""
    prov = "custom" if model is not None else models.provider()
    model = model if model is not None else models.build_planner_model(prov)
    model_id = str((model.get_config() or {}).get("model_id", "")) if hasattr(model, "get_config") else ""
    snap = load_snapshot(run_id)
    if snap is not None:
        ctx = RunContext.from_dict(snap.data.get("state", {}).get(STATE_KEY) or {})
    if ctx is None:
        ctx = load_work(event_id, run_id, scenario=scenario, max_items=max_items, include=include,
                        only_include=only_include)
    ctx.model_id, ctx.provider = model_id, prov
    trace = TraceWriter(run_id, event_id)
    backend = tools if tools is not None else tools_backend(event_id, scenario)
    toolset = Toolset(ctx, backend, trace)
    hooks = KeepFrameHooks(ctx, trace, toolset)
    toolset.queue = hooks._queue
    agent = Agent(
        model=model,
        tools=toolset.tools(),
        system_prompt=build_system_prompt(),
        hooks=[hooks],
        callback_handler=None,
        tool_executor=SequentialToolExecutor(),
        conversation_manager=SlidingWindowConversationManager(window_size=60, pin_first=1),
        agent_id="keepframe-planner",
        name="keepframe-planner",
        state={STATE_KEY: ctx.to_dict()},
        trace_attributes={"keepframe.run_id": run_id, "keepframe.event_id": event_id},
    )
    if snap is not None:
        agent.load_snapshot(snap)
        agent.state.set(STATE_KEY, ctx.to_dict())
    return agent, Session(run_id, event_id, ctx, trace, toolset, hooks, prov, model_id)


# --------------------------------------------------------------------------- run / resume

TURNS_PER_CHUNK = 80   # Strands recurses ~3 Python frames per loop turn; 80 turns stay far below the 1000 limit
MAX_CHUNKS = 100       # 8000 turns: unreachable in practice (hooks cap tool calls at 1.5 x N), still a hard stop


def _usage(agent: Agent) -> tuple[int, int]:
    """(input, output) tokens of the latest Strands invocation (reset by Strands at every agent() call)."""
    inv = agent.event_loop_metrics.latest_agent_invocation
    if inv is None:
        return 0, 0
    return int(inv.usage.get("inputTokens", 0) or 0), int(inv.usage.get("outputTokens", 0) or 0)


def _invoke(agent: Agent, prompt: Any, ctx: RunContext | None = None) -> Any:
    """``agent(prompt)`` in bounded chunks.

    One Strands invocation recurses once per model turn (event_loop_cycle -> recurse_event_loop), so a single
    invocation longer than ~320 turns dies with ``RecursionError`` (surfaced as EventLoopException). Each
    chunk is capped with ``limits={"turns": N}`` (Strands stops cleanly with stop_reason ``limit_turns`` and
    leaves the conversation reinvokable); the next chunk continues from history with no new prompt, on a fresh
    stack. Interrupts, end_turn and hook stops end the loop as before.
    """
    turns = max(1, int(os.environ.get("CULL_TURNS_PER_CHUNK", str(TURNS_PER_CHUNK))))

    def call(p: Any) -> Any:
        try:
            return agent(p, limits={"turns": turns})
        finally:
            if ctx is not None:  # count tokens even when the chunk raised
                tin, tout = _usage(agent)
                ctx.tokens_in += tin
                ctx.tokens_out += tout

    result = call(prompt)
    for _ in range(MAX_CHUNKS - 1):
        if result.stop_reason != "limit_turns":
            break
        result = call(None)
    return result


def agent_host() -> str:
    """Where this agent process runs: agentcore | fargate | local (RUN# ``agent_host``, trace header ``host``).

    AgentCore Runtime sets no environment variable of its own (bedrock-agentcore 1.24.0 only exposes the
    X-Amzn-Bedrock-AgentCore-Runtime-Session-Id header per request), so the runtime app marks the process
    via CULL_AGENT_HOST=agentcore when it sees that header, and infra sets CULL_INSTANCE_TYPE=agentcore-arm64.
    """
    explicit = os.environ.get("CULL_AGENT_HOST", "").strip()
    if explicit:
        return explicit
    if os.environ.get("CULL_INSTANCE_TYPE", "").startswith("agentcore"):
        return "agentcore"
    if os.environ.get("ECS_CONTAINER_METADATA_URI_V4"):
        return "fargate"
    return "local"


def _pending(result: Any) -> list[dict[str, Any]]:
    out = []
    for i in result.interrupts or []:
        reason = i.reason if isinstance(i.reason, dict) else {"reason": i.reason}
        out.append({"interrupt_id": i.id, "name": i.name, "items": reason.get("items", []),
                    "kind": reason.get("kind")})
    return out


def _finish(agent: Agent, sess: Session, result: Any, t0: float) -> dict[str, Any]:
    ctx = sess.ctx
    sess.save(agent)
    if result.stop_reason == "interrupt":
        pend = _pending(result)
        sess.trace.emit("interrupt", text=f"run paused for {sum(len(p['items']) for p in pend)} approval item(s)",
                        pending=pend, tokens_in=ctx.tokens_in, tokens_out=ctx.tokens_out)
        now = now_iso()
        dynamodb.update_run(ctx.event_id, ctx.run_id, status="awaiting_approval", updated_at=now, paused_at=now,
                            tool_call_count=ctx.tool_calls, agent_escalations=ctx.escalations,
                            agent_wall_s=round(ctx.wall_s(), 2), agent_tokens_in=ctx.tokens_in,
                            agent_tokens_out=ctx.tokens_out)
        sess.trace.flush()
        return {"status": "paused", "run_id": ctx.run_id, "event_id": ctx.event_id, "pending_approvals": pend}
    counts = {"keep": 0, "reject": 0, "review": 0}
    for d in ctx.decided.values():
        counts[d["verdict"]] = counts.get(d["verdict"], 0) + 1
    escalated = [i.item_id for i in ctx.items if i.status == "escalated"]
    left = [i.item_id for i in ctx.items if i.status in ("open", "active")]
    status = "done"
    summary = {"items": len(ctx.items), "decided": len(ctx.decided), "verdict_counts": counts,
               "escalated": escalated, "unfinished": left, "tool_calls": ctx.tool_calls,
               "model_calls": ctx.model_calls, "tokens_in": ctx.tokens_in, "tokens_out": ctx.tokens_out,
               "escalations": ctx.escalations, "wall_s": round(ctx.wall_s(), 2),
               "stopped": ctx.stopped, "final_text": str(result)[:500]}
    sess.trace.emit("run_end", text=f"run finished: {summary['decided']} decided, {len(escalated)} escalated",
                    summary=summary)
    sess.trace.flush()
    now = now_iso()
    dynamodb.update_run(ctx.event_id, ctx.run_id, status="succeeded" if not left else "partial",
                        agent_ended_at=now, ended_at=now, updated_at=now, tool_call_count=ctx.tool_calls,
                        agent_escalations=ctx.escalations, agent_wall_s=round(ctx.wall_s(), 2),
                        agent_verdict_counts=counts, agent_decided=len(ctx.decided), agent_tokens_in=ctx.tokens_in,
                        agent_tokens_out=ctx.tokens_out, remove=("paused_at",))
    emf.run_done(ctx.run_id, os.environ.get("CULL_OPENCV_FLAVOR", "stock"), len(ctx.decided),
                 time.perf_counter() - t0, sess.provider)
    _summary_json(sess, summary)
    return {"status": status, "run_id": ctx.run_id, "event_id": ctx.event_id, "summary": summary}


def _summary_json(sess: Session, summary: dict[str, Any]) -> None:
    """results/<E>/<runId>/agent_summary.json, plus the unversioned latest copy (kept for one release)."""
    body = json.dumps({"run_id": sess.run_id, **summary, **opencv_info(), "provider": sess.provider,
                       "model_id": sess.model_id, "thresholds_sha": thresholds_sha()}, indent=1).encode()
    store = proxies.get_store()
    for key in result_keys(sess.event_id, sess.run_id, "agent_summary.json"):
        store.put_bytes(key, body, "application/json")


def run_event_agent(event_id: str, run_id: str, model: Any = None, scenario: dict[str, Any] | None = None,
                    max_items: int | None = None, tools: Any = None, include: list[str] | None = None,
                    only_include: bool = False) -> dict[str, Any]:
    """Start (or continue an unpaused) run. Returns {status, pending_approvals?, summary?}."""
    t0 = time.perf_counter()
    agent, sess = build_agent(event_id, run_id, model, scenario=scenario, max_items=max_items, tools=tools,
                              include=include, only_include=only_include)
    if agent._interrupt_state.activated:
        return {"status": "paused", "run_id": run_id, "event_id": event_id,
                "pending_approvals": [{"interrupt_id": i.id, "name": i.name,
                                       "items": (i.reason or {}).get("items", [])}
                                      for i in agent._interrupt_state.interrupts.values() if i.response is None]}
    ctx = sess.ctx
    info = opencv_info()
    if not sess.trace.lines and sess.trace.seq == 0:
        sess.trace.emit("run_header", event_id=event_id, provider=sess.provider, model=sess.model_id,
                        host=agent_host(), run_kind=dynamodb.run_kind(),
                        reasons_model=models.reasons_model_id() if sess.provider != "custom" else None,
                        thresholds_sha=thresholds_sha(), tools=os.environ.get("CULL_TOOLS", "real"),
                        n_photos=ctx.n_photos, n_items=len(ctx.items),
                        **{k: info[k] for k in ("opencv_version", "cv2_file")})
    now = now_iso()
    ctx.agent_started_at = ctx.agent_started_at or now
    dynamodb.update_run(event_id, run_id, status="running", started_at=now, agent_started_at=ctx.agent_started_at,
                        updated_at=now,
                        llm_provider=sess.provider, planner_model=sess.model_id,
                        reasons_model=models.reasons_model_id() if sess.provider != "custom" else None,
                        opencv_version=info["opencv_version"], cv2_file=info["cv2_file"],
                        thresholds_sha=thresholds_sha(), flavor=os.environ.get("CULL_OPENCV_FLAVOR", "stock"),
                        instance_type=os.environ.get("CULL_INSTANCE_TYPE", "local"),
                        agent_host=agent_host(), agent_photos=len(ctx.photos))
    if agent.messages:
        prompt: Any = "Continue with next_work_item."
    else:
        prompt = START_PROMPT.format(event_id=event_id, run_id=run_id, n_items=len(ctx.items),
                                     n_groups=sum(1 for i in ctx.items if i.kind == "group"),
                                     n_single=sum(1 for i in ctx.items if i.kind != "group"),
                                     n_photos=ctx.n_photos)
    result = _invoke(agent, prompt, ctx)
    return _finish(agent, sess, result, t0)


HUMAN_SOURCES = ("ui", "file", "http", "scripted", "lead_test")
LEAD_TEST_LABELLER = "lead-mechanism-test"


def _answer_source(d: dict[str, Any]) -> str | None:
    """Stored/posted source of one answer; labeller lead-mechanism-test always means lead_test."""
    if str(d.get("labeller") or "") == LEAD_TEST_LABELLER:
        return "lead_test"
    src = d.get("human_source")
    return str(src) if src else None


def resume_with_decisions(run_id: str, decisions: list[dict[str, Any]] | None = None, event_id: str | None = None,
                          model: Any = None, scenario: dict[str, Any] | None = None,
                          tools: Any = None, source: str | None = None) -> dict[str, Any]:
    """Resume a paused run with the human answers.

    decisions: [{id | group_id | photo_id | interrupt_id, human_verdict, note}] (the API payload). When None,
    the answers are read from DynamoDB (``human_decision`` on the answered GROUP#/PHOTO# items, falling back
    to ``resume_decisions`` on RUN#): the ECS resume path ``python -m keepframe.agent.run --resume``.
    source: how the answers arrived: ui (DynamoDB / API), file (--resume <file>), http (/invocations),
    scripted (auto-approver / demo; written as decided_by='scripted', never 'human'). Defaults to ui when the
    answers come from the store; REQUIRED when ``decisions`` is passed.
    """
    if source is None and decisions is not None:
        raise ValueError("source is required with explicit decisions (ui|file|http|scripted|lead_test)")
    if decisions is not None and any(_answer_source(d) == "lead_test" for d in decisions):
        source = "lead_test"  # an HTTP/file payload labelled lead-mechanism-test is never a photographer
    if source is not None and source not in HUMAN_SOURCES:
        raise ValueError(f"source must be one of {HUMAN_SOURCES}")
    if decisions is not None:
        try:
            validate_answers(decisions)
        except ValueError as e:
            return _invalid(run_id, event_id, e)
    t0 = time.perf_counter()
    snap = load_snapshot(run_id)
    if snap is None:
        raise FileNotFoundError(f"no saved session for {run_id} ({session_key(run_id)})")
    event_id = event_id or snap.app_data.get("event_id")
    agent, sess = build_agent(str(event_id), run_id, model, scenario=scenario, tools=tools)
    st = agent._interrupt_state
    open_ints = [i for i in st.interrupts.values() if i.response is None]
    if not st.activated or not open_ints:
        return {"status": "done", "run_id": run_id, "event_id": event_id, "note": "nothing to resume"}
    if decisions is None:
        decisions = _decisions_from_store(str(event_id), run_id)
        # the API stamps human_source on what it saves (ui; scripted for an auto-approver); honour it
        stored = {s for s in (_answer_source(d) for d in decisions) if s}
        if len(stored) > 1:
            raise ValueError(f"stored answers mix human sources {sorted(stored)}; resume them separately")
        src = stored.pop() if stored else None
        if src is not None and src not in HUMAN_SOURCES:
            raise ValueError(f"stored human_source {src!r} not in {HUMAN_SOURCES}")
        if source is None or (source == "ui" and src in ("scripted", "lead_test")):
            source = src or "ui"  # never upgrade a stored scripted / lead-test answer to ui
    try:
        validate_answers(decisions, {g: list(v.get("members") or []) for g, v in sess.ctx.groups.items()})
    except ValueError as e:
        return _invalid(run_id, event_id, e)
    responses: list[dict[str, Any]] = []
    unanswered: list[str] = []
    for i in open_ints:
        items = (i.reason or {}).get("items", []) if isinstance(i.reason, dict) else []
        ids = {str(x.get("group_id") or x.get("photo_id")) for x in items}
        mine = [d for d in decisions if d.get("interrupt_id") == i.id or
                str(d.get("id") or d.get("group_id") or d.get("photo_id")) in ids]
        if mine:
            responses.append({"interruptResponse": {"interruptId": i.id, "response": {"decisions": mine}}})
        else:
            unanswered.append(i.id)
    if not responses:
        return {"status": "paused", "run_id": run_id, "event_id": event_id,
                "pending_approvals": [{"interrupt_id": x} for x in unanswered],
                "note": "no decision matched a pending approval"}
    if unanswered:
        # Strands needs a response for every open interrupt; keep the others pending by answering 'review'.
        for iid in unanswered:
            i = st.interrupts[iid]
            items = (i.reason or {}).get("items", [])
            responses.append({"interruptResponse": {"interruptId": iid, "response": {"decisions": [
                {"id": x.get("group_id") or x.get("photo_id"), "group_id": x.get("group_id"),
                 "photo_id": x.get("photo_id"),
                 "human_verdict": ({"review": [m for m in (sess.ctx.groups.get(x.get("group_id")) or {})
                                               .get("members", [])]} if x.get("group_id") else "review"),
                 "note": "no answer in this resume; left for review"} for x in items]}}})
    sess.ctx.human_source = source
    sess.trace.emit("interrupt_resumed", human_source=source,
                    decisions=[r["interruptResponse"]["response"]["decisions"] for r in responses])
    dynamodb.update_run(str(event_id), run_id, status="running", updated_at=now_iso(), agent_host=agent_host(),
                        remove=("paused_at",))
    result = _invoke(agent, cast("list[InterruptResponseContent]", responses), sess.ctx)
    return _finish(agent, sess, result, t0)


def _invalid(run_id: str, event_id: Any, err: ValueError) -> dict[str, Any]:
    """Malformed answers: report, do not resume (resuming would feed the planner an answer without a verdict)."""
    return {"status": "error", "run_id": run_id, "event_id": event_id, "error": str(err)[:1000],
            "note": "run left paused; resend the decisions with human_verdict"}


def _decisions_from_store(event_id: str, run_id: str) -> list[dict[str, Any]]:
    out = []
    for it in dynamodb.answered_for_run(event_id, run_id):
        hd = it.get("human_decision") or {}
        d = {"human_verdict": hd.get("human_verdict"), "note": hd.get("note", ""),
             "interrupt_id": it.get("interrupt_id"), "interrupt_name": it.get("interrupt_name"),
             "human_source": hd.get("human_source") or it.get("human_source"),
             "labeller": hd.get("labeller") or it.get("labeller")}
        if it.get("group_id") and str(it.get("sk", "")).startswith("GROUP#"):
            d["group_id"] = d["id"] = it["group_id"]
        else:
            d["photo_id"] = d["id"] = it.get("photo_id")
        out.append(d)
    if not out:
        run = dynamodb.get_run(event_id, run_id) or {}
        out = list(run.get("resume_decisions") or [])
    return out

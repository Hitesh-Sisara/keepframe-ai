"""Strands hooks: hard caps, retries, policy ENFORCEMENT, approval interrupts, trace emission.

BeforeToolCallEvent (in order)
  1. trace ``model_reasoning`` (the assistant text that preceded the call) + ``tool_call``
  2. caps: <= 25 calls per group, <= 1.5 x N calls per event, <= 60 min working wall clock -> escalate
     (cancel the call, put the item on the approval queue, mark it escalated), never loop
  3. retries: a tool+args that already failed 3 times is cancelled and the item escalated (write_decision
     included); 6 consecutive failed or blocked calls stop the run (before_model)
  4. policy gates (keepframe.agent.policies): P1 prerequisites, P3 eye skip, P7 budget -> cancel with the
     policy text as the tool result, so the model reads why and does the right thing next
  5. write_decision: P2/P3/P4/P6 violations cancel the write; P5 (decided_by=agent with a reject below
     0.8, a small-margin / tied / all-failed group) raises a Strands interrupt *in the hook*
     (``event.interrupt``) -- the run pauses until a human answers, then the write proceeds as the human
     said (the hook rewrites the tool input to the answered verdicts; decided_by=human, or scripted for
     auto-approvals, with human_source)
AfterToolCallEvent
  trace ``tool_result`` (+ opencv version, cv2_file, latency_ms), EMF metrics, failure counting / retry
AfterInvocationEvent: persist RunContext into agent.state; trace ``interrupt`` when the run paused.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from strands.hooks import (
    AfterInvocationEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from keepframe.agent import policies as ap
from keepframe.agent.context import GROUP_CALL_CAP, RETRY_CAP, WALL_CLOCK_CAP_S, RunContext
from keepframe.metrics import emf
from keepframe.store import dynamodb
from keepframe.store.traces import TraceWriter, now_iso

STATE_KEY = "keepframe_ctx"
NO_CAP_TOOLS = ("next_work_item", "get_run_budget", "request_human_approval", "write_decision")
HOOK_APPROVAL = "keepframe-policy-approval"
MAX_SEQ_BLOCKED = 6


def _assistant_text(agent: Any, tool_use_id: str) -> tuple[str, str | None]:
    """(reasoning text, first toolUseId) of the assistant message containing this toolUse.

    A model may emit several tool calls in one message; its text is traced once, before the first call."""
    for m in reversed(agent.messages):
        if m.get("role") != "assistant":
            continue
        ids = [c["toolUse"]["toolUseId"] for c in m.get("content", []) if "toolUse" in c]
        if tool_use_id in ids:
            return " ".join(c["text"].strip() for c in m["content"] if c.get("text")).strip(), ids[0]
    return "", None


def _result_payload(result: Mapping[str, Any]) -> Any:
    txt = "".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
    try:
        return json.loads(txt)
    except ValueError:
        return {"text": txt[:2000]}


class KeepFrameHooks(HookProvider):
    def __init__(self, ctx: RunContext, trace: TraceWriter, toolset: Any) -> None:
        self.ctx = ctx
        self.trace = trace
        self.toolset = toolset
        self._t0: dict[str, float] = {}

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self.before_invocation)
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)
        registry.add_callback(AfterInvocationEvent, self.after_invocation)

    # ------------------------------------------------------------------ lifecycle

    def before_invocation(self, event: BeforeInvocationEvent) -> None:
        self.ctx.invocation_started = time.monotonic()

    def before_model(self, event: BeforeModelCallEvent) -> None:
        self.ctx.model_calls += 1
        if self.ctx.wall_s() > WALL_CLOCK_CAP_S or self.ctx.seq_blocked >= MAX_SEQ_BLOCKED:
            why = "wall clock" if self.ctx.wall_s() > WALL_CLOCK_CAP_S else "repeated blocked calls"
            self._stop_run(f"{why} cap")
            event.cancel = f"Run stopped by hook: {why} cap reached; remaining items escalated."

    def after_invocation(self, event: AfterInvocationEvent) -> None:
        ctx = self.ctx
        if ctx.invocation_started is not None:
            ctx.active_s += time.monotonic() - ctx.invocation_started
            ctx.invocation_started = None
        event.agent.state.set(STATE_KEY, ctx.to_dict())
        self.trace.flush()

    # ------------------------------------------------------------------ before tool

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        ctx = self.ctx
        tu = event.tool_use
        name, args, tid = tu["name"], dict(tu.get("input") or {}), tu["toolUseId"]
        resuming = self._is_resume(event, tid)
        if not resuming and tid not in ctx.traced_tool_uses:
            ctx.traced_tool_uses.append(tid)
            text, first = _assistant_text(event.agent, tid)
            if text and first == tid:
                self.trace.emit("model_reasoning", text=text[:2000])
            self.trace.emit("tool_call", tool=name, args=args, model=ctx.model_id or None)
        self._t0[tid] = time.perf_counter()

        # write_decision first: its hook interrupt must re-run on resume regardless of caps.
        if name == "write_decision":
            key = f"{name}|{json.dumps(args, sort_keys=True)}"
            if ctx.failures.get(key, 0) > RETRY_CAP:
                scope = ctx.scope_of_call(args) or ctx.current_item
                self._cancel(event, f"write_decision failed {RETRY_CAP + 1} times with these arguments; the item "
                                    "is escalated to a human. Call next_work_item.", "cap_retry")
                if scope:
                    self._escalate(scope, "write_decision kept failing")
                return
            self._gate_write(event, args)
            return
        if name in NO_CAP_TOOLS:
            if not resuming and name == "request_human_approval":
                ctx.tool_calls += 1
            return

        # caps
        scope = ctx.scope_of_call(args)
        ctx.tool_calls += 1
        if scope:
            ctx.group_calls[scope] = ctx.group_calls.get(scope, 0) + 1
        if ctx.tool_calls > ctx.event_call_cap():
            self._cancel(event, f"Event cap reached: {ctx.tool_calls - 1} tool calls >= 1.5 x {ctx.n_photos} "
                                "photos. Remaining items are escalated to a human; stop analysing.", "cap_event")
            self._stop_run("event tool-call cap")
            return
        if scope and ctx.group_calls[scope] > GROUP_CALL_CAP:
            self._cancel(event, f"Group cap reached: {GROUP_CALL_CAP} tool calls on {scope}. The item is "
                                "escalated to a human; call next_work_item.", "cap_group")
            self._escalate(scope, f"per-group cap of {GROUP_CALL_CAP} tool calls reached")
            return
        key = f"{name}|{json.dumps(args, sort_keys=True)}"
        if ctx.failures.get(key, 0) > RETRY_CAP:
            self._cancel(event, f"{name} failed {RETRY_CAP + 1} times with these arguments; the item is escalated. "
                                "Call next_work_item.", "cap_retry")
            if scope:
                self._escalate(scope, f"{name} kept failing")
            return
        block = ap.check_tool_call(ctx, name, args)
        if block is not None:
            self._cancel(event, block.message, f"policy_{block.policy}")

    def _is_resume(self, event: BeforeToolCallEvent, tid: str) -> bool:
        st = event.agent._interrupt_state
        return any(i.id.startswith(f"v1:before_tool_call:{tid}:") or i.id.startswith(f"v1:tool_call:{tid}:")
                   for i in st.interrupts.values() if i.response is not None)

    def _cancel(self, event: BeforeToolCallEvent, message: str, kind: str) -> None:
        self.ctx.seq_blocked += 1
        event.cancel_tool = f"Policy hook: {message}" if not message.startswith("Policy") else message
        self.trace.emit("model_reasoning", text=f"[hook:{kind}] {message}", hook=kind)

    # ------------------------------------------------------------------ write gate (+ hook interrupt)

    def _gate_write(self, event: BeforeToolCallEvent, args: dict[str, Any]) -> None:
        ctx = self.ctx
        decisions = list(args.get("decisions") or [])
        check = ap.check_write(ctx, decisions)
        if check.blocks:
            self._cancel(event, " | ".join(b.message for b in check.blocks)[:1500], f"policy_{check.blocks[0].policy}")
            return
        if not check.approvals:
            ctx.tool_calls += 1
            return
        # P5: a human must confirm. Interrupt INSIDE the hook; resumes with the answer.
        items = [self._approval_item(a) for a in check.approvals]
        name = HOOK_APPROVAL + ":" + ",".join(sorted(str(i.get("group_id") or i.get("photo_id")) for i in items))
        iid = event._interrupt_id(name)
        known = event.agent._interrupt_state.interrupts.get(iid)
        if known is None or known.response is None:
            for d in decisions:
                p = self.ctx.photos.get(str(d.get("photo_id")))
                if p is not None and d.get("confidence") is not None:
                    p["agent_confidence"] = float(d["confidence"])
            self._queue(items, name, iid)
        answer = event.interrupt(name, reason={"kind": "keepframe_policy_approval", "event_id": ctx.event_id,
                                               "run_id": ctx.run_id, "items": items})
        decisions_out = self._apply_answer(items, answer, decisions)
        event.tool_use["input"] = {"decisions": decisions_out}
        ctx.tool_calls += 1

    def _approval_item(self, a: dict[str, Any]) -> dict[str, Any]:
        gid = a.get("group_id")
        item = {"group_id": gid if "proposed" in a and isinstance(a["proposed"], dict) else None,
                "photo_id": a.get("photo_id") if not isinstance(a.get("proposed"), dict) else None,
                "proposed": a.get("proposed"), "reason": a.get("reason", ""),
                "confidence": a.get("confidence", 0.5),
                "contact_sheet_key": a.get("contact_sheet_key") or
                ((self.ctx.groups.get(gid) or {}).get("contact_sheet_key") if gid else None)}
        return {k: v for k, v in item.items() if v is not None}

    def _apply_answer(self, items: list[dict[str, Any]], answer: Any,
                      decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from keepframe.tools.approval import normalise_response

        resp = normalise_response(answer, items)
        self.toolset.absorb_answers(resp["decisions"])
        by_photo = {str(d["photo_id"]): dict(d) for d in decisions}
        for d in by_photo.values():
            hv = ap.human_answer_for(self.ctx, str(d["photo_id"]))
            if hv is None:
                continue
            gid = d.get("group_id") or self.ctx.group_of(str(d["photo_id"]))
            if hv != d.get("verdict"):
                d["reason_codes"] = ({"keep": ["KEEP_BEST_OF_GROUP"], "reject": ["BURST_NOT_BEST"]}.get(
                    hv, ["REVIEW_LOW_CONFIDENCE"]) if gid else
                    ({"keep": ["KEEP_SINGLETON"]}.get(hv, d.get("reason_codes") or ["REVIEW_LOW_CONFIDENCE"])))
                d.pop("reason_text", None)
            d["verdict"] = hv
            d["decided_by"] = "human"  # toolset.write_decision sets human|scripted + human_source from the answer
            # confidence: the agent's proposed value is kept (not forced to 1.0)
        # photos the human named that the model did not include (e.g. group overrides)
        for it in items:
            gid = it.get("group_id")
            if not gid:
                continue
            for pid, hv in (self.ctx.human_answers.get(gid) or {}).items():
                if pid not in by_photo:
                    by_photo[pid] = {"photo_id": pid, "group_id": gid, "verdict": hv, "decided_by": "human",
                                     "confidence": float(it.get("confidence", 0.5)), "reason_codes": {"keep": ["KEEP_BEST_OF_GROUP"],
                                                                         "reject": ["BURST_NOT_BEST"]}.get(
                                         hv, ["REVIEW_LOW_CONFIDENCE"])}
        return list(by_photo.values())

    def _queue(self, items: list[dict[str, Any]], name: str, interrupt_id: str) -> None:
        """Approval queue (DynamoDB GSI3) + trace, before the run pauses."""
        ctx = self.ctx
        ts = now_iso()
        confs: dict[str, float] = {}
        for it in items:
            gid = it.get("group_id")
            pids = list((ctx.groups.get(gid) or {}).get("members", [])) if gid else [str(it.get("photo_id"))]
            for pid in pids:
                p = ctx.photos.get(pid)
                if p is None:
                    continue
                p.setdefault("agent_confidence", float(it.get("confidence", 0.5)))
                confs[pid] = p["agent_confidence"]
        if confs:
            dynamodb.set_agent_confidence(ctx.event_id, confs, ts)
        for it in items:
            kind = "group" if it.get("group_id") else "photo"
            dynamodb.mark_pending(ctx.event_id, str(it.get("group_id") or it.get("photo_id")), kind,
                                  {**it, "interrupt_id": interrupt_id, "interrupt_name": name,
                                   "run_id": ctx.run_id}, ts)
        ctx.pending[name] = {"items": items, "interrupt_id": interrupt_id, "ts": ts}
        ctx.escalations += len(items)
        emf.escalation("policy_gate", ctx.run_id, len(items))

    # ------------------------------------------------------------------ escalation (caps)

    def _escalate(self, item_id: str, why: str) -> None:
        ctx = self.ctx
        item = ctx.item(item_id)
        if item is None or item.status == "escalated":
            return
        item.status = "escalated"
        ctx.escalations += 1
        kind = "group" if item.kind == "group" else "photo"
        rid = item.group_id or item.photo_ids[0]
        dynamodb.mark_pending(ctx.event_id, rid, kind,
                              {"proposed": {"review": item.photo_ids} if kind == "group" else "review",
                               "reason": f"Escalated by hook: {why}", "confidence": 0.0, "run_id": ctx.run_id,
                               "contact_sheet_key": (ctx.groups.get(rid) or {}).get("contact_sheet_key")},
                              now_iso())
        self.trace.emit("escalation", text=f"{item_id}: {why}", item_id=item_id)
        emf.escalation("cap", ctx.run_id)
        if ctx.current_item == item_id:
            ctx.current_item = None

    def _stop_run(self, why: str) -> None:
        ctx = self.ctx
        if ctx.stopped:
            return
        ctx.stopped = why
        for it in ctx.open_items():
            self._escalate(it.item_id, why)

    # ------------------------------------------------------------------ after tool

    def after_tool(self, event: AfterToolCallEvent) -> None:
        ctx = self.ctx
        tu = event.tool_use
        name, tid = tu["name"], tu["toolUseId"]
        t0 = self._t0.pop(tid, None)
        latency = round((time.perf_counter() - t0) * 1e3, 2) if t0 else round((event.duration or 0) * 1e3, 2)
        status = str(event.result.get("status", "success"))
        payload = _result_payload(dict(event.result))
        cancelled = event.cancel_message is not None
        if not cancelled and status != "error":
            ctx.seq_blocked = 0
        args = dict(tu.get("input") or {})
        if status == "error" and not cancelled:
            # A failing call counts like a blocked one: MAX_SEQ_BLOCKED consecutive failures/blocks stop the run
            # (before_model), so a planner that keeps repeating a bad call can never loop until RecursionError.
            ctx.seq_blocked += 1
            key = f"{name}|{json.dumps(args, sort_keys=True)}"
            ctx.failures[key] = ctx.failures.get(key, 0) + 1
            if ctx.failures[key] <= RETRY_CAP and event.exception is not None and _transient(event.exception):
                self.trace.emit("error", tool=name, text=f"attempt {ctx.failures[key]} failed: "
                                                         f"{str(event.exception)[:200]}; retrying")
                event.retry = True
                return
        line: dict[str, Any] = {"tool": name, "result": payload, "latency_ms": latency}
        if status == "error":
            line["status"] = "cancelled" if cancelled else "error"
        self.trace.emit("tool_result", **line)
        emf.tool_call(name, latency, ctx.run_id, ok=status != "error")
        if name == "write_decision" and status != "error":
            self._after_write(args)
        if name == "next_work_item" and isinstance(payload, dict) and payload.get("done"):
            self.trace.flush()

    def _after_write(self, args: dict[str, Any]) -> None:
        """Resolve any approval queue entries the write answered; store GROUP# items for touched groups."""
        ctx = self.ctx
        ts = now_iso()
        groups = {d.get("group_id") or ctx.group_of(str(d["photo_id"])) for d in args.get("decisions", [])}
        for name, p in list(ctx.pending.items()):
            for it in p["items"]:
                key = str(it.get("group_id") or it.get("photo_id"))
                if key in ctx.human_answers:
                    kind = "group" if it.get("group_id") else "photo"
                    dynamodb.resolve_pending(ctx.event_id, key, kind,
                                             {"human_verdict": ctx.human_answers[key],
                                              "note": ctx.human_notes.get(key, ""), "decided_at": ts}, ts)
                    p.setdefault("resolved", []).append(key)
            if len(p.get("resolved", [])) >= len(p["items"]):
                ctx.pending.pop(name)
        gl = []
        for gid in groups:
            if gid and gid in ctx.groups:
                g = ctx.groups[gid]
                best = g.get("best") or {}
                keeper = next((p for p in g["members"] if (ctx.decided.get(p) or {}).get("verdict") == "keep"),
                              best.get("best_photo_id"))
                gl.append({"group_id": gid, "members": g["members"], "size": len(g["members"]),
                           "span_s": g.get("span_s", 0.0), "best_photo_id": keeper, "margin": best.get("margin"),
                           "tie": best.get("tie", False), "ranking": best.get("ranking", []),
                           "contact_sheet_key": g.get("contact_sheet_key")})
        if gl:
            dynamodb.put_groups(ctx.event_id, gl, ctx.run_id, ts)


def _transient(exc: BaseException) -> bool:
    """Retry I/O-ish failures; do not retry validation errors (the model must change its arguments)."""
    return not isinstance(exc, ValueError | KeyError | TypeError)

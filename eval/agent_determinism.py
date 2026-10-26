#!/usr/bin/env python3
"""Agent determinism + token cost: run the planner twice on the same worker output and compare verdicts.

    # one repetition (long: launch with setsid/nohup, one process per repetition)
    python eval/agent_determinism.py run --event E09 --rep A --root /tmp/eval-det
    python eval/agent_determinism.py run --event E09 --rep B --root /tmp/eval-det
    # compare -> eval/results/determinism.{json,md} (aggregates only; per-photo pairs under eval/results/raw/)
    python eval/agent_determinism.py compare --root /tmp/eval-det --reps A B

Isolation (nothing is written to the live stack):
  * READS come from the work bucket ($WORK_BUCKET: manifests, worker results, images) through the normal store.
  * WRITES go to an overlay directory <root>/<rep>/s3/ (session snapshot, decisions.jsonl, trace.jsonl, contact
    sheets, agent_summary.json). Reads check the overlay first, so a repetition sees its own writes.
  * DynamoDB is the LOCAL JSONL backend (CULL_DDB=local, CULL_LOCAL_ROOT=<root>/<rep>), never the AWS tables.
  * Guards: keepframe.io.s3.put_bytes/put_json and the AWS DynamoDB backend raise if anything tries to use
    them, so a missed code path fails loudly instead of touching live data.

Approvals: every pause is answered by a SCRIPTED approve-as-proposed reviewer (human_source=scripted, the same
rule as `run.py --approve-all`). That keeps both repetitions on the same answers; it is not a human judgement.

Tokens: summed from Strands' per-cycle usage (the anthropic `usage` fields) on every agent object of the run
(start + each resume). The Anthropic-compatible endpoint may report output_tokens=0 on tool-only turns; such cycles are counted
(`cycles_zero_output`) and the output-token figure is then a lower bound. $ uses Anthropic list prices for the
planner model (PRICES below), which is what the tokens would cost on the public API; the endpoint's bill can differ.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
# USD per million tokens, Anthropic first-party list prices (claude-api skill model table, cached 2026-09-25).
# cache_write = 1.25 x input (5-minute TTL), cache_read = 0.1 x input.
PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-haiku-4.5": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
    "claude-opus-5.5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.40},
}
PRICES_SOURCE = "Anthropic list prices per MTok (claude-api model table, cached 2026-09-25)"


# --------------------------------------------------------------------------- isolation


class OverlayStore:
    """Reads: overlay dir first, then the inner (S3) store. Writes: overlay dir only."""

    kind = "overlay"

    def __init__(self, inner: Any, root: Path) -> None:
        from keepframe.io._s3_stub import LocalStore

        self.inner = inner
        self.local = LocalStore(root)
        self.writes = 0

    def get_bytes(self, key: str) -> bytes:
        if self.local.exists(key):
            return self.local.get_bytes(key)
        return bytes(self.inner.get_bytes(key))

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self.writes += 1
        self.local.put_bytes(key, data, content_type)

    def exists(self, key: str) -> bool:
        return self.local.exists(key) or bool(self.inner.exists(key))

    def list(self, prefix: str) -> list[str]:
        return sorted(set(self.local.list(prefix)) | set(self.inner.list(prefix)))


def _forbid(*_a: Any, **_k: Any) -> None:
    raise RuntimeError("agent_determinism: write to the live work bucket / DynamoDB blocked")


def isolate(rep_root: Path) -> OverlayStore:
    """Point every writer of this process at rep_root. Must run before the agent is built."""
    if not os.environ.get("WORK_BUCKET"):
        raise SystemExit("WORK_BUCKET must be set: inputs are read from the work bucket")
    os.environ["CULL_DDB"] = "local"
    os.environ["CULL_LOCAL_ROOT"] = str(rep_root)
    os.environ.setdefault("CULL_TRACE_SINKS", "ddb,s3")
    os.environ.setdefault("CULL_METRICS", "off")
    os.environ.setdefault("CULL_TOOLS", "real")
    from keepframe.io import proxies
    from keepframe.io import s3 as s3mod
    from keepframe.store import dynamodb

    setattr(s3mod, "put_bytes", _forbid)
    setattr(s3mod, "put_json", _forbid)
    setattr(dynamodb.AwsBackend, "__init__", _forbid)
    dynamodb.reset_backend(None)
    if dynamodb.backend_kind() != "local":
        raise SystemExit("DynamoDB backend is not local")
    proxies.reset_store(None)
    inner = proxies.get_store()
    overlay = OverlayStore(inner, rep_root / "s3")
    proxies.reset_store(overlay)
    return overlay


# --------------------------------------------------------------------------- one repetition


def scripted_answers(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Approve every pending proposal as proposed, in the API's answer shape (validate_answers).

    A group answer is the proposed {keep|reject|review: [ids]} map. A singleton's proposal arrives as
    {"verdict": v} (or {v: [id]}); its answer must be the bare verdict string. run.py's --approve-all passes the
    singleton map through unchanged, which validate_answers rejects (reported to the agent owner)."""
    out = []
    for p in result.get("pending_approvals", []):
        for it in p.get("items", []):
            prop = it.get("proposed") or {}
            gid, pid = it.get("group_id"), it.get("photo_id")
            if gid:
                hv: Any = {k: list(v) for k, v in prop.items() if k in ("keep", "reject", "review") and v}
            else:
                hv = prop.get("verdict") or next((k for k in ("keep", "reject", "review") if prop.get(k)), "review")
            out.append({"id": gid or pid, "group_id": gid, "photo_id": pid, "interrupt_id": p["interrupt_id"],
                        "human_verdict": hv, "note": "scripted approve-as-proposed (determinism run, not human)"})
    return out


def preflight(event_id: str) -> None:
    """One real detect_faces + sharpness call: a missing model file must fail here, not inside the planner."""
    from keepframe.agent._tool_real import RealTools

    rt = RealTools(event_id)
    rt.ensure_ingested()
    pid = sorted(rt._ctx.photos)[0]
    faces = rt.detect_faces(pid)
    rt.score_sharpness(pid, "full", "processed")
    if "faces" not in faces and "n_faces" not in faces:
        raise SystemExit(f"preflight: detect_faces returned no face list: {sorted(faces)}")


def _usage_of(agent: Any) -> dict[str, Any]:
    m = agent.event_loop_metrics
    cycles = [c.usage for inv in m.agent_invocations for c in inv.cycles]
    acc = m.accumulated_usage
    return {"input_tokens": int(acc.get("inputTokens", 0)), "output_tokens": int(acc.get("outputTokens", 0)),
            "cache_read_tokens": int(acc.get("cacheReadInputTokens", 0)),
            "cache_write_tokens": int(acc.get("cacheWriteInputTokens", 0)),
            "cycles": len(cycles), "cycles_zero_output": sum(1 for u in cycles if not u.get("outputTokens")),
            "latency_ms": int(m.accumulated_metrics.get("latencyMs", 0))}


def run_rep(event_alias: str, rep: str, root: Path, max_items: int | None) -> int:
    sys.path.insert(0, str(HERE))
    from events import load_aliases

    event_id = load_aliases().get(event_alias, event_alias)
    rep_root = root / rep
    rep_root.mkdir(parents=True, exist_ok=True)
    overlay = isolate(rep_root)

    from keepframe.agent import models
    from keepframe.agent import strands_agent as sa

    if models.provider() != "anthropic":
        raise SystemExit("set CULL_MODEL_PROVIDER=anthropic (the determinism figure is about the real planner)")
    usage: list[dict[str, Any]] = []
    orig_finish = sa._finish

    def finish(agent: Any, sess: Any, result: Any, t0: float) -> dict[str, Any]:
        usage.append(_usage_of(agent))
        return orig_finish(agent, sess, result, t0)

    setattr(sa, "_finish", finish)
    preflight(event_id)
    run_id = f"run_det_{rep}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    progress = rep_root / "progress.jsonl"

    def note(**kw: Any) -> None:
        with progress.open("a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **kw}) + "\n")

    note(stage="start", run_id=run_id, model=models.planner_model_id())
    t0 = time.monotonic()
    res = sa.run_event_agent(event_id, run_id, max_items=max_items)
    pauses = 0
    while res.get("status") == "paused":
        pauses += 1
        note(stage="paused", n=pauses, pending=sum(len(p.get("items", [])) for p in res["pending_approvals"]))
        res = sa.resume_with_decisions(run_id, scripted_answers(res), event_id=event_id, source="scripted")
    wall = time.monotonic() - t0
    out = {"rep": rep, "event_alias": event_alias, "event_id": event_id, "run_id": run_id,
           "model": models.planner_model_id(), "status": res.get("status"), "pauses": pauses,
           "wall_s": round(wall, 1), "summary": res.get("summary"), "usage_per_invocation": usage,
           "overlay_writes": overlay.writes}
    (rep_root / "result.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    note(stage="done", status=res.get("status"), wall_s=round(wall, 1))
    print(json.dumps({k: out[k] for k in ("rep", "run_id", "status", "pauses", "wall_s")}))
    return 0 if res.get("status") == "done" else 1


# --------------------------------------------------------------------------- compare


def _agent_rows(rep_root: Path, event_id: str, run_id: str) -> dict[str, dict[str, Any]]:
    p = rep_root / "s3" / "results" / event_id / "decisions.jsonl"
    rows: dict[str, dict[str, Any]] = {}
    for line in p.read_text().splitlines() if p.exists() else []:
        if line.strip():
            d = json.loads(line)
            if d.get("run_id") == run_id:
                rows[d["photo_id"]] = d  # last line per photo wins
    return rows


def _trace(rep_root: Path, event_id: str, run_id: str) -> list[dict[str, Any]]:
    p = rep_root / "s3" / "results" / event_id / "trace.jsonl"
    out = []
    for line in p.read_text().splitlines() if p.exists() else []:
        if line.strip():
            t = json.loads(line)
            if t.get("run_id") == run_id:
                out.append(t)
    return out


def _proposals(trace: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in trace:
        if t.get("type") == "tool_call" and t.get("tool") == "request_human_approval":
            for item in (t.get("args") or {}).get("items") or []:
                for verdict in ("keep", "reject", "review"):
                    v = (item.get("proposed") or {}).get(verdict)
                    for pid in [v] if isinstance(v, str) else (v or []):
                        out[pid] = verdict
    return out


def cost_usd(model: str, u: dict[str, int]) -> float | None:
    p = PRICES.get(model)
    if p is None:
        return None
    return (u["input_tokens"] * p["input"] + u["output_tokens"] * p["output"]
            + u["cache_read_tokens"] * p["cache_read"] + u["cache_write_tokens"] * p["cache_write"]) / 1e6


def summarise_rep(rep_root: Path) -> dict[str, Any]:
    r = json.loads((rep_root / "result.json").read_text())
    tot = {k: sum(int(u[k]) for u in r["usage_per_invocation"]) for k in
           ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cycles",
            "cycles_zero_output", "latency_ms")}
    trace = _trace(rep_root, r["event_id"], r["run_id"])
    tools = [t.get("tool") for t in trace if t.get("type") == "tool_call"]
    return {**r, "tokens": tot, "usd": cost_usd(r["model"], tot), "tool_calls_trace": len(tools),
            "tool_sequence": tools, "rows": _agent_rows(rep_root, r["event_id"], r["run_id"]),
            "proposals": _proposals(trace)}


def compare(root: Path, reps: list[str], out_dir: Path) -> dict[str, Any]:
    runs = [summarise_rep(root / r) for r in reps]
    a, b = runs[0], runs[1]
    ra, rb = a["rows"], b["rows"]
    common = sorted(set(ra) & set(rb))
    union = sorted(set(ra) | set(rb))
    same_v = sum(ra[p]["verdict"] == rb[p]["verdict"] for p in common)
    same_r = sum(sorted(ra[p].get("reason_codes") or []) == sorted(rb[p].get("reason_codes") or []) for p in common)
    pa, pb = a["proposals"], b["proposals"]
    esc_a, esc_b = set(pa), set(pb)
    pcommon = sorted(esc_a & esc_b)
    seq_same = a["tool_sequence"] == b["tool_sequence"]
    by_src: dict[str, dict[str, Any]] = {}
    for p in common:
        k = ra[p].get("decided_by") if ra[p].get("decided_by") == rb[p].get("decided_by") else "mixed"
        e = by_src.setdefault(str(k), {"n": 0, "same": 0})
        e["n"] += 1
        e["same"] += int(ra[p]["verdict"] == rb[p]["verdict"])
    for e in by_src.values():
        e["identical_verdict_pct"] = round(100 * e["same"] / e["n"], 2)
    per_rep = [{"rep": r["rep"], "status": r["status"], "pauses": r["pauses"], "wall_s": r["wall_s"],
                "model": r["model"], "items": (r.get("summary") or {}).get("items"),
                "photos_decided": len(r["rows"]),
                "verdicts": _count(r["rows"]), "photos_escalated": len(r["proposals"]),
                "tool_calls": (r.get("summary") or {}).get("tool_calls"), "tool_calls_trace": r["tool_calls_trace"],
                "model_calls": (r.get("summary") or {}).get("model_calls"), "tokens": r["tokens"],
                "usd_list_price": None if r["usd"] is None else round(r["usd"], 4)} for r in runs]
    usd = [p["usd_list_price"] for p in per_rep if p["usd_list_price"] is not None]
    rep = {
        "event": a["event_alias"], "reps": reps, "model": a["model"],
        "approvals": "scripted approve-as-proposed (not human)",
        "photos_decided_in_both": len(common), "photos_decided_in_either": len(union),
        "only_in_one_run": len(set(ra) ^ set(rb)),
        "identical_verdict_pct": round(100 * same_v / len(common), 2) if common else None,
        "identical_verdict_pct_of_union": round(100 * same_v / len(union), 2) if union else None,
        "identical_reasons_pct": round(100 * same_r / len(common), 2) if common else None,
        "escalated_sets_identical": esc_a == esc_b, "escalated_in_both": len(pcommon),
        "escalated_only_in_one": len(esc_a ^ esc_b),
        "identical_proposal_pct": round(100 * sum(pa[p] == pb[p] for p in pcommon) / len(pcommon), 2)
        if pcommon else None,
        "tool_sequence_identical": seq_same,
        "by_decided_by": dict(sorted(by_src.items())),
        "per_rep": per_rep,
        "usd_per_event_mean": round(sum(usd) / len(usd), 4) if usd else None,
        "prices": {a["model"]: PRICES.get(a["model"])}, "prices_source": PRICES_SOURCE,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "determinism.json").write_text(json.dumps(rep, indent=1) + "\n")
    (out_dir / "determinism.md").write_text(render(rep))
    raw = out_dir / "raw"
    raw.mkdir(exist_ok=True)
    (raw / "determinism_pairs.json").write_text(json.dumps(
        {p: {"a": ra.get(p, {}).get("verdict"), "b": rb.get(p, {}).get("verdict"),
             "a_reasons": ra.get(p, {}).get("reason_codes"), "b_reasons": rb.get(p, {}).get("reason_codes")}
         for p in union}, indent=1) + "\n")
    return rep


def _count(rows: dict[str, dict[str, Any]]) -> dict[str, int]:
    c: dict[str, int] = {}
    for d in rows.values():
        c[d["verdict"]] = c.get(d["verdict"], 0) + 1
    return dict(sorted(c.items()))


def render(r: dict[str, Any]) -> str:
    def f(x: Any) -> str:
        return "n/a" if x is None else str(x)

    L = [f"# Agent determinism: {r['event']} run twice", "",
         f"Planner {r['model']} through the Anthropic-compatible provider setting, real OpenCV tools on the work bucket, DynamoDB in "
         f"LOCAL_MODE (nothing written to the live tables). Approvals: {r['approvals']}. Same worker output, "
         "same code, same thresholds; only the planner's sampling can differ.", "",
         "| measure | value |", "|---|---|",
         f"| photos decided by the agent loop in both runs | {r['photos_decided_in_both']} "
         f"(either run: {r['photos_decided_in_either']}; only in one: {r['only_in_one_run']}) |",
         f"| identical final verdicts | {f(r['identical_verdict_pct'])} % of photos decided in both "
         f"({f(r['identical_verdict_pct_of_union'])} % of either) |",
         f"| identical reason-code sets | {f(r['identical_reasons_pct'])} % |",
         f"| same photos escalated | {r['escalated_sets_identical']} ({r['escalated_in_both']} in both, "
         f"{r['escalated_only_in_one']} in one only) |",
         f"| identical proposals on photos escalated in both | {f(r['identical_proposal_pct'])} % |",
         f"| identical tool-call sequence | {r['tool_sequence_identical']} |",
         *[f"| identical verdicts, decided_by={k} in both runs | {v['identical_verdict_pct']} % of {v['n']} |"
           for k, v in r["by_decided_by"].items()],
         f"| $ per event (list price, mean of runs) | {f(r['usd_per_event_mean'])} |", "",
         "| run | status | pauses | wall s | items | decided | verdicts | escalated | tool calls | model cycles | "
         "input tok | output tok | cycles with 0 output tok | $ list |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in r["per_rep"]:
        t = p["tokens"]
        L.append(f"| {p['rep']} | {p['status']} | {p['pauses']} | {p['wall_s']} | {f(p['items'])} | "
                 f"{p['photos_decided']} | {', '.join(f'{k} {v}' for k, v in p['verdicts'].items())} | "
                 f"{p['photos_escalated']} | {f(p['tool_calls'])} | {t['cycles']} | {t['input_tokens']:,} | "
                 f"{t['output_tokens']:,} | {t['cycles_zero_output']} | {f(p['usd_list_price'])} |")
    L += ["", f"Prices: {r['prices']} ({r['prices_source']}). If the endpoint reports 0 output tokens on "
          "tool-only turns, those cycles are counted above and the output-token and $ figures are lower bounds. "
          "`decided_by=scripted` rows are the escalated photos settled by the scripted approve-as-proposed "
          "reviewer, so their agreement measures the agent's proposals, not a person.", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--event", default="E09", help="alias (eval/events.py) or event id")
    r.add_argument("--rep", required=True)
    r.add_argument("--root", type=Path, default=Path("/tmp/eval-det"))
    r.add_argument("--max-items", type=int, default=None)
    c = sub.add_parser("compare")
    c.add_argument("--root", type=Path, default=Path("/tmp/eval-det"))
    c.add_argument("--reps", nargs=2, default=["A", "B"])
    c.add_argument("--out-dir", type=Path, default=HERE / "results")
    a = ap.parse_args()
    if a.cmd == "run":
        return run_rep(a.event, a.rep, a.root, a.max_items)
    rep = compare(a.root, a.reps, a.out_dir)
    print(render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

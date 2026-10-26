#!/usr/bin/env python3
"""Evaluation metrics for KeepFrame (docs/EVALUATION.md §2).

Inputs per event (local files; fetch from S3 first with --fetch):
  eval/labels/<eventId>.csv              human labels (docs/EVALUATION.md §1 columns; several labellers allowed)
  <results-dir>/<eventId>/decisions.jsonl agent verdicts, append-only, last line per photo wins
  <results-dir>/<eventId>/groups.json     {groups:[{group_id, photo_ids|members, best_photo_id?, ...}], singletons}
  <results-dir>/<eventId>/trace.jsonl     agent trace (docs/AGENT.md "Trace schema"), optional
  <results-dir>/<eventId>.run2/decisions.jsonl  optional second run of the same event (robustness)

Outputs: eval/results/metrics.json, metrics.md, groups_purity.csv, agent_metrics.csv.

Labels are optional. An event with results/<eventId>/ but no eval/labels/<eventId>.csv still gets the
agent-loop metrics (trace.jsonl), run counts (decisions.jsonl) and group shape (groups.json); every
label-dependent section is marked "no labels yet". Events = --events, else every labelled event plus
every results/<eventId>/ that has a decisions.jsonl. Pivots come from services/api/app/pivots.py so they
match GET /runs/{id}/trace.

Labeller classes (column `labeller`): `photographer` (or `photographer:<initials>`) = the ground truth the
report may call human/photographer; `team` (or `team:<initials>`) = a team member's own labels, reported ONLY
as "team labels (not photographer)" with their own kappa against the worker and the agent, never as ground
truth; `lead-mechanism-test` / `lead_test` rows are dropped; any other value is `unclassified` and reported
as such (never as photographer). eval/sample_for_labelling.py --labeller stamps the class into the todo CSV.

Committed outputs use E-index aliases (eval/events.py) and alias-rewritten run ids; a UUID guard refuses to
write metrics.md / metrics.json / agent_metrics.csv if a raw id slips through (use --raw-ids for a local
debug copy). groups_purity.csv stays gitignored (group ids).

Answer sources: S3 trace.jsonl can carry a stale source for an answer the team relabelled later in DynamoDB.
--runs-file (a read-only export of the RUN# items, {eventId: [items]}) makes RUN#.resume_decisions win:
any resume whose stored answers say lead_test or scripted is counted as such, never as ui/http.

Conventions (stated in the output so the report can quote them):
- Primary label = the labeller with the most rows for the event (ties: alphabetical); the other
  labeller's rows only feed Cohen's kappa on the overlap.
- Reject P/R: agent `review` counts as not-reject; review rates are reported separately.
- DUPLICATE_OF:<id> and BURST_NOT_BEST are merged into one DUPLICATE family for per-reason scores.
- Duplicate retrieval (imagededup-style): for every photo, ground truth = other members of its
  human group, retrieved = other members of its agent group. Precision/recall are averaged over
  queries that have a non-empty ground truth (or retrieval for precision), MAP as in
  imagededup.evaluation (AP over the retrieved list, which here is unordered -> order by photo id).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
REASON_FAMILIES = {
    "BLUR_MOTION": "BLUR_MOTION",
    "BLUR_FOCUS": "BLUR_FOCUS",
    "EYES_CLOSED": "EYES_CLOSED",
    "EXPOSURE_UNDER": "EXPOSURE_UNDER",
    "EXPOSURE_OVER": "EXPOSURE_OVER",
    "LOW_CONTRAST": "LOW_CONTRAST",
    "DUPLICATE_OF": "DUPLICATE",
    "BURST_NOT_BEST": "DUPLICATE",
    "NO_SUBJECT": "NO_SUBJECT",
}
SCORED_REASONS = ("BLUR_MOTION", "BLUR_FOCUS", "EYES_CLOSED", "DUPLICATE", "EXPOSURE_UNDER", "EXPOSURE_OVER",
                  "LOW_CONTRAST", "NO_SUBJECT")


# --------------------------------------------------------------------------- io
def reason_family(code: str) -> str | None:
    return REASON_FAMILIES.get(code.split(":", 1)[0].strip().upper())


def families(codes: Iterable[str]) -> set[str]:
    return {f for c in codes if c and (f := reason_family(c))}


LEAD_TEST_LABELLERS = frozenset({"lead-mechanism-test", "lead_test"})
NO_PHOTOGRAPHER = "no photographer labels yet (n=0)"


def labeller_class(name: str) -> str:
    """photographer | team | lead_test | unclassified (see module docstring)."""
    n = (name or "").strip().lower()
    if n in LEAD_TEST_LABELLERS:
        return "lead_test"
    head = n.split(":", 1)[0]
    if head in ("photographer", "team"):
        return head
    return "unclassified"


def read_labels(path: Path) -> dict[str, dict[str, dict]]:
    """-> {labeller: {photo_id: row}}; lead-test rows are dropped (never labels)."""
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            pid = (row.get("photo_id") or "").strip()
            if not pid:
                continue
            verdict = (row.get("verdict_human") or "").strip().lower()
            if verdict not in ("keep", "reject"):
                continue  # unlabelled / todo rows
            lab = (row.get("labeller") or "unknown").strip()
            if labeller_class(lab) == "lead_test":
                continue
            out[lab][pid] = {
                "photo_id": pid,
                "group_id_human": (row.get("group_id_human") or "").strip() or f"solo:{pid}",
                "verdict": verdict,
                "reasons": families((row.get("reasons_human") or "").split(";")),
                "best": (row.get("best_of_group_human") or "0").strip() in ("1", "true", "True", "yes"),
            }
    return dict(out)


def label_files(labels_dir: Path, event_id: str) -> list[Path]:
    """<eventId>.csv (photographer) and <eventId>.team.csv (team); *.todo.csv are never read."""
    return [p for p in (labels_dir / f"{event_id}.csv", labels_dir / f"{event_id}.team.csv") if p.exists()]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def read_decisions(path: Path) -> dict[str, dict]:
    out = {}
    for r in read_jsonl(path):
        # write_decision args may arrive as {"decisions":[...]} lines or one decision per line
        for d in r.get("decisions", [r]) if isinstance(r, dict) else []:
            if isinstance(d, dict) and isinstance(d.get("photo_id"), str):
                out[d["photo_id"]] = d
    return out


def read_groups(path: Path) -> tuple[dict[str, str], dict[str, str | None], dict[str, list[str]]]:
    """-> (photo->group_id, group->best_photo_id, group->ranking photo ids)"""
    if not path.exists():
        return {}, {}, {}
    doc = json.loads(path.read_text())
    p2g: dict[str, str] = {}
    best: dict[str, str | None] = {}
    ranking: dict[str, list[str]] = {}
    for g in doc.get("groups") or []:
        gid = g.get("group_id")
        members = g.get("photo_ids") or g.get("members") or []
        if not isinstance(gid, str):
            continue
        for m in members:
            p2g[m] = gid
        best[gid] = g.get("best_photo_id")
        ranking[gid] = [r["photo_id"] for r in (g.get("ranking") or []) if isinstance(r, dict) and "photo_id" in r]
    singles = doc.get("singletons")
    if isinstance(singles, list):
        for s in singles:
            pid = s if isinstance(s, str) else (s or {}).get("photo_id")
            if pid and pid not in p2g:
                p2g[pid] = f"solo:{pid}"
    return p2g, best, ranking


# --------------------------------------------------------------------------- metrics
def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p is not None and r is not None and p + r else (0.0 if p == 0 or r == 0 else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": _r(p), "recall": _r(r), "f1": _r(f1), "support": tp + fn}


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, nd)


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    """Unweighted Cohen's kappa; uses scikit-learn when installed (bench extra), else the closed form."""
    if not a or len(a) != len(b):
        return None
    try:
        from sklearn.metrics import cohen_kappa_score

        if len(set(a) | set(b)) < 2:
            return 1.0 if a == b else 0.0
        return float(cohen_kappa_score(a, b))
    except ImportError:
        n = len(a)
        po = sum(x == y for x, y in zip(a, b)) / n
        ca, cb = Counter(a), Counter(b)
        pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
        return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def cluster_scores(human: dict[str, str], agent: dict[str, str]) -> dict:
    """purity = sum over agent groups of max overlap with one human group / N;
    completeness = fraction of human groups (size>1) whose members all share one agent group."""
    pids = sorted(set(human) & set(agent))
    n = len(pids)
    if not n:
        return {"n": 0, "purity": None, "completeness": None, "n_human_groups": 0, "n_agent_groups": 0}
    by_agent: dict[str, Counter] = defaultdict(Counter)
    by_human: dict[str, set] = defaultdict(set)
    for p in pids:
        by_agent[agent[p]][human[p]] += 1
        by_human[human[p]].add(agent[p])
    purity = sum(c.most_common(1)[0][1] for c in by_agent.values()) / n
    multi = {h: s for h, s in by_human.items() if sum(1 for p in pids if human[p] == h) > 1}
    complete = sum(1 for s in multi.values() if len(s) == 1)
    # Inverse purity (each human group's best agent match), the standard companion to purity.
    inv_purity = sum(max(Counter(agent[p] for p in pids if human[p] == h).values()) for h in by_human) / n
    return {"n": n, "purity": _r(purity), "inverse_purity": _r(inv_purity),
            "completeness": _r(complete / len(multi)) if multi else None,
            "n_human_groups_multi": len(multi), "n_human_groups": len(by_human), "n_agent_groups": len(by_agent)}


def retrieval_scores(human: dict[str, str], agent: dict[str, str]) -> dict:
    """imagededup.evaluation-style precision / recall / MAP for duplicate retrieval."""
    pids = sorted(set(human) & set(agent))
    hm: dict[str, set] = defaultdict(set)
    am: dict[str, set] = defaultdict(set)
    for p in pids:
        hm[human[p]].add(p)
        am[agent[p]].add(p)
    precs, recs, aps = [], [], []
    for p in pids:
        gt = hm[human[p]] - {p}
        ret = sorted(am[agent[p]] - {p})
        if ret:
            precs.append(len(gt & set(ret)) / len(ret))
        if gt:
            recs.append(len(gt & set(ret)) / len(gt))
            hits, s = 0, 0.0
            for k, r in enumerate(ret, 1):
                if r in gt:
                    hits += 1
                    s += hits / k
            aps.append(s / len(gt))
    mean = lambda v: _r(statistics.fmean(v)) if v else None  # noqa: E731
    return {"precision": mean(precs), "recall": mean(recs), "map": mean(aps),
            "n_queries_with_gt": len(recs), "n_queries_with_retrieval": len(precs)}


def best_of_burst(primary: dict[str, dict], agent_p2g: dict[str, str], agent_best: dict[str, str | None],
                  agent_rank: dict[str, list[str]], decisions: dict[str, dict]) -> dict:
    """Human groups with >1 member and exactly one marked best are scored.

    The matching agent group is the one holding most of the human group's members (ties go to the
    agent group that holds the human best). Its best is groups.json best_photo_id, else the member
    the agent kept. top-1 = that equals the human best; top-2 = the human best is in the first two
    of the agent ranking (or equals the agent best when no ranking exists)."""
    hg: dict[str, list[dict]] = defaultdict(list)
    for r in primary.values():
        hg[r["group_id_human"]].append(r)
    n = top1 = top2 = 0
    for members in hg.values():
        if len(members) < 2:
            continue
        bests = [m["photo_id"] for m in members if m["best"]]
        if len(bests) != 1:
            continue
        hb = bests[0]
        ag = Counter(agent_p2g.get(m["photo_id"]) for m in members if agent_p2g.get(m["photo_id"]))
        if not ag:
            continue
        top = max(ag.values())
        tied = sorted(g for g, c in ag.items() if c == top)
        # tie -> the agent group holding the human best (the group the agent "should" have ranked it in)
        gid = agent_p2g.get(hb) if agent_p2g.get(hb) in tied else tied[0]
        ab = agent_best.get(gid)
        ranking = agent_rank.get(gid) or []
        if ab is None:
            kb = [m["photo_id"] for m in members if agent_p2g.get(m["photo_id"]) == gid and
                  decisions.get(m["photo_id"], {}).get("verdict") == "keep"]
            ab = kb[0] if kb else None
        n += 1
        top1 += int(ab == hb)
        top2 += int(hb in (ranking[:2] if ranking else ([ab] if ab else [])))
    return {"n_groups": n, "top1_agreement": _r(top1 / n) if n else None,
            "top2_agreement": _r(top2 / n) if n else None}


# human_source values that mean a person answered (keepframe/schemas.py HumanSource minus 'scripted')
REAL_HUMAN_SOURCES = frozenset({"ui", "file", "http"})


def _ids(v: Any) -> list[str]:
    return [v] if isinstance(v, str) else [x for x in (v or []) if isinstance(x, str)]


def _flat(xs: Any) -> list[dict]:
    """interrupt_resumed.decisions may be a list of dicts or a list of lists of dicts (one per interrupt)."""
    out: list[dict] = []
    for x in xs or []:
        if isinstance(x, dict):
            out.append(x)
        elif isinstance(x, list):
            out.extend(_flat(x))
    return out


def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    a = sorted(vals)
    k = (len(a) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(a) - 1)
    return a[lo] + (a[hi] - a[lo]) * (k - lo)


def load_pivot_finder(path: Path | None) -> Any:
    """services/api/app/pivots.py:find_pivots (stdlib only), so pivot counts match GET /runs/{id}/trace.
    Returns None when the module is not available; pivots are then reported as n/a, never re-derived."""
    import importlib.util

    for cand in [path] if path else [HERE.parent / "services" / "api" / "app" / "pivots.py"]:
        if cand and cand.exists():
            spec = importlib.util.spec_from_file_location("kc_api_pivots", cand)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod.find_pivots
    return None


def agent_loop_metrics(trace: list[dict], decisions: dict[str, dict], primary: dict[str, dict],
                       group_members: dict[str, list[str]] | None = None, find_pivots: Any = None,
                       source_override: str | None = None) -> dict:
    """Agent-loop metrics from trace.jsonl (docs/AGENT.md "Trace schema" lines) joined with decisions.jsonl.

    Human sources: an escalated photo is `pending` until an interrupt_resumed line answers it; then its
    source is the decision row's `human_source` (ui|file|http|scripted), else the resume line's, else
    `unknown`. Only ui|file|http count as a person: `scripted` answers (demo/CI reviewer) and `unknown`
    ones (no recorded source) are counted separately and EXCLUDED from every human-agreement figure, as
    are pending ones; `human_agreed_pct` covers answers with a recorded human source only.
    run_status: finished/stopped when run_end exists, awaiting_approval when the last interrupt has no
    later interrupt_resumed, else running_or_incomplete.
    source_override (from RUN#.resume_decisions, see source_override()): when the stored answers of this run say
    lead_test or scripted, every answer of the run is counted under that source, whatever the trace says.
    """
    tool_calls = [t for t in trace if t.get("type") == "tool_call"]
    tool_results = [t for t in trace if t.get("type") == "tool_result"]
    by_tool = Counter(t.get("tool") for t in tool_calls)
    members = {g: set(m) for g, m in (group_members or {}).items()}
    for d in decisions.values():
        if d.get("group_id"):
            members.setdefault(d["group_id"], set()).add(d["photo_id"])
    agent_decided = {p for p, d in decisions.items() if d.get("decided_by") not in (None, "worker")}
    escalated: set[str] = set()
    proposals: dict[str, str] = {}  # photo_id -> proposed verdict
    n_items = 0
    for t in tool_calls:
        if t.get("tool") != "request_human_approval":
            continue
        for item in (t.get("args") or {}).get("items") or []:
            n_items += 1
            ids = set(_ids(item.get("photo_id")))
            if item.get("group_id"):
                ids |= members.get(item["group_id"], set())
            for verdict in ("keep", "reject", "review"):
                for pid in _ids((item.get("proposed") or {}).get(verdict)):
                    proposals[pid] = verdict
                    ids.add(pid)
            escalated |= ids
    # Which escalated photos were actually answered, and by which source. An escalation is `pending` until an
    # interrupt_resumed line answers it; a pending photo's PHOTO# row still carries the worker/agent verdict
    # with needs_approval=true and no human_source. Never attribute an unanswered escalation to a human.
    answered_src: dict[str, str] = {}
    answered_verdict: dict[str, str] = {}
    for t in trace:
        if t.get("type") != "interrupt_resumed":
            continue
        for d in _flat(t.get("decisions")):
            hv = d.get("human_verdict") or {}
            for verdict in ("keep", "reject", "review"):
                for pid in _ids(hv.get(verdict)):
                    if pid in answered_verdict:
                        continue
                    answered_verdict[pid] = verdict
                    answered_src[pid] = source_override or (decisions.get(pid) or {}).get("human_source") \
                        or d.get("human_source") or t.get("human_source") or "unknown"
    src = {p: answered_src.get(p, "pending") for p in escalated}
    agree = disagree = 0
    scripted_answered = unknown_answered = lead_test_answered = 0
    for pid, verdict in answered_verdict.items():
        if pid not in proposals:
            continue
        if src.get(pid) == "scripted":
            scripted_answered += 1
            continue
        if src.get(pid) == "lead_test":
            lead_test_answered += 1
            continue
        if src.get(pid) not in REAL_HUMAN_SOURCES:  # no recorded source: cannot claim a person answered
            unknown_answered += 1
            continue
        agree += int(proposals[pid] == verdict)
        disagree += int(proposals[pid] != verdict)
    esc_lab = [p for p in escalated if p in primary and p in decisions and src.get(p) in REAL_HUMAN_SOURCES]
    # run status from the trace: finished (run_end), or paused (an interrupt not followed by a resume)
    last_int = max((int(t.get("seq", 0)) for t in trace if t.get("type") == "interrupt"), default=-1)
    last_res = max((int(t.get("seq", 0)) for t in trace if t.get("type") == "interrupt_resumed"), default=-1)
    has_end = any(t.get("type") == "run_end" for t in trace)
    esc_lab_agree = sum(decisions[p].get("verdict") == primary[p]["verdict"] for p in esc_lab)
    timed = [t for t in trace if t.get("type") in ("tool_result", "tool_call")]  # results carry it on real traces
    lat = [float(t["latency_ms"]) for t in timed if isinstance(t.get("latency_ms"), (int, float))]
    lat_by_tool: dict[str, list[float]] = defaultdict(list)
    for t in timed:
        if isinstance(t.get("latency_ms"), (int, float)):
            lat_by_tool[str(t.get("tool"))].append(float(t["latency_ms"]))
    tokens = sum(int((t.get("usage") or {}).get(k, 0) or 0) for t in trace for k in ("input_tokens", "output_tokens"))
    loop_guard = sum(1 for t in trace if t.get("type") in ("loop_guard", "guard_triggered", "budget_stop"))
    hooks = Counter(t.get("hook") for t in trace if t.get("type") == "model_reasoning" and t.get("hook"))
    pivots = find_pivots(trace) if find_pivots else None
    header = next((t for t in trace if t.get("type") == "run_header"), {})
    end = next((t for t in reversed(trace) if t.get("type") == "run_end"), {})
    summ = end.get("summary") or {}
    n_event_photos = header.get("n_photos") or len(decisions) or None
    hs = Counter(src.values())
    if has_end:
        status = summ.get("status") or ("stopped" if summ.get("stopped") else "finished")
    elif last_int > last_res:
        status = "awaiting_approval"
    else:
        status = "running_or_incomplete"
    return {
        "run_id": header.get("run_id") or end.get("run_id"),
        "run_status": status,
        "model": header.get("model"), "provider": header.get("provider"),
        "n_trace_lines": len(trace),
        "tool_calls": len(tool_calls),
        "tool_calls_per_event": len(tool_calls),
        "tool_calls_excl_next_work_item": sum(v for k, v in by_tool.items() if k != "next_work_item"),
        "tool_calls_run_summary": summ.get("tool_calls"),  # the agent's own count (excludes next_work_item)
        "model_calls": summ.get("model_calls"),
        "tool_calls_per_photo": _r(len(tool_calls) / n_event_photos) if n_event_photos else None,
        "tool_calls_per_agent_photo": _r(len(tool_calls) / len(agent_decided)) if agent_decided else None,
        "photos_in_event": n_event_photos,
        "photos_agent_decided": len(agent_decided),
        "work_items": header.get("n_items") or summ.get("items"),
        "tool_calls_by_tool": dict(sorted(by_tool.items(), key=lambda kv: (-kv[1], str(kv[0])))),
        "tool_results_not_ok": sum(1 for t in tool_results if t.get("status") not in (None, "ok")),
        "approval_items": n_items,
        "pauses": sum(1 for t in trace if t.get("type") == "interrupt"),
        "resumes": sum(1 for t in trace if t.get("type") == "interrupt_resumed"),
        "photos_escalated": len(escalated),
        "pct_escalated": _r(100 * len(escalated) / n_event_photos, 2) if n_event_photos else None,
        "pct_escalated_of_agent_photos": _r(100 * len(escalated & agent_decided) / len(agent_decided), 2)
        if agent_decided else None,
        "human_source_breakdown": dict(sorted(hs.items())),
        "escalations_pending": hs.get("pending", 0),
        "scripted_answers_excluded": scripted_answered,
        "lead_test_answers_excluded": lead_test_answered,
        "source_override": source_override,
        "agent_active_s": trace_active_s(trace),
        "host": header.get("host"),
        "unknown_source_answers_excluded": unknown_answered,
        "escalation_human_agreed_pct": _r(100 * agree / (agree + disagree), 2) if agree + disagree else None,
        "escalation_human_agreed_n": agree + disagree,
        "escalated_final_vs_label_agreement_pct": _r(100 * esc_lab_agree / len(esc_lab), 2) if esc_lab else None,
        "pivots": len(pivots) if pivots is not None else None,
        "pivots_by_policy": dict(Counter(p.get("policy") or p.get("kind") for p in pivots)) if pivots is not None
        else None,
        "pivots_by_kind": dict(Counter(p.get("kind") for p in pivots)) if pivots is not None else None,
        "policy_hooks": dict(hooks),
        "tool_latency_ms_p50": _r(_pct(lat, 0.50), 2),
        "tool_latency_ms_p95": _r(_pct(lat, 0.95), 2),
        "tool_latency_ms_mean": _r(statistics.fmean(lat), 2) if lat else None,
        "tool_latency_ms_by_tool": {k: {"n": len(v), "p50": _r(_pct(v, 0.5), 2), "p95": _r(_pct(v, 0.95), 2)}
                                    for k, v in sorted(lat_by_tool.items())},
        "run_wall_s": summ.get("wall_s"),
        "model_tokens": tokens or None,
        "loop_guard_triggers": loop_guard,
        "stopped": summ.get("stopped"),
    }


def run_counts(decisions: dict[str, dict]) -> dict:
    """Run-level counts from decisions.jsonl (last line per photo)."""
    reasons = Counter(c.split(":", 1)[0] for d in decisions.values() for c in (d.get("reason_codes") or []))
    return {"n_photos": len(decisions),
            "verdicts": dict(Counter(d.get("verdict") for d in decisions.values())),
            "decided_by": dict(Counter(d.get("decided_by") for d in decisions.values())),
            "human_source": dict(Counter(d.get("human_source") for d in decisions.values() if d.get("human_source"))),
            "needs_approval": sum(1 for d in decisions.values() if d.get("needs_approval")),
            "reason_codes": dict(sorted(reasons.items(), key=lambda kv: -kv[1]))}


def group_shape(groups_path: Path) -> dict | None:
    """Group-size histogram, singletons and tie/margin stats from groups.json."""
    if not groups_path.exists():
        return None
    doc = json.loads(groups_path.read_text())
    gs = [g for g in doc.get("groups") or [] if isinstance(g, dict)]
    sizes = [len(g.get("photo_ids") or g.get("members") or []) for g in gs]
    singles = doc.get("singletons")
    n_single = len(singles) if isinstance(singles, list) else int(singles or 0)
    margins = [float(g["margin"]) for g in gs if isinstance(g.get("margin"), (int, float))]
    exact = doc.get("exact_duplicates")
    return {"n_groups": len(gs), "n_singletons": n_single,
            "photos_in_groups": sum(sizes),
            "size_histogram": {str(k): v for k, v in sorted(Counter(sizes).items())},
            "max_size": max(sizes) if sizes else 0,
            "ties": sum(1 for g in gs if g.get("tie")),
            "margin_lt_0_05": sum(1 for m in margins if m < 0.05),
            "exact_duplicates": len(exact) if isinstance(exact, list) else exact}


def robustness(d1: dict[str, dict], d2: dict[str, dict]) -> dict | None:
    common = sorted(set(d1) & set(d2))
    if not common:
        return None
    same = sum(d1[p].get("verdict") == d2[p].get("verdict") for p in common)
    same_r = sum(sorted(d1[p].get("reason_codes") or []) == sorted(d2[p].get("reason_codes") or []) for p in common)
    return {"n": len(common), "identical_verdict_pct": _r(100 * same / len(common), 2),
            "identical_reasons_pct": _r(100 * same_r / len(common), 2),
            "only_in_one_run": len(set(d1) ^ set(d2))}


# --------------------------------------------------------------------------- label classes, runs, sources


def label_counts(all_lab: dict[str, dict[str, dict]]) -> dict:
    """Rows (photos with a keep/reject) per labeller class; labeller names are not reported."""
    c: Counter = Counter()
    n_lab: Counter = Counter()
    for name, rows in all_lab.items():
        k = labeller_class(name)
        c[k] += len(rows)
        n_lab[k] += 1
    return {k: {"rows": c.get(k, 0), "labellers": n_lab.get(k, 0)} for k in ("photographer", "team", "unclassified")}


def _verdict_pairs(lab: dict[str, dict], dec: dict[str, dict], mapping: str) -> tuple[list[str], list[str]]:
    """Label verdicts vs a machine verdict. mapping 'review_as_keep': agent/worker review counts as not-reject
    (same convention as reject P/R)."""
    a, b = [], []
    for pid in sorted(set(lab) & set(dec)):
        v = (dec[pid].get("verdict") or "").lower()
        a.append(lab[pid]["verdict"])
        b.append("reject" if v == "reject" else "keep")
    return a, b


def team_agreement(all_lab: dict[str, dict[str, dict]], photographer: dict[str, dict[str, dict]],
                   decisions_path: Path) -> dict | None:
    """Team labels (not photographer): kappa vs the worker verdict, vs the final verdict (agent / scripted /
    human rows win over the worker's), and vs the photographer where both labelled the same photo."""
    team = {k: v for k, v in all_lab.items() if labeller_class(k) == "team"}
    if not team:
        return None
    rows: dict[str, dict] = {}
    worker: dict[str, dict] = {}
    for r in read_jsonl(decisions_path):
        for d in r.get("decisions", [r]) if isinstance(r, dict) else []:
            if isinstance(d, dict) and isinstance(d.get("photo_id"), str):
                rows[d["photo_id"]] = d
                if d.get("decided_by", "worker") == "worker":
                    worker[d["photo_id"]] = d
    agent = {p: d for p, d in rows.items() if d.get("decided_by") not in (None, "worker")}
    merged: dict[str, dict] = {}
    for name in sorted(team, key=lambda k: (-len(team[k]), k)):
        for pid, r in team[name].items():
            merged.setdefault(pid, r)
    out: dict[str, Any] = {"n_labellers": len(team), "n_rows": len(merged)}
    for key, dec in (("vs_worker", worker), ("vs_final", rows), ("vs_agent_rows", agent)):
        a, b = _verdict_pairs(merged, dec, "review_as_keep")
        out[key] = {"n": len(a), "kappa": _r(cohen_kappa(a, b)) if a else None,
                    "raw_agreement": _r(sum(x == y for x, y in zip(a, b)) / len(a)) if a else None}
    ph: dict[str, dict] = {}
    for name in sorted(photographer, key=lambda k: (-len(photographer[k]), k)):
        for pid, r in photographer[name].items():
            ph.setdefault(pid, r)
    ov = sorted(set(merged) & set(ph))
    out["vs_photographer"] = {"n": len(ov), "kappa": _r(cohen_kappa([merged[p]["verdict"] for p in ov],
                                                                      [ph[p]["verdict"] for p in ov])) if ov else None}
    return out


def source_override(runs: list[dict] | None, run_id: str | None) -> str | None:
    """lead_test / scripted when the RUN# item's stored resume answers say so (DynamoDB is relabelled by hand;
    the S3 trace copy is not)."""
    if not runs or not run_id:
        return None
    item = next((r for r in runs if str(r.get("sk", "")).endswith(f"RUN#{run_id}") or r.get("run_id") == run_id), None)
    if not item:
        return None
    srcs = {str(d.get("human_source")) for d in item.get("resume_decisions") or [] if isinstance(d, dict)}
    srcs |= {"lead_test" for d in item.get("resume_decisions") or []
             if isinstance(d, dict) and str(d.get("labeller")) in LEAD_TEST_LABELLERS}
    if str(item.get("approvals_source")) == "scripted":
        srcs.add("scripted")
    for s in ("lead_test", "scripted"):
        if s in srcs:
            return s
    return None


def _only_run(trace: list[dict]) -> str | None:
    ids = {t.get("run_id") for t in trace if t.get("run_id")}
    return str(next(iter(ids))) if len(ids) == 1 else None


def _ts(t: dict) -> float | None:
    import datetime as dt

    v = t.get("ts")
    if not isinstance(v, str):
        return None
    try:
        return dt.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def trace_active_s(trace: list[dict]) -> float | None:
    """Agent working wall time from trace timestamps: run_header (or first line) to the first interrupt, then
    each interrupt_resumed to the next interrupt / run_end. Time waiting for an answer is excluded."""
    seg_start: float | None = None
    total = 0.0
    seen = False
    for t in sorted(trace, key=lambda x: int(x.get("seq", 0))):
        ts = _ts(t)
        if ts is None:
            continue
        if seg_start is None and t.get("type") != "interrupt":
            seg_start = ts
        if t.get("type") in ("interrupt", "run_end") and seg_start is not None:
            total += max(0.0, ts - seg_start)
            seen = True
            seg_start = None
        elif t.get("type") == "interrupt_resumed":
            seg_start = ts
    if seg_start is not None:  # still running / paused without an interrupt line: count to the last line
        last = max((x for x in (_ts(t) for t in trace) if x is not None), default=seg_start)
        total += max(0.0, last - seg_start)
        seen = True
    return round(total, 1) if seen else None


def agent_runs(trace: list[dict], rdir: Path, find_pivots: Any, runs: list[dict] | None) -> list[dict]:
    """One row per agent run in the append-only trace.jsonl (label-independent): status, host, tool and model
    calls, pauses/resumes, answer sources, pivots per policy, active wall time, tokens if the trace has them."""
    ids = {str(t["run_id"]) for t in trace if t.get("run_id")}
    only = next(iter(ids)) if len(ids) == 1 else None  # lines without a run_id belong to the only run
    by: dict[str, list[dict]] = {}
    for t in trace:
        by.setdefault(str(t.get("run_id") or only), []).append(t)
    sel = select_run(trace, rdir)[1].get("selected_run") or only
    out = []
    for rid, lines in by.items():
        al = agent_loop_metrics(lines, {}, {}, _members(rdir), find_pivots, source_override(runs, rid))
        item = next((r for r in runs or [] if str(r.get("sk", "")).endswith(f"RUN#{rid}")), {})
        hdr = next((t for t in lines if t.get("type") == "run_header"), {})
        out.append({"run_id": rid, "started": hdr.get("ts") or (lines[0].get("ts") if lines else None),
                    "host": al.get("host") or item.get("agent_host") or item.get("instance_type"),
                    "run_kind": hdr.get("run_kind") or item.get("run_kind"), "status": al["run_status"],
                    "is_results_run": rid == sel, "tool_calls": al["tool_calls"],
                    "tool_calls_excl_next_work_item": al["tool_calls_excl_next_work_item"],
                    "model_calls": al["model_calls"], "pauses": al["pauses"], "resumes": al["resumes"],
                    "approval_items": al["approval_items"], "photos_escalated": al["photos_escalated"],
                    "answer_sources": al["human_source_breakdown"], "pivots": al["pivots"],
                    "pivots_by_policy": al["pivots_by_policy"], "agent_active_s": al["agent_active_s"],
                    "agent_wall_s_run_item": item.get("agent_wall_s"), "tool_results_not_ok": al["tool_results_not_ok"],
                    "model_tokens": al["model_tokens"], "tool_latency_ms_p50": al["tool_latency_ms_p50"],
                    "tool_latency_ms_p95": al["tool_latency_ms_p95"]})
    return sorted(out, key=lambda r: str(r["started"]))


# --------------------------------------------------------------------------- aliases (committed outputs carry no ids)

UUID_RE = __import__("re").compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class Aliaser:
    """event id -> E-alias, run id -> '<alias> <kind> <timestamp>' (the uuid prefix and suffix are dropped)."""

    def __init__(self, aliases: dict[str, str]) -> None:
        self.by_id = {v: k for k, v in aliases.items()}

    def event(self, event_id: str) -> str:
        return self.by_id.get(event_id) or ("E??" if UUID_RE.fullmatch(event_id) else event_id)

    def run(self, run_id: Any, event_id: str) -> Any:
        if not isinstance(run_id, str):
            return run_id
        import re

        ts = re.search(r"(\d{8}T\d{6}Z)", run_id)
        kind = ("agentcore" if "agentcore" in run_id else "aws" if "_aws_" in run_id else
                "det" if run_id.startswith("run_det_") else "agent" if "agent" in run_id else "worker")
        return f"{self.event(event_id)}-{kind}-{ts.group(1) if ts else 'run'}"

    def scrub(self, obj: Any, event_id: str, run_ids: set[str]) -> Any:
        if isinstance(obj, dict):
            return {self.scrub(k, event_id, run_ids): self.scrub(v, event_id, run_ids) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.scrub(v, event_id, run_ids) for v in obj]
        if isinstance(obj, str):
            if obj in run_ids:
                return self.run(obj, event_id)
            out = obj
            for rid in sorted(run_ids, key=len, reverse=True):
                out = out.replace(rid, str(self.run(rid, event_id)))
            for eid, al in self.by_id.items():
                out = out.replace(eid, al).replace(eid.replace("-", ""), al)
                out = out.replace(eid[:8], al)
            return UUID_RE.sub("<id>", out)
        return obj


def _run_ids(e: dict) -> set[str]:
    ids = {r["run_id"] for r in e.get("agent_runs") or []}
    al = e.get("agent_loop") or {}
    ids |= {x for x in [al.get("run_id"), al.get("selected_run"), *(al.get("other_runs") or [])] if x}
    return {str(i) for i in ids}


def assert_no_ids(text: str, where: str) -> None:
    m = UUID_RE.search(text)
    if m:
        raise SystemExit(f"refusing to write {where}: raw id {m.group(0)[:8]}... (use --raw-ids for a local copy)")



def evaluate_event(event_id: str, labels_dir: Path, results_dir: Path, find_pivots: Any = None,
                   runs: list[dict] | None = None) -> dict:
    """Photographer labels are ground truth; team labels only get their own agreement section; rows with an
    unclassified labeller are counted and reported, never scored as photographer."""
    all_lab: dict[str, dict[str, dict]] = {}
    for lpath in label_files(labels_dir, event_id):
        for lab, rows in read_labels(lpath).items():
            all_lab.setdefault(lab, {}).update(rows)
    rdir = results_dir / event_id
    by_lab = {k: v for k, v in all_lab.items() if labeller_class(k) == "photographer"}
    extra = {"label_counts": label_counts(all_lab),
             "team_labels": team_agreement(all_lab, by_lab, rdir / "decisions.jsonl"),
             "agent_runs": agent_runs(read_jsonl(rdir / "trace.jsonl"), rdir, find_pivots, runs)}
    if not by_lab:
        return {**evaluate_unlabelled(event_id, rdir, results_dir, find_pivots, runs), **extra}
    primary_name = sorted(by_lab, key=lambda k: (-len(by_lab[k]), k))[0]
    primary = by_lab[primary_name]
    decisions = read_decisions(rdir / "decisions.jsonl")
    p2g, gbest, grank = read_groups(rdir / "groups.json")
    for pid, d in decisions.items():  # fall back to decision group ids when groups.json lacks a photo
        if pid not in p2g:
            p2g[pid] = d.get("group_id") or f"solo:{pid}"
    trace, run_info = select_run(read_jsonl(rdir / "trace.jsonl"), rdir)
    override = source_override(runs, run_info.get("selected_run") or _only_run(trace))

    pids = sorted(set(primary) & set(decisions))
    counts: dict[str, Counter] = {r: Counter() for r in SCORED_REASONS}
    rej = Counter()
    review_n = 0
    review_human = Counter()
    per_photo = []
    for p in pids:
        h, a = primary[p], decisions[p]
        a_fams = families(a.get("reason_codes") or [])
        av = (a.get("verdict") or "").lower()
        for r in SCORED_REASONS:
            hit_h, hit_a = r in h["reasons"], r in a_fams
            counts[r]["tp" if hit_h and hit_a else "fp" if hit_a else "fn" if hit_h else "tn"] += 1
        hr, ar = h["verdict"] == "reject", av == "reject"
        rej["tp" if hr and ar else "fp" if ar else "fn" if hr else "tn"] += 1
        if av == "review":
            review_n += 1
            review_human[h["verdict"]] += 1
        per_photo.append({"photo_id": p, "human": h["verdict"], "agent": av,
                          "human_reasons": sorted(h["reasons"]), "agent_reasons": sorted(a_fams)})

    per_reason = {r: {**prf(c["tp"], c["fp"], c["fn"]), "tn": c["tn"]} for r, c in counts.items()}
    human_g = {p: primary[p]["group_id_human"] for p in primary}
    out: dict[str, Any] = {
        "event_id": event_id,
        "primary_labeller": primary_name,
        "labellers": {k: len(v) for k, v in by_lab.items()},
        "n_labelled": len(primary),
        "n_decided": len(decisions),
        "n_scored": len(pids),
        "per_reason": per_reason,
        "reject": {**prf(rej["tp"], rej["fp"], rej["fn"]), "tn": rej["tn"]},
        "review": {"n": review_n, "pct": _r(100 * review_n / len(pids), 2) if pids else None,
                   "human_keep": review_human["keep"], "human_reject": review_human["reject"]},
        "clusters": cluster_scores(human_g, p2g),
        "retrieval": retrieval_scores(human_g, p2g),
        "best_of_burst": best_of_burst(primary, p2g, gbest, grank, decisions),
        "labels": True,
        "run_counts": run_counts(decisions),
        "group_shape": group_shape(rdir / "groups.json"),
        "agent_loop": ({**agent_loop_metrics(trace, decisions, primary, _members(rdir), find_pivots, override),
                        **run_info} if trace else None),
        "robustness": robustness(decisions, read_decisions(results_dir / f"{event_id}.run2" / "decisions.jsonl")),
        **extra,
        "_per_photo": per_photo,
        "_group_rows": _group_rows(human_g, p2g),
    }
    # inter-annotator agreement on the overlap (first two labellers by size)
    if len(by_lab) >= 2:
        a_name, b_name = sorted(by_lab, key=lambda k: (-len(by_lab[k]), k))[:2]
        ov = sorted(set(by_lab[a_name]) & set(by_lab[b_name]))
        ka = [by_lab[a_name][p]["verdict"] for p in ov]
        kb = [by_lab[b_name][p]["verdict"] for p in ov]
        per_reason_k = {}
        for r in SCORED_REASONS:
            ra = ["1" if r in by_lab[a_name][p]["reasons"] else "0" for p in ov]
            rb = ["1" if r in by_lab[b_name][p]["reasons"] else "0" for p in ov]
            if "1" in ra or "1" in rb:
                per_reason_k[r] = _r(cohen_kappa(ra, rb))
        ba = ["1" if by_lab[a_name][p]["best"] else "0" for p in ov]
        bb_ = ["1" if by_lab[b_name][p]["best"] else "0" for p in ov]
        out["inter_annotator"] = {"labellers": [a_name, b_name], "n_overlap": len(ov),
                                  "verdict_kappa": _r(cohen_kappa(ka, kb)),
                                  "best_of_group_kappa": _r(cohen_kappa(ba, bb_)) if ov else None,
                                  "verdict_raw_agreement": _r(sum(x == y for x, y in zip(ka, kb)) / len(ov)) if ov else None,
                                  "per_reason_kappa": per_reason_k}
    else:
        out["inter_annotator"] = None
    return out


def select_run(trace: list[dict], rdir: Path) -> tuple[list[dict], dict]:
    """trace.jsonl is append-only per event and may hold several runs. Keep the lines of the run that
    produced this folder's decisions/groups: summary.json's run_id if it appears in the trace, else the
    run with the latest run_header. Returns (lines, info)."""
    ids = {t["run_id"] for t in trace if t.get("run_id")}
    info: dict[str, Any] = {"runs_in_trace": max(1, len(ids)) if trace else 0}
    if len(ids) <= 1:  # lines without a run_id belong to the only run
        return trace, info
    runs: dict[str, list[dict]] = {}
    for t in trace:
        runs.setdefault(str(t.get("run_id")), []).append(t)
    want = None
    sp = rdir / "summary.json"
    if sp.exists():
        sid = json.loads(sp.read_text()).get("run_id")
        want = sid if sid in runs else None
    if want is None:
        hdr = [t for t in trace if t.get("type") == "run_header"]
        want = str(max(hdr, key=lambda t: str(t.get("ts")))["run_id"]) if hdr else max(runs)
    info["selected_run"] = want
    info["other_runs"] = sorted(r for r in runs if r != want)
    return runs[want], info


def _members(rdir: Path) -> dict[str, list[str]]:
    p = rdir / "groups.json"
    if not p.exists():
        return {}
    doc = json.loads(p.read_text())
    return {g["group_id"]: list(g.get("photo_ids") or g.get("members") or []) for g in doc.get("groups") or []
            if isinstance(g, dict) and isinstance(g.get("group_id"), str)}


def evaluate_unlabelled(event_id: str, rdir: Path, results_dir: Path, find_pivots: Any = None,
                        runs: list[dict] | None = None) -> dict:
    """No photographer labels yet: everything that does not need ground truth."""
    decisions = read_decisions(rdir / "decisions.jsonl")
    trace, run_info = select_run(read_jsonl(rdir / "trace.jsonl"), rdir)
    if not decisions and not trace:
        raise SystemExit(f"nothing to evaluate for {event_id}: no labels, decisions.jsonl or trace.jsonl in {rdir}")
    override = source_override(runs, run_info.get("selected_run") or _only_run(trace))
    return {
        "event_id": event_id, "labels": False, "n_labelled": 0, "n_decided": len(decisions), "n_scored": 0,
        "run_counts": run_counts(decisions),
        "group_shape": group_shape(rdir / "groups.json"),
        "agent_loop": ({**agent_loop_metrics(trace, decisions, {}, _members(rdir), find_pivots, override),
                        **run_info} if trace else None),
        "robustness": robustness(decisions, read_decisions(results_dir / f"{event_id}.run2" / "decisions.jsonl")),
        "inter_annotator": None,
        "_per_photo": [], "_group_rows": [],
    }


def _group_rows(human: dict[str, str], agent: dict[str, str]) -> list[dict]:
    rows = []
    hm: dict[str, list[str]] = defaultdict(list)
    for p, g in human.items():
        if p in agent:
            hm[g].append(p)
    for g, ms in sorted(hm.items()):
        if len(ms) < 2:
            continue
        ag = Counter(agent[p] for p in ms)
        top, k = ag.most_common(1)[0]
        rows.append({"group_id_human": g, "size": len(ms), "n_agent_groups": len(ag), "best_agent_group": top,
                     "best_overlap": k, "complete": int(len(ag) == 1)})
    return rows


def aggregate(events: list[dict]) -> dict:
    """Micro-average counts across labelled events; macro means for rates."""
    all_events = events
    events = [e for e in all_events if e.get("labels")]
    agg: dict[str, Any] = {"events": [e["event_id"] for e in all_events],
                           "labelled_events": [e["event_id"] for e in events]}
    if not events:
        agg["n_scored"] = 0
        agg["no_labels"] = True
        rob = [e["robustness"] for e in all_events if e.get("robustness")]
        if rob:
            n = sum(r["n"] for r in rob)
            agg["robustness"] = {"n": n, "identical_verdict_pct":
                                 _r(sum(r["identical_verdict_pct"] * r["n"] for r in rob) / n, 2)}
        return agg
    pr = {}
    for r in SCORED_REASONS:
        c = Counter()
        for e in events:
            for k in ("tp", "fp", "fn", "tn"):
                c[k] += e["per_reason"][r][k]
        pr[r] = {**prf(c["tp"], c["fp"], c["fn"]), "tn": c["tn"]}
    agg["per_reason"] = pr
    c = Counter()
    for e in events:
        for k in ("tp", "fp", "fn", "tn"):
            c[k] += e["reject"][k]
    agg["reject"] = {**prf(c["tp"], c["fp"], c["fn"]), "tn": c["tn"]}
    n_scored = sum(e["n_scored"] for e in events)
    rv = sum(e["review"]["n"] for e in events)
    agg["review"] = {"n": rv, "pct": _r(100 * rv / n_scored, 2) if n_scored else None}
    agg["n_scored"] = n_scored

    def wmean(path: tuple[str, str], weight: tuple[str, str]) -> float | None:
        vals = [(e[path[0]][path[1]], e[weight[0]][weight[1]]) for e in events
                if e.get(path[0]) and e[path[0]].get(path[1]) is not None and e[weight[0]].get(weight[1])]
        tot = sum(w for _, w in vals)
        return _r(sum(v * w for v, w in vals) / tot) if tot else None

    agg["clusters"] = {"purity": wmean(("clusters", "purity"), ("clusters", "n")),
                       "inverse_purity": wmean(("clusters", "inverse_purity"), ("clusters", "n")),
                       "completeness": wmean(("clusters", "completeness"), ("clusters", "n_human_groups_multi"))}
    agg["retrieval"] = {k: wmean(("retrieval", k), ("retrieval", "n_queries_with_gt")) for k in ("precision", "recall", "map")}
    agg["best_of_burst"] = {k: wmean(("best_of_burst", k), ("best_of_burst", "n_groups"))
                            for k in ("top1_agreement", "top2_agreement")}
    agg["best_of_burst"]["n_groups"] = sum(e["best_of_burst"]["n_groups"] for e in events)
    ia = [e["inter_annotator"] for e in events if e.get("inter_annotator")]
    if ia:
        agg["inter_annotator"] = {"n_overlap": sum(i["n_overlap"] for i in ia),
                                  "verdict_kappa_by_event": {e["event_id"]: e["inter_annotator"]["verdict_kappa"]
                                                             for e in events if e.get("inter_annotator")},
                                  "best_of_group_kappa_by_event": {
                                      e["event_id"]: e["inter_annotator"]["best_of_group_kappa"]
                                      for e in events if e.get("inter_annotator")}}
    rob = [e["robustness"] for e in all_events if e.get("robustness")]
    if rob:
        n = sum(r["n"] for r in rob)
        agg["robustness"] = {"n": n, "identical_verdict_pct": _r(sum(r["identical_verdict_pct"] * r["n"] for r in rob) / n, 2)}
    return agg


# --------------------------------------------------------------------------- render
def f3(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


NO_LABELS = NO_PHOTOGRAPHER


def _fmt(x: Any) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.2f}"
    return str(x)


def _kv(d: dict | None) -> str:
    return ", ".join(f"{k} {v}" for k, v in (d or {}).items()) or "none"


def _label_line(e: dict) -> str:
    lc = e.get("label_counts") or {}
    ph = (lc.get("photographer") or {}).get("rows", 0)
    tm = (lc.get("team") or {}).get("rows", 0)
    un = (lc.get("unclassified") or {}).get("rows", 0)
    return f"photographer {ph}, team {tm} (not photographer), unclassified {un}"


def render_md(report: dict) -> str:
    a = report["aggregate"]
    ev = report["events"]
    labelled = bool(a.get("labelled_events"))
    n_ph = sum(((e.get("label_counts") or {}).get("photographer") or {}).get("rows", 0) for e in ev)
    L = ["# Evaluation metrics", "",
         f"Generated {report['generated_at']} by `eval/compute_metrics.py` from {len(ev)} event(s). "
         f"Photographer-labelled photos: {n_ph}. Definitions: docs/EVALUATION.md and the compute_metrics.py "
         "docstring. Events are E-index aliases; run ids are `<event>-<kind>-<UTC timestamp>`.", "",
         "Honesty rules applied: only `labeller=photographer` rows are ground truth. Team labels are reported "
         "separately as \"team labels (not photographer)\". Answers from the scripted reviewer and the team's "
         "mechanism tests (`lead_test`) are counted but never enter a human-agreement figure.", "",
         "## Label status", "", "| event | label rows by class |", "|---|---|"]
    L += [f"| {e['event_id']} | {_label_line(e)} |" for e in ev]
    L.append("")
    if labelled:
        L += ["## Per-reason (micro-averaged over photographer-labelled events)", "",
              "| reason | support (n positives) | TP | FP | FN | precision | recall | F1 |",
              "|---|---|---|---|---|---|---|---|"]
        for r, m in a["per_reason"].items():
            L.append(f"| {r} | {m['support']} | {m['tp']} | {m['fp']} | {m['fn']} | {f3(m['precision'])} | "
                     f"{f3(m['recall'])} | {f3(m['f1'])} |")
        rj = a["reject"]
        L += ["", "## Overall reject", "",
              f"precision {f3(rj['precision'])}, recall {f3(rj['recall'])}, F1 {f3(rj['f1'])} "
              f"(TP {rj['tp']}, FP {rj['fp']}, FN {rj['fn']}, TN {rj['tn']}). Agent `review` counted as not-reject; "
              f"{a['review']['n']} photos ({a['review']['pct']}%) were routed to review.", "",
              "## Duplicate clusters and retrieval", "",
              f"purity {f3(a['clusters']['purity'])}, inverse purity {f3(a['clusters']['inverse_purity'])}, "
              f"completeness {f3(a['clusters']['completeness'])}; retrieval precision "
              f"{f3(a['retrieval']['precision'])}, recall {f3(a['retrieval']['recall'])}, "
              f"MAP {f3(a['retrieval']['map'])}.", "",
              "## Best of burst", "",
              f"{a['best_of_burst']['n_groups']} human groups with one marked best: top-1 agreement "
              f"{f3(a['best_of_burst']['top1_agreement'])}, top-2 {f3(a['best_of_burst']['top2_agreement'])}.", ""]
        ia = a.get("inter_annotator")
        L += ["## Inter-annotator agreement (Cohen's kappa, photographers)", "",
              (f"verdict kappa by event: {_kv(ia['verdict_kappa_by_event'])} on {ia['n_overlap']} overlap photos"
               if ia else NO_PHOTOGRAPHER + " for a second photographer (overlap n=0)."), ""]
    else:
        L += ["## Per-reason precision / recall / F1", "",
              "| reason | support (n positives) | precision | recall | F1 |", "|---|---|---|---|---|"]
        L += [f"| {r} | {NO_PHOTOGRAPHER} | {NO_PHOTOGRAPHER} | {NO_PHOTOGRAPHER} | {NO_PHOTOGRAPHER} |"
              for r in SCORED_REASONS]
        L.append("")
        for title in ("Overall reject precision / recall", "Duplicate clusters vs human groups (purity / completeness)",
                      "Best of burst (top-1 / top-2)", "Inter-annotator agreement (Cohen's kappa)",
                      "Escalated photos: human agreed with the agent's proposal"):
            L += [f"## {title}", "", NO_PHOTOGRAPHER + ".", ""]
    tl = [e for e in ev if e.get("team_labels")]
    L += ["## Team labels (not photographer)", ""]
    if tl:
        L += ["Labels by team members, NOT a photographer. Reported for transparency only; never used as ground "
              "truth. Kappa on keep/reject; worker/agent `review` counts as keep.", "",
              "| event | team rows | kappa vs worker (n) | kappa vs final verdicts (n) | kappa vs agent-decided rows (n) "
              "| kappa vs photographer (n) |", "|---|---|---|---|---|---|"]
        for e in tl:
            t = e["team_labels"]
            L.append(f"| {e['event_id']} | {t['n_rows']} | " + " | ".join(
                f"{f3(t[k]['kappa'])} ({t[k]['n']})" for k in ("vs_worker", "vs_final", "vs_agent_rows",
                                                                "vs_photographer")) + " |")
    else:
        L.append("No team labels yet (n=0). `eval/sample_for_labelling.py --labeller team` prepares them.")
    L.append("")

    L += ["## Agent runs (label-independent, every run in each event's trace)", "",
          "Pivots use the API's own rule set (`services/api/app/pivots.py`), so they match `GET /runs/{id}/trace` "
          "with limit=500. Active s = agent working time from trace timestamps (time waiting for an answer "
          "excluded). Answer sources: `pending` = escalated, not answered; `scripted` = auto-approver; "
          "`lead_test` = the team's mechanism tests; `ui`/`file`/`http` = a person.", "",
          "| event | run | host | status | tool calls | model calls | pauses / resumes | escalated photos | "
          "answer sources | pivots (by policy) | active s | tool p50 / p95 ms |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in ev:
        for r in e.get("agent_runs") or []:
            mark = " (results)" if r["is_results_run"] else ""
            L.append(f"| {e['event_id']} | {r['run_id']}{mark} | {_fmt(r['host'])} | {r['status']} | {r['tool_calls']} | "
                     f"{_fmt(r['model_calls'])} | {r['pauses']} / {r['resumes']} | {r['photos_escalated']} | "
                     f"{_kv(r['answer_sources'])} | {_fmt(r['pivots'])} ({_kv(r['pivots_by_policy'])}) | "
                     f"{_fmt(r['agent_active_s'])} | {_fmt(r['tool_latency_ms_p50'])} / "
                     f"{_fmt(r['tool_latency_ms_p95'])} |")
    tot = [r for e in ev for r in e.get("agent_runs") or []]
    pol: Counter = Counter()
    for r in tot:
        pol.update(r.get("pivots_by_policy") or {})
    L += ["", f"Totals over {len(tot)} agent runs: {sum(r['tool_calls'] for r in tot)} tool calls, "
          f"{sum(r['pauses'] for r in tot)} pauses, {sum(r['resumes'] for r in tot)} resumes, "
          f"{sum(r['pivots'] or 0 for r in tot)} pivots ({_kv(dict(sorted(pol.items())))}). Model tokens are not "
          "recorded in trace.jsonl; tokens and $ come from the determinism runs below.", ""]

    L += ["## Run counts (decisions.jsonl) and group shape (groups.json)", "",
          "| event | photos | keep / reject / review | decided by | answer source on rows | needs approval | groups | "
          "singletons | group-size histogram | ties | margin < 0.05 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in ev:
        rc, gs = e.get("run_counts") or {}, e.get("group_shape") or {}
        v = rc.get("verdicts") or {}
        L.append(f"| {e['event_id']} | {rc.get('n_photos')} | {v.get('keep', 0)} / {v.get('reject', 0)} / "
                 f"{v.get('review', 0)} | {_kv(rc.get('decided_by'))} | {_kv(rc.get('human_source'))} | "
                 f"{rc.get('needs_approval')} | {gs.get('n_groups', 'n/a')} | {gs.get('n_singletons', 'n/a')} | "
                 f"{_kv(gs.get('size_histogram'))} | {gs.get('ties', 'n/a')} | {gs.get('margin_lt_0_05', 'n/a')} |")
    L += ["", "Figures: `verdicts_by_event.png`, `group_sizes.png`, `confusion_<REASON>.png` (empty until labels).", ""]

    det = report.get("determinism")
    rb = a.get("robustness")
    L += ["## Robustness: agent determinism", ""]
    if det:
        reps = det["per_rep"]
        L += [f"{det['event']} run twice with {det['model']} (approvals: {det['approvals']}): "
              f"**{_fmt(det['identical_verdict_pct'])} % identical final verdicts** over "
              f"{det['photos_decided_in_both']} agent-loop photos; identical reason sets {_fmt(det['identical_reasons_pct'])} %; "
              f"identical proposals on photos escalated in both {_fmt(det['identical_proposal_pct'])} %; "
              f"same tool-call sequence: {det['tool_sequence_identical']}. Source: `eval/results/determinism.md`.", "",
              "| run | tool calls | model cycles | input tokens | output tokens | cycles with 0 output tokens | "
              "$ at list price |", "|---|---|---|---|---|---|---|"]
        for p in reps:
            t = p["tokens"]
            L.append(f"| {p['rep']} | {_fmt(p['tool_calls'])} | {t['cycles']} | {t['input_tokens']:,} | "
                     f"{t['output_tokens']:,} | {t['cycles_zero_output']} | {_fmt(p['usd_list_price'])} |")
        L += ["", f"$ per event (mean of the two runs, Anthropic list price, no prompt caching): "
              f"{_fmt(det['usd_per_event_mean'])}. The endpoint reported 0 output tokens on "
              f"{sum(p['tokens']['cycles_zero_output'] for p in reps)} of {sum(p['tokens']['cycles'] for p in reps)} "
              "cycles (tool-only turns), so output tokens and $ are lower bounds; input tokens dominate the cost.", ""]
    elif rb:
        L += [f"{rb['identical_verdict_pct']}% identical verdicts over {rb['n']} photos on re-run.", ""]
    else:
        L += ["No second run yet (eval/agent_determinism.py).", ""]
    if labelled:
        L += ["## Per photographer-labelled event", "", "| event | labelled | scored | reject P | reject R | purity | "
              "completeness | best top-1 | kappa (overlap) |", "|---|---|---|---|---|---|---|---|---|"]
        for e in ev:
            if not e.get("labels"):
                continue
            ia = e.get("inter_annotator")
            L.append(f"| {e['event_id']} | {e['n_labelled']} | {e['n_scored']} | {f3(e['reject']['precision'])} | "
                     f"{f3(e['reject']['recall'])} | {f3(e['clusters']['purity'])} | "
                     f"{f3(e['clusters']['completeness'])} | {f3(e['best_of_burst']['top1_agreement'])} | "
                     f"{f3(ia['verdict_kappa']) + ' (' + str(ia['n_overlap']) + ')' if ia else 'n/a'} |")
        L.append("")
    return "\n".join(L)


AGENT_CSV_COLS = ["event_id", "run_id", "is_results_run", "host", "run_kind", "status", "tool_calls",
                  "tool_calls_excl_next_work_item", "model_calls", "pauses", "resumes", "approval_items",
                  "photos_escalated", "answer_sources", "pivots", "pivots_by_policy", "agent_active_s",
                  "agent_wall_s_run_item", "tool_results_not_ok", "tool_latency_ms_p50", "tool_latency_ms_p95",
                  "model_tokens", "escalation_human_agreed_pct", "photographer_label_rows", "team_label_rows",
                  "input_tokens", "output_tokens", "cycles_zero_output", "usd_list_price"]


def agent_csv_rows(per_event: list[dict], det: dict | None) -> list[dict]:
    rows = []
    for e in per_event:
        lc = e.get("label_counts") or {}
        ph = (lc.get("photographer") or {}).get("rows", 0)
        for r in e.get("agent_runs") or []:
            rows.append({**r, "event_id": e["event_id"],
                         "answer_sources": _kv(r["answer_sources"]), "pivots_by_policy": _kv(r["pivots_by_policy"]),
                         "escalation_human_agreed_pct": NO_PHOTOGRAPHER if not ph else
                         (e.get("agent_loop") or {}).get("escalation_human_agreed_pct"),
                         "photographer_label_rows": ph, "team_label_rows": (lc.get("team") or {}).get("rows", 0),
                         "model_tokens": "not recorded in trace"})
    for p in (det or {}).get("per_rep") or []:
        t = p["tokens"]
        rows.append({"event_id": det["event"], "run_id": f"{det['event']}-det-{p['rep']}", "is_results_run": False,
                     "host": "local (anthropic)", "run_kind": "determinism", "status": p["status"],
                     "tool_calls": p["tool_calls"], "model_calls": p["model_calls"], "pauses": p["pauses"],
                     "photos_escalated": p["photos_escalated"], "answer_sources": "scripted",
                     "escalation_human_agreed_pct": "n/a (scripted approvals)", "input_tokens": t["input_tokens"],
                     "output_tokens": t["output_tokens"], "cycles_zero_output": t["cycles_zero_output"],
                     "usd_list_price": p["usd_list_price"], "model_tokens": t["input_tokens"] + t["output_tokens"],
                     "agent_active_s": p["wall_s"]})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", nargs="*", default=None,
                    help="event ids or E-aliases (default: every eval/labels/*.csv + every results/<eventId>/)")
    ap.add_argument("--labels-dir", type=Path, default=HERE / "labels")
    ap.add_argument("--results-dir", type=Path, default=HERE / "results" / "raw" / "mirror" / "results",
                    help="local mirror of s3://<WORK_BUCKET>/results/ (gitignored)")
    ap.add_argument("--out-dir", type=Path, default=HERE / "results")
    ap.add_argument("--fetch", action="store_true", help="download results/<event>/ from $WORK_BUCKET first")
    ap.add_argument("--runs-file", type=Path, default=None,
                    help="read-only export of RUN# items {eventId: [items]} (default: <results-dir>/../runs.json)")
    ap.add_argument("--determinism", type=Path, default=HERE / "results" / "determinism.json")
    ap.add_argument("--pivots-module", type=Path, default=None,
                    help="path to services/api/app/pivots.py (default: the repo copy, if present)")
    ap.add_argument("--raw-ids", action="store_true", help="keep raw ids (local debugging; never commit)")
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args()

    import sys

    sys.path.insert(0, str(HERE))
    from events import load_aliases

    aliases = load_aliases()
    labelled = {p.name.split(".")[0] for p in args.labels_dir.glob("*.csv") if ".todo" not in p.name}
    with_results = {p.name for p in args.results_dir.glob("*") if p.is_dir() and "." not in p.name
                    and (p / "decisions.jsonl").exists()} if args.results_dir.exists() else set()
    events = [aliases.get(e, e) for e in args.events] if args.events else sorted(labelled | with_results)
    if not events:
        raise SystemExit(f"no label CSVs in {args.labels_dir} and no results/<eventId>/ in {args.results_dir}")
    find_pivots = load_pivot_finder(args.pivots_module)
    if find_pivots is None:
        print("note: services/api/app/pivots.py not found; pivots reported as n/a (pass --pivots-module)")
    if args.fetch:
        import os

        import boto3

        s3 = boto3.client("s3")
        bucket = os.environ["WORK_BUCKET"]
        for ev in events:
            for name in ("decisions.jsonl", "groups.json", "trace.jsonl", "summary.json", "agent_summary.json"):
                dst = args.results_dir / ev / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    s3.download_file(bucket, f"results/{ev}/{name}", str(dst))
                except Exception as exc:  # noqa: BLE001 - optional files
                    print(f"skip {name}: {type(exc).__name__}")
    runs_path = args.runs_file or args.results_dir.parent / "runs.json"
    runs_doc = json.loads(runs_path.read_text()) if runs_path.exists() else {}
    if not runs_doc:
        print("note: no RUN# export (--runs-file); answer sources come from trace.jsonl only")
    import datetime as dt

    per_event = [evaluate_event(ev, args.labels_dir, args.results_dir, find_pivots, runs_doc.get(ev)) for ev in events]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw = args.out_dir / "raw"
    raw.mkdir(exist_ok=True)
    ali = Aliaser({} if args.raw_ids else aliases)
    if not args.raw_ids:
        per_event = [ali.scrub({**e, "event_id": e["event_id"]}, e["event_id"], _run_ids(e)) | {
            "event_id": ali.event(e["event_id"]), "_per_photo": e["_per_photo"], "_group_rows": e["_group_rows"]}
            for e in per_event]
    # groups_purity.csv: gitignored (agent group ids); per-photo pairs feed make_confusions.py from raw/
    with (args.out_dir / "groups_purity.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["event_id", "group_id_human", "size", "n_agent_groups",
                                          "best_agent_group", "best_overlap", "complete"])
        w.writeheader()
        for e in per_event:
            for r in e["_group_rows"]:
                w.writerow({"event_id": e["event_id"], **r})
        if not any(e["_group_rows"] for e in per_event):
            w.writerow({"event_id": "all", "group_id_human": NO_PHOTOGRAPHER})
    confusions = {e["event_id"]: e.pop("_per_photo") for e in per_event}
    for e in per_event:
        e.pop("_group_rows")
    det = json.loads(args.determinism.read_text()) if args.determinism.exists() else None
    report = {"generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
              "events": per_event, "aggregate": aggregate(per_event), "determinism": det}
    blob = json.dumps(report, indent=2) + "\n"
    md = render_md(report)
    rows = agent_csv_rows(per_event, det)
    import io

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=AGENT_CSV_COLS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    if not args.raw_ids:
        for text, where in ((blob, "metrics.json"), (md, "metrics.md"), (buf.getvalue(), "agent_metrics.csv")):
            assert_no_ids(text, where)
    (args.out_dir / "metrics.json").write_text(blob)
    (args.out_dir / "metrics.md").write_text(md)
    (args.out_dir / "agent_metrics.csv").write_text(buf.getvalue())
    (raw / "per_photo.json").write_text(json.dumps(confusions) + "\n")
    if not args.no_plots:
        import importlib.util

        spec = importlib.util.spec_from_file_location("make_confusions", HERE / "make_confusions.py")
        if spec and spec.loader:
            mc = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mc)
            for pth in mc.render_all(report, args.out_dir):
                print(pth)
    print(f"wrote {args.out_dir / 'metrics.json'}, metrics.md, groups_purity.csv, agent_metrics.csv")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

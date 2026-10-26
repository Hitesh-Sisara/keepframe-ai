// Display-only derivations for the Overview dashboard and the run timeline. Pure functions over the API's
// own fields (lib/runs RunX, TracePage), so they are unit-tested and never invent a number.
import type { RunX } from "./runs.ts";
import type { Pivot, TraceLine } from "./types.ts";

export type StageKey = "sync" | "score" | "agent" | "waiting" | "done";

export interface Stage {
  key: StageKey;
  label: string;
  /** Seconds spent in the stage; undefined when the run's stamps can't tell. "done" never has one. */
  s?: number;
  state: "done" | "active" | "todo";
  hint: string;
}

const LABEL: Record<StageKey, string> = { sync: "Ingest", score: "Score", agent: "Agent", waiting: "Waiting", done: "Done" };
const HINT: Record<StageKey, string> = {
  sync: "Uploaded photos read from S3 and handed to the worker",
  score: "Every frame and face scored with OpenCV",
  agent: "The agent working through close calls",
  waiting: "Paused on close calls until each was answered",
  done: "Verdicts saved",
};

function t(iso?: string): number | undefined {
  if (!iso) return undefined;
  const v = Date.parse(iso);
  return Number.isNaN(v) ? undefined : v;
}

const pos = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : undefined);

/**
 * Ingest → Score → Agent → Waiting → Done, with real durations from the run's stamps:
 *   sync    = start → agent start, minus the worker's own wall time (listing, queueing, hand-off)
 *   score   = worker_wall_s
 *   agent   = agent_wall_s (time the agent itself spent working)
 *   waiting = agent start → agent end, minus agent_wall_s (paused on people)
 * For a finished run the four add up to the run's duration. A live run marks the stage it is in.
 */
export function runStages(run: Partial<RunX> & { worker_wall_s?: number; agent_wall_s?: number }, now = Date.now()): Stage[] {
  const start = t(run.started_at);
  const aStart = t(run.agent_started_at);
  const aEnd = t(run.agent_ended_at) ?? t(run.ended_at);
  const worker = pos(run.worker_wall_s) ?? pos(run.wall_s);
  const agentWork = pos(run.agent_wall_s);
  const live = ["starting", "running", "worker_done", "awaiting_approval"].includes(run.status ?? "");
  const failed = run.status === "failed" || run.status === "cancelled";

  const handoff = start !== undefined && aStart !== undefined ? Math.max(0, (aStart - start) / 1000) : undefined;
  const sync = handoff !== undefined && worker !== undefined ? Math.max(0, handoff - worker) : undefined;
  const agentSpan = aStart !== undefined && aEnd !== undefined && !live ? Math.max(0, (aEnd - aStart) / 1000) : aStart !== undefined && live ? Math.max(0, (now - aStart) / 1000) : undefined;
  const waiting = agentSpan !== undefined && agentWork !== undefined ? Math.max(0, agentSpan - agentWork) : undefined;
  const agent = agentWork ?? (waiting === undefined ? agentSpan : undefined);

  // Which stage a live run is in.
  let at: StageKey | null = null;
  if (live) {
    if (run.status === "starting") at = "sync";
    else if (run.status === "awaiting_approval") at = "waiting";
    else if (aStart !== undefined || run.status === "worker_done") at = "agent";
    else at = "score";
  }
  const order: StageKey[] = ["sync", "score", "agent", "waiting", "done"];
  const atIdx = at ? order.indexOf(at) : failed ? -1 : order.length;
  // A run paused on a person keeps waiting until it is answered: count to now (agent start → now, minus the
  // agent's own work; else from the pause stamp), never a stale 0.
  const pausedAt = t(run.paused_at);
  const openWait =
    run.status === "awaiting_approval"
      ? aStart !== undefined && agentWork !== undefined
        ? Math.max(0, (now - aStart) / 1000 - agentWork)
        : pausedAt !== undefined
          ? Math.max(0, (now - pausedAt) / 1000)
          : undefined
      : undefined;
  const secs: Record<StageKey, number | undefined> = { sync, score: worker, agent, waiting: live ? openWait : waiting, done: undefined };

  return order.map((key, i) => ({
    key,
    label: LABEL[key],
    hint: HINT[key],
    s: i < atIdx || (!live && key !== "done") || (key === "waiting" && at === "waiting") ? secs[key] : undefined,
    state: live ? (i < atIdx ? "done" : i === atIdx ? "active" : "todo") : failed ? (secs[key] !== undefined ? "done" : "todo") : "done",
  }));
}

/** Seconds of machine time (scoring + agent work), the part that scales with photos. */
export function computeSeconds(run: { worker_wall_s?: number; agent_wall_s?: number; wall_s?: number }): number | undefined {
  const w = pos(run.worker_wall_s) ?? pos(run.wall_s);
  const a = pos(run.agent_wall_s);
  if (w === undefined && a === undefined) return undefined;
  return (w ?? 0) + (a ?? 0);
}

export interface PivotExample {
  pivot: Pivot;
  groupId?: string;
}

/** Group each trace line belongs to: the latest next_work_item result (or explicit group_id arg) before it. */
export function groupBySeq(lines: TraceLine[]): Map<number, string> {
  const out = new Map<number, string>();
  let cur: string | undefined;
  for (const l of [...lines].sort((a, b) => a.seq - b.seq)) {
    const r = l.result as { group_id?: unknown } | undefined;
    const a = l.args as { group_id?: unknown } | undefined;
    if (l.type === "tool_result" && l.tool === "next_work_item" && typeof r?.group_id === "string") cur = r.group_id;
    else if (typeof a?.group_id === "string") cur = a.group_id;
    if (cur) out.set(l.seq, cur);
  }
  return out;
}

/** Up to `n` plan changes, one per kind first (so the examples show different pivots), each with its group. */
export function pivotExamples(pivots: Pivot[], lines: TraceLine[], n = 2): PivotExample[] {
  const groups = groupBySeq(lines);
  const seen = new Set<string>();
  const first: Pivot[] = [];
  const rest: Pivot[] = [];
  // Prefer the kinds that read best as a story: re-scoring on the original, then eyes, then the human hand-off.
  const rank: Record<string, number> = { sharpness_rescore: 0, eyes_compare_in_group: 1, small_margin_to_human: 2 };
  const sorted = [...pivots].sort((a, b) => (rank[a.kind] ?? 9) - (rank[b.kind] ?? 9) || a.result_seq - b.result_seq);
  for (const p of sorted) (seen.has(p.kind) ? rest : (seen.add(p.kind), first)).push(p);
  return [...first, ...rest].slice(0, n).map((pivot) => ({ pivot, groupId: groups.get(pivot.call_seq) ?? groups.get(pivot.result_seq) }));
}

/** Tool-call counts by tool, largest first. */
export function toolMix(lines: TraceLine[]): { tool: string; n: number }[] {
  const m = new Map<string, number>();
  for (const l of lines) if (l.type === "tool_call" && l.tool) m.set(l.tool, (m.get(l.tool) ?? 0) + 1);
  return [...m.entries()].map(([tool, n]) => ({ tool, n })).sort((a, b) => b.n - a.n || a.tool.localeCompare(b.tool));
}

const TOOL_WORD: Record<string, string> = {
  score_sharpness: "Sharpness",
  score_exposure: "Exposure",
  detect_faces: "Face detection",
  score_eyes_open: "Eyes open",
  score_face_quality: "Face quality",
  pick_best_in_cluster: "Rank burst",
  make_contact_sheet: "Contact sheet",
  request_human_approval: "Ask a person",
  write_decision: "Save verdict",
  next_work_item: "Next burst",
};

export function toolWord(tool: string): string {
  return TOOL_WORD[tool] ?? tool.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

const KIND_WORD: Record<string, string> = {
  sharpness_rescore: "Re-scored on the original",
  eyes_compare_in_group: "Closed eyes: compared in the burst",
  small_margin_to_human: "Too close to call: asked you",
};

export function pivotTitle(kind: string): string {
  return KIND_WORD[kind] ?? "Changed its plan";
}

export function fmtTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

export function fmtClock(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString().slice(11, 23);
}

export function fmtNum(v: unknown, digits = 2): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  if (Math.abs(v) >= 1000) return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
  return Number.isInteger(v) ? String(v) : v.toFixed(digits);
}

export function pct(v?: number): string {
  return typeof v === "number" ? `${Math.round(v * 100)}%` : "—";
}

export const STATUS_LABEL: Record<string, string> = {
  starting: "Starting",
  running: "Culling",
  awaiting_approval: "Waiting for you",
  succeeded: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** Significant-figure format that rounds half up despite binary floats (0.001055 → "0.00106"). */
export function fmtSig(v: unknown, sig = 3): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  if (v === 0) return "0";
  const nudge = Math.abs(v) * 1e-9;
  return String(Number((v + Math.sign(v) * nudge).toPrecision(sig)));
}

/** One decimal, truncated, so 99.95 % reads 99.9 % rather than an impossible-looking 100 %. */
export function fmtPct1(v: unknown): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return (Math.floor(v * 10) / 10).toFixed(1);
}

export function framesText(group: { size: number; span_s?: number }): string {
  const n = `${group.size} ${group.size === 1 ? "frame" : "frames"}`;
  if (typeof group.span_s !== "number") return n;
  // Frames with identical capture times (no sub-second EXIF) read better as "same second" than "over 0.0 s".
  return group.span_s < 0.05 ? `${n}, same second` : `${n} over ${group.span_s.toFixed(1)} s`;
}

/** "N flagged by the worker, M asked by the agent" from the split counts; old single-count runs fall back to
 *  escalation_count ("N policy escalations"). The review count is appended when known. */
export function escalationText(run: {
  escalations?: { worker?: number; agent?: number };
  escalation_count?: number;
  verdict_counts?: { review?: number };
}): string {
  const parts: string[] = [];
  const e = run.escalations;
  if (e && (typeof e.worker === "number" || typeof e.agent === "number")) {
    if (typeof e.worker === "number") parts.push(`${e.worker} flagged by the worker`);
    if (typeof e.agent === "number") parts.push(`${e.agent} asked by the agent`);
  } else if (typeof run.escalation_count === "number" && run.escalation_count > 0) {
    const n = run.escalation_count;
    parts.push(`${n} policy ${n === 1 ? "escalation" : "escalations"}`);
  }
  const review = run.verdict_counts?.review;
  if (typeof review === "number") parts.push(`${review} sent to review`);
  return parts.length ? `, ${parts.join(", ")}` : "";
}

/** ", 1 keep, 1 reject" for accessible names (zero counts omitted). */
export function tallyText(c?: { keep?: number; reject?: number; review?: number }): string {
  if (!c) return "";
  const parts = (["keep", "review", "reject"] as const).filter((k) => (c[k] ?? 0) > 0).map((k) => `${c[k]} ${k}`);
  return parts.length ? `, ${parts.join(", ")}` : "";
}

export function fmtSeconds(s?: number): string {
  if (typeof s !== "number" || !Number.isFinite(s) || s < 0) return "";
  if (s < 60) return `${s.toFixed(0)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}

/** Time clause for a run: "took 59 s" when finished with a valid duration; "waiting for approval since
 *  17:11" / "running since 17:10" while active; nothing when the stamps are missing or inconsistent. */
export function runTimeText(
  run: { status?: string; started_at?: string; duration_s?: number; paused_at?: string },
  clock: (iso?: string) => string,
): string {
  if (typeof run.duration_s === "number" && run.duration_s > 0) return `took ${fmtSeconds(run.duration_s)}`;
  if (run.status === "awaiting_approval") return run.paused_at ? `waiting for approval since ${clock(run.paused_at)}` : "waiting for approval";
  if (run.status === "running" || run.status === "starting") return run.started_at ? `running since ${clock(run.started_at)}` : "running";
  return "";
}

export const FRAME_CAP = 24;
export const TOP_N = 12;

/** Frames shown on a large group before "show all": the keeper(s), every frame sent to review, the top TOP_N
 *  by rank, and the selected frame, in rank order. Groups at or below FRAME_CAP show everything. */
export function framesToShow<T extends { photo_id: string; is_best?: boolean; verdict?: string; rank?: number }>(
  members: T[],
  selected?: string,
): T[] {
  if (members.length <= FRAME_CAP) return members;
  const ranked = members.map((m, i) => ({ m, r: typeof m.rank === "number" ? m.rank : 10_000 + i }));
  ranked.sort((a, b) => a.r - b.r);
  const top = new Set(ranked.slice(0, TOP_N).map((x) => x.m.photo_id));
  return ranked
    .filter(({ m }) => m.is_best || m.verdict === "keep" || m.verdict === "review" || top.has(m.photo_id) || m.photo_id === selected)
    .map((x) => x.m);
}

const AGENT_HOST_WORD: Record<string, string> = {
  agentcore: "AgentCore Runtime",
  fargate: "Fargate task", // older runs, before the Fargate agent was removed
  local: "local",
};

/** Where the agent pass ran, only from what the run says: `agent_host` (stamped by the agent), else a
 *  `run_kind` that names a host exactly. "deployed" says nothing about the agent host, so it shows nothing. */
export function agentHostText(run: { agent_host?: string; run_kind?: string }): string {
  for (const v of [run.agent_host, run.run_kind]) {
    if (v && AGENT_HOST_WORD[v]) return AGENT_HOST_WORD[v];
  }
  return run.agent_host ?? "";
}

/** Live agent state for the run banner, from GET /runs/{id}/agent. Empty when there is nothing live to say. */
export function agentLiveText(runStatus: string | undefined, live?: { status?: string } | null): string {
  if (!live?.status) return "";
  const s = live.status;
  if (s === "running" && runStatus === "awaiting_approval") return "resuming…";
  if (s === "running") return "running";
  if (s === "paused") return "paused, waiting for a person";
  if (s === "done") return "finished";
  if (s === "error") return "stopped with an error";
  if (s === "unreachable") return "not reachable";
  return "";
}

/** Approval toast after POST /approvals/{id}: what the resume actually did. */
export function resumeResultText(resume?: { via?: string; status?: string | number }): string {
  if (resume?.via === "api-direct") return "Verdicts recorded.";
  if (resume?.via === "agentcore") {
    if (resume.status === "paused") return "The agent resumed and stopped at the next close call.";
    if (resume.status === "done") return "The agent resumed and finished the run.";
  }
  return "The agent is resuming.";
}

// Display-only scrub (local promo build): readable labels in place of storage ids and paths.
export function cleanText(s: string): string {
  return s
    .replace(/\bsheets\/[^\s"',]+/g, "contact sheet")
    .replace(/\bg_[A-Za-z0-9-]+_(\d+)\b/g, "Group $1")
    .replace(/\brun_[0-9A-Z]{20,}\b/g, "this run")
    .replace(/(?:\/[^\s/"']+)+\/(site-packages\/cv2\/[^\s"']+)/g, "…/$1")
    .replace(/\bfake-planner-v1\b/g, "scripted planner")
    .replace(/\bfake-reasons-v1\b/g, "scripted reasons");
}

/** "Run 26 Oct, 00:16" for a run row (the ULID stays in the URL only). */
export function runLabel(run: { started_at?: string; run_id: string }): string {
  return run.started_at ? `Run ${fmtTime(run.started_at)}` : "Run";
}

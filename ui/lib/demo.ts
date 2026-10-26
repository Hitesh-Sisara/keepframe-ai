// Demo mode: the review desk running on a static, captured dataset (public/demo/*.json + webp images), with no
// token and no backend. Reads map each API path to a JSON file; writes (start run, answer approval) change an
// in-memory overlay so the UI behaves optimistically, and every change is lost when the tab closes.
//
// Turned on by NEXT_PUBLIC_DEMO=1 at build time (the whole site is the demo, e.g. GitHub Pages), or per tab by
// "Explore the demo" on sign-in (sessionStorage keepframe.role = "demo"; see lib/session.ts).
//
// The dataset is one licensed sample event (TechSummit 2026: Web Summit photos, CC BY) captured from a local run
// through the API's alias layer, so it only carries aliases (ev_01, ph_…, gx_…, run_demo0N).

import type { Approval, GroupsPage, Member, Page, Run, RunAgent, Verdict } from "./types";

export const DEMO_BUILD = process.env.NEXT_PUBLIC_DEMO === "1";
const BASE = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/+$/, "");
export const DEMO_NOTICE = "Demo mode: changes stay in your browser";

type Query = Record<string, string | number | undefined | null>;

export class DemoMiss extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// ------------------------------------------------------------------ routing (pure, unit-tested)

const seg = (s: string) => encodeURIComponent(decodeURIComponent(s));

/** API path + query → the JSON file that answers it, or null when the demo has no such data. */
export function demoFile(path: string, query: Query = {}): string | null {
  const p = path.replace(/\/+$/, "");
  let m: RegExpExecArray | null;
  if (p === "/health") return "health.json";
  if (p === "/events") return "events.json";
  if (p === "/runs") return "runs.json";
  if (p === "/approvals") return "approvals.json";
  if (p === "/bench") return "bench.json";
  if ((m = /^\/events\/([^/]+)\/groups$/.exec(p))) {
    return query.group_id ? `groups/${seg(m[1])}/${seg(String(query.group_id))}.json` : `groups/${seg(m[1])}.json`;
  }
  if ((m = /^\/runs\/([^/]+)\/agent$/.exec(p))) return `runs/${seg(m[1])}/agent.json`;
  if ((m = /^\/runs\/([^/]+)\/trace$/.exec(p))) {
    return query.group_id ? `traces/${seg(m[1])}/${seg(String(query.group_id))}.json` : `traces/${seg(m[1])}.json`;
  }
  if ((m = /^\/runs\/([^/]+)$/.exec(p))) return `runs/${seg(m[1])}.json`;
  return null;
}

/** "/demo/img/t/ph_x.webp" (as captured) → the same path under the build's basePath. */
export function withBase(u: string, base = BASE): string {
  return base && u.startsWith("/demo/") ? `${base}${u}` : u;
}

function rebase<T>(v: T): T {
  if (!BASE) return v;
  if (typeof v === "string") return withBase(v) as T;
  if (Array.isArray(v)) return v.map(rebase) as T;
  if (v && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) out[k] = rebase(x);
    return out as T;
  }
  return v;
}

// ------------------------------------------------------------------ in-memory overlay

interface Answer {
  approval_id: string;
  group_id?: string;
  /** photo id → verdict the visitor chose */
  verdicts: Record<string, Verdict>;
  note: string;
  at: string;
}

interface Overlay {
  answers: Map<string, Answer>;
  runs: Run[];
}

const STORE = "keepframe.demo.overlay";
const overlay: Overlay = restore();
const listeners = new Set<() => void>();

/** The overlay survives a reload of this tab (sessionStorage), and nothing else: never sent anywhere. */
function restore(): Overlay {
  try {
    const raw = typeof window !== "undefined" ? window.sessionStorage.getItem(STORE) : null;
    if (raw) {
      const doc = JSON.parse(raw) as { answers: Answer[]; runs: Run[] };
      return { answers: new Map(doc.answers.map((a) => [a.approval_id, a])), runs: doc.runs };
    }
  } catch {
    /* corrupt or unavailable: start clean */
  }
  return { answers: new Map(), runs: [] };
}
function persist() {
  try {
    window.sessionStorage.setItem(STORE, JSON.stringify({ answers: [...overlay.answers.values()], runs: overlay.runs }));
  } catch {
    /* storage full or blocked: changes still live until the page closes */
  }
}

export function onDemoChange(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}
const changed = () => {
  persist();
  listeners.forEach((l) => l());
};

export function demoAnsweredCount(): number {
  return overlay.answers.size;
}

/** Test hook: forget every in-browser change. */
export function resetDemo(): void {
  overlay.answers.clear();
  overlay.runs = [];
  changed();
}

const cache = new Map<string, Promise<unknown>>();

async function load<T>(file: string): Promise<T> {
  let p = cache.get(file) as Promise<T> | undefined;
  if (!p) {
    p = fetch(`${BASE}/demo/${file}`).then(async (r) => {
      if (!r.ok) throw new DemoMiss(r.status === 404 ? 404 : r.status, r.status === 404 ? "Not in the demo dataset" : `Demo data failed to load (${r.status})`);
      return (await r.json()) as T;
    });
    cache.set(file, p);
    p.catch(() => cache.delete(file));
  }
  // structuredClone: callers may mutate what they get; the cached copy stays pristine.
  return structuredClone(await p);
}

function answerFor(groupId?: string): Answer | undefined {
  if (!groupId) return undefined;
  for (const a of overlay.answers.values()) if (a.group_id === groupId || a.approval_id === groupId) return a;
  return undefined;
}

/** A pending group the visitor answered: the verdicts become theirs, and it leaves the queue. */
export function applyAnswerToGroup<G extends { group_id: string; pending_approval: boolean; members: Member[]; verdict_counts: Partial<Record<Verdict, number>> }>(
  g: G,
  a: Answer | undefined,
): G {
  if (!a) return g;
  const members = g.members.map((m) =>
    a.verdicts[m.photo_id]
      ? { ...m, verdict: a.verdicts[m.photo_id], decided_by: "human" as const, decision_source: "ui" as const, human_note: a.note || undefined, confidence: 0.9 }
      : m,
  );
  const verdict_counts: Partial<Record<Verdict, number>> = {};
  for (const m of members) if (m.verdict) verdict_counts[m.verdict] = (verdict_counts[m.verdict] ?? 0) + 1;
  return { ...g, members, verdict_counts, pending_approval: false };
}

function patchRunsPage(page: Page<Run>, eventId?: string): Page<Run> {
  const extra = overlay.runs.filter((r) => !eventId || r.event_id === eventId);
  const items = [...extra, ...page.items].map((r) => patchRun(r));
  return { ...page, items };
}

/** A run started in the demo replays the shape of a real one over ~40 s (scoring, then the agent), then
 *  finishes with the captured run's verdicts. Nothing is computed: it is a playback, and the run note says so. */
const SIM = { workerS: 12, agentS: 28 };
function simulate(r: Run, template?: Run): Run {
  const t0 = Date.parse(r.started_at ?? "") || Date.now();
  const s = (Date.now() - t0) / 1000;
  const note = "Demo playback: a replay of an earlier run's results. Nothing was processed.";
  const tpl: Partial<Run> = template ?? {};
  if (s < 2) return { ...r, status: "starting", run_note: note };
  if (s < SIM.workerS) return { ...r, status: "running", run_note: note, photos_processed: Math.round(((s - 2) / (SIM.workerS - 2)) * 278) };
  if (s < SIM.workerS + SIM.agentS) {
    const f = (s - SIM.workerS) / SIM.agentS;
    return { ...r, status: "worker_done", run_note: note, worker_ended_at: new Date(t0 + SIM.workerS * 1000).toISOString(), photos_processed: 278, tool_call_count: Math.round(f * (tpl.tool_call_count ?? 180)) };
  }
  const ended = new Date(t0 + (SIM.workerS + SIM.agentS) * 1000).toISOString();
  return {
    ...tpl,
    ...r,
    status: "succeeded",
    run_note: note,
    ended_at: ended,
    updated_at: ended,
    duration_s: SIM.workerS + SIM.agentS,
    photos_processed: 278,
    verdict_counts: tpl.verdict_counts,
    tool_call_count: tpl.tool_call_count,
    escalations: tpl.escalations,
    llm_provider: tpl.llm_provider,
    planner_model: tpl.planner_model,
    opencv_version: tpl.opencv_version,
    opencv_hal: tpl.opencv_hal,
  };
}
let template: Run | undefined;

function patchRun(r: Run): Run {
  if (overlay.runs.some((x) => x.run_id === r.run_id)) return simulate(r, template);
  // A visitor who answers every close call of the waiting run sees it finish.
  if (r.status === "awaiting_approval" && overlay.answers.size > 0) {
    const left = pendingLeft(r.run_id);
    if (left === 0) return { ...r, status: "succeeded", ended_at: lastAnswerAt(), last_answer_source: "ui" };
    return { ...r, last_answer_source: "ui" };
  }
  return r;
}

let pendingIds: { run_id: string; ids: string[] }[] | undefined;
function pendingLeft(runId: string): number | undefined {
  const row = pendingIds?.find((p) => p.run_id === runId);
  return row ? row.ids.filter((id) => !overlay.answers.has(id)).length : undefined;
}
function lastAnswerAt(): string {
  return [...overlay.answers.values()].map((a) => a.at).sort().at(-1) ?? new Date().toISOString();
}

// ------------------------------------------------------------------ the adapter

export async function demoGet<T>(path: string, query: Query = {}): Promise<T> {
  const file = demoFile(path, query);
  if (!file) throw new DemoMiss(404, "Not in the demo dataset");
  const started = overlay.runs.find((r) => path.startsWith(`/runs/${r.run_id}`));
  if (started) {
    const now = patchRun(started);
    if (path.endsWith("/agent")) return { run_id: started.run_id, path: "none", run_status: now.status, live: null } satisfies RunAgent as T;
    if (path.endsWith("/trace")) return { run_id: started.run_id, source: "demo", items: [], pivots: [], next_after: null } as T;
    return now as T;
  }
  const doc = rebase(await load<unknown>(file));
  if (file === "approvals.json") {
    const page = doc as Page<Approval>;
    if (!pendingIds) pendingIds = groupBy(page.items);
    return { ...page, items: page.items.filter((a) => !overlay.answers.has(a.approval_id)) } as T;
  }
  if (file === "runs.json") {
    await ensurePending();
    template ??= (doc as Page<Run>).items.find((r) => r.status === "succeeded");
    return patchRunsPage(doc as Page<Run>, query.event_id ? String(query.event_id) : undefined) as T;
  }
  if (file.startsWith("runs/") && !file.endsWith("/agent.json")) {
    await ensurePending();
    return patchRun(doc as Run) as T;
  }
  if (file.startsWith("groups/")) {
    const page = doc as GroupsPage;
    let items = page.items.map((g) => applyAnswerToGroup(g, answerFor(g.group_id)));
    // The captured event page holds every group; honour limit/cursor so pagination UIs still work.
    if (!query.group_id) {
      const limit = Number(query.limit ?? 24);
      const offset = query.cursor ? Number(String(query.cursor).replace(/^o/, "")) || 0 : 0;
      const next = offset + limit < items.length ? `o${offset + limit}` : null;
      items = items.slice(offset, offset + limit);
      const counts = offset ? undefined : adjustCounts(page);
      return { ...page, items, next_cursor: next, event_verdict_counts: counts } as T;
    }
    return { ...page, items } as T;
  }
  return doc as T;
}

/** Event totals with the visitor's answers applied: captured totals, minus each answered photo's old verdict,
 *  plus its new one (singletons and unanswered groups are unchanged). */
function adjustCounts(page: GroupsPage): GroupsPage["event_verdict_counts"] {
  const base = page.event_verdict_counts;
  if (!base || !overlay.answers.size) return base;
  const out = { ...base };
  for (const g of page.items) {
    const a = answerFor(g.group_id);
    if (!a) continue;
    for (const m of g.members) {
      const next = a.verdicts[m.photo_id];
      if (!next || next === m.verdict) continue;
      if (m.verdict) out[m.verdict] = Math.max(0, (out[m.verdict] ?? 0) - 1);
      out[next] = (out[next] ?? 0) + 1;
    }
  }
  return out;
}

async function ensurePending() {
  if (pendingIds) return;
  try {
    const page = rebase(await load<Page<Approval>>("approvals.json"));
    pendingIds = groupBy(page.items);
  } catch {
    pendingIds = [];
  }
}

function groupBy(items: Approval[]): { run_id: string; ids: string[] }[] {
  const out = new Map<string, string[]>();
  for (const a of items) out.set(a.run_id, [...(out.get(a.run_id) ?? []), a.approval_id]);
  return [...out.entries()].map(([run_id, ids]) => ({ run_id, ids }));
}

interface ApprovalBody {
  decision: "approve" | "override";
  note?: string;
  human_verdict?: Verdict | Partial<Record<Verdict, string[]>>;
}

export async function demoPost<T>(path: string, body: unknown): Promise<T> {
  let m: RegExpExecArray | null;
  if ((m = /^\/approvals\/([^/]+)$/.exec(path))) {
    const id = decodeURIComponent(m[1]);
    const page = rebase(await load<Page<Approval>>("approvals.json"));
    if (!pendingIds) pendingIds = groupBy(page.items);
    const item = page.items.find((a) => a.approval_id === id);
    if (!item || overlay.answers.has(id)) throw new DemoMiss(404, "No pending approval with this id (already answered?)");
    const b = body as ApprovalBody;
    const verdicts = toVerdicts(b.decision === "approve" ? item.proposed : b.human_verdict, item);
    if (!Object.values(verdicts).includes("keep") && item.group_id) throw new DemoMiss(422, "Keep at least one photo from the group");
    overlay.answers.set(id, { approval_id: id, group_id: item.group_id, verdicts, note: (b.note ?? "").trim(), at: new Date().toISOString() });
    changed();
    return { approval_id: id, status: "resolved", resume: { via: "api-direct", status: 200 } } as T;
  }
  if (path === "/runs") {
    const { event_id } = body as { event_id: string };
    const runs = await demoGet<Page<Run>>("/runs", { event_id });
    if (runs.items.some((r) => ["starting", "running", "awaiting_approval"].includes(r.status))) {
      throw new DemoMiss(409, "A run is already in progress for this event.");
    }
    const now = new Date().toISOString();
    const run: Run = { run_id: `run_demo${String(overlay.runs.length + 3).padStart(2, "0")}`, event_id, status: "starting", flavor: "stock", started_at: now, updated_at: now };
    overlay.runs.unshift(run);
    changed();
    return run as T;
  }
  throw new DemoMiss(404, "Not available in the demo");
}

function toVerdicts(v: ApprovalBody["human_verdict"] | Approval["proposed"], item: Approval): Record<string, Verdict> {
  const out: Record<string, Verdict> = {};
  if (typeof v === "string") {
    if (item.photo_id) out[item.photo_id] = v;
    return out;
  }
  for (const [verdict, ids] of Object.entries(v ?? {}) as [Verdict, string | string[] | undefined][]) {
    for (const pid of Array.isArray(ids) ? ids : ids ? [ids] : []) out[pid] = verdict;
  }
  return out;
}

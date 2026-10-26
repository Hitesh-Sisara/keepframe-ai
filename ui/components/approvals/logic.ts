// Pure view logic for the approvals desk (no React, no runtime imports: lib/approvals.test.ts runs it under
// node --test). Display-only transforms over the API's Approval / Group shapes.
import { fmtGap, gapText, isTie } from "../../lib/closecall.ts";
import { frameOrder, marginsToPts } from "../../lib/cull.ts";
import { eyesTo100, groupDigits, plainTerms } from "../../lib/glossary.ts";
export { fmtGap };
import type { Approval, DecisionSource, Group, Proposed, Verdict } from "../../lib/types";

export type Assign = Record<string, Verdict>;

export interface FrameVM {
  photo_id: string;
  proxy_url: string;
  /** 1-based position, the same number the burst and trace pages use (lib/cull frameOrder): capture order when
   *  the ids carry it, else rank order (aliased ids). Never the raw id. */
  n: number;
  rank?: number;
  score?: number;
  breakdown?: Record<string, number>;
}

/** One pending request, whether it came from /approvals (team) or a pending group (judge, read-only). */
export interface ApprovalVM {
  id: string;
  eventId: string;
  eventName?: string;
  runId?: string;
  groupId?: string;
  groupLabel: string;
  isGroup: boolean;
  frames: FrameVM[];
  proposed: Assign;
  /** Proposal as the API sent it; approve is only possible when there is one. */
  hasProposal: boolean;
  question: string;
  why: string[];
  confidence?: number;
  createdAt?: string;
  contactSheetUrl?: string;
  resumeError?: string;
  spanS?: number;
  /** Where the verdicts come from, which sets the wording: "capture" = an /approvals request (the agent's
   *  proposal), "rank" = a group rebuilt from /events/{id}/groups (its current verdicts). Frame numbers do not
   *  depend on it: see FrameVM.n. */
  order: "capture" | "rank";
  /** Where the frame scores came from: the agent's own ranking step (pick_best_in_cluster in the trace) or the
   *  worker's stored group score. Shown as the score's label so the two never look like one number. */
  scoreSource?: "agent" | "worker";
}

/** Label for a frame score, by where it came from. */
export function scoreLabel(src?: ApprovalVM["scoreSource"]): string {
  return "Score";
}

/** The agent's ranking (pick_best_in_cluster result) as the frames' scores and ranks. Frames the ranking
 *  doesn't name keep their worker score. Returns undefined when the ranking names none of these frames. */
export function applyRankScores(frames: FrameVM[], ranking: { photo_id: string; score: number }[] | undefined): FrameVM[] | undefined {
  if (!ranking?.length) return undefined;
  const by = new Map(ranking.map((r, i) => [r.photo_id, { score: r.score, rank: i + 1 }]));
  if (!frames.some((f) => by.has(f.photo_id))) return undefined;
  return frames.map((f) => {
    const r = by.get(f.photo_id);
    return r ? { ...f, score: r.score, rank: r.rank } : f;
  });
}

export function proposedMap(proposed: Proposed | Verdict | undefined, photoId?: string): Assign {
  const out: Assign = {};
  if (!proposed) return out;
  if (typeof proposed === "string") {
    if (photoId) out[photoId] = proposed;
    return out;
  }
  const add = (ids: string | string[] | undefined, v: Verdict) => {
    for (const id of typeof ids === "string" ? [ids] : (ids ?? [])) out[id] = v;
  };
  add(proposed.reject, "reject");
  add(proposed.review, "review");
  add(proposed.keep, "keep");
  return out;
}

/** Full keep/reject/review map for an override; frames the proposal didn't mention go to review. */
export function toVerdictMap(frames: { photo_id: string }[], assign: Assign): { keep: string[]; reject: string[]; review: string[] } {
  const m = { keep: [] as string[], reject: [] as string[], review: [] as string[] };
  for (const f of frames) m[assign[f.photo_id] ?? "review"].push(f.photo_id);
  return m;
}

export function sameAssign(frames: { photo_id: string }[], a: Assign, b: Assign): boolean {
  return frames.every((f) => (a[f.photo_id] ?? "review") === (b[f.photo_id] ?? "review"));
}

/** "Pick another": the clicked frame becomes the keeper. The agent's keeper is not thrown away: it goes to
 *  review (it was a close call), every other frame keeps the agent's verdict. */
export function pickFrame(frames: { photo_id: string }[], proposed: Assign, photoId: string): Assign {
  const out: Assign = {};
  for (const f of frames) {
    const was = proposed[f.photo_id] ?? "review";
    out[f.photo_id] = f.photo_id === photoId ? "keep" : was === "keep" ? "review" : was;
  }
  return out;
}

/** "Keep all for review": nothing is rejected. The current keeper stays keep (a group always keeps at least
 *  one frame, policy 4), every other frame goes to review. */
export function keepAllForReview(frames: { photo_id: string }[], current: Assign): Assign {
  const keeper = frames.find((f) => current[f.photo_id] === "keep")?.photo_id ?? frames[0]?.photo_id;
  const out: Assign = {};
  for (const f of frames) out[f.photo_id] = f.photo_id === keeper ? "keep" : "review";
  return out;
}

export function keeperOf(frames: { photo_id: string }[], a: Assign): string | undefined {
  return frames.find((f) => a[f.photo_id] === "keep")?.photo_id;
}

export function frameWord(frames: FrameVM[], photoId?: string): string {
  const f = frames.find((x) => x.photo_id === photoId);
  return f ? `Frame ${f.n}` : "a frame";
}

/** "keep Frame 5 · review Frames 3, 4 · reject 2" — what an answer will save. */
export function planSummary(frames: FrameVM[], a: Assign, order: ApprovalVM["order"] = "capture"): string {
  void order; // numbering is the same for every source (FrameVM.n)
  const word = "Frame";
  const by: Record<Verdict, number[]> = { keep: [], review: [], reject: [] };
  for (const f of frames) by[a[f.photo_id] ?? "review"].push(f.n);
  const part = (v: Verdict) => {
    const ns = by[v];
    if (!ns.length) return null;
    if (v === "reject" && ns.length > 3) return `reject the other ${ns.length}`;
    return `${v} ${word}${ns.length === 1 ? "" : "s"} ${ns.join(", ")}`;
  };
  return (["keep", "review", "reject"] as const).map(part).filter(Boolean).join(" · ");
}

const PHOTO_ID = /\b(p_\d{3,}|ph_[0-9a-f]{6,})\b/g;

/** Replace photo ids in agent text with "Frame n" (burst position). Unknown ids are dropped to "a frame". */
export function idsToFrames(text: string, frames: FrameVM[]): string {
  const byId = new Map(frames.map((f) => [f.photo_id, f.n]));
  // A comma list of ids ("p_0017, p_0014") becomes "Frames 2 and 5", sorted.
  const LIST = new RegExp(`${PHOTO_ID.source}(?:,\\s*${PHOTO_ID.source})+`, "g");
  const out = text.replace(LIST, (m) => {
    const ns = (m.match(PHOTO_ID) ?? []).map((id) => byId.get(id)).filter((n): n is number => n !== undefined).sort((x, y) => x - y);
    if (!ns.length) return "some frames";
    if (ns.length === 1) return `Frame ${ns[0]}`;
    return `Frames ${ns.slice(0, -1).join(", ")} and ${ns[ns.length - 1]}`;
  });
  return out.replace(PHOTO_ID, (id) => (byId.has(id) ? `Frame ${byId.get(id)}` : "a frame"));
}

/** The agent's terse reason ("Top two frames within 0.01; main subject's eyes closed in p_0006") as plain
 *  sentences. Display-only; unknown clauses pass through with ids replaced, score gaps in points and long numbers
 *  grouped, the way the trace shows them. Measurement names (lap_var, open_p) stay as keys here so the desk can
 *  render them with <Glossed plain> (plain word + tooltip); plain-string callers use plainTerms(). */
export function plainReasons(reason: string | undefined, frames: FrameVM[]): string[] {
  if (!reason) return [];
  return reason
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((clause) => {
      let s = eyesTo100(groupDigits(marginsToPts(idsToFrames(clause, frames))));
      s = s.replace(/^Top two frames within ([\d.]+)$/i, (_m, d: string) =>
        Number(d) === 0 ? "The top two frames scored exactly the same" : `The top two frames scored within ${d} of each other`,
      );
      s = s.replace(/^main subject's eyes closed in (.+)$/i, (_m, who: string) => `The main subject's eyes are closed in ${who}`);
      s = s.replace(/^(\d+) frame\(s\) with a merit the keeper lacks$/i, (_m, n: string) =>
        n === "1" ? "Another frame has something the keeper lacks" : `${n} other frames have something the keeper lacks`,
      );
      s = s.charAt(0).toUpperCase() + s.slice(1);
      return /[.!?]$/.test(s) ? s : `${s}.`;
    });
}

/** The question the agent is effectively asking, in plain words. */
export function questionFor(isGroup: boolean, frameCount: number): string {
  if (!isGroup) return "Keep this photo?";
  return frameCount === 2 ? "Which of these two frames is the keeper?" : "Which frame should be the keeper?";
}

/** Up to `max` frames shown large for comparison: the agent's keeper, then its best-ranked alternatives,
 *  laid out in capture order so the burst still reads left to right. */
export function chooseCandidates(frames: FrameVM[], proposed: Assign, max = 4, pinned: (string | undefined)[] = []): FrameVM[] {
  if (frames.length <= max) return frames;
  const pins = [keeperOf(frames, proposed), ...pinned].filter((x): x is string => Boolean(x));
  const pinRank = (id: string) => {
    const i = pins.indexOf(id);
    return i < 0 ? Number.POSITIVE_INFINITY : i;
  };
  const ranked = [...frames].sort((a, b) => {
    const pa = pinRank(a.photo_id);
    const pb = pinRank(b.photo_id);
    if (pa !== pb) return pa - pb;
    const ra = a.rank ?? Number.POSITIVE_INFINITY;
    const rb = b.rank ?? Number.POSITIVE_INFINITY;
    if (ra !== rb) return ra - rb;
    const va = proposed[a.photo_id] === "review" ? 0 : 1;
    const vb = proposed[b.photo_id] === "review" ? 0 : 1;
    return va - vb || a.n - b.n;
  });
  const pick = new Set(ranked.slice(0, max).map((f) => f.photo_id));
  return frames.filter((f) => pick.has(f.photo_id));
}

/** Survey-mode fit: the column count that makes n frames (aspect w/h) as large as possible in w × h, using at
 *  least `minCols` columns (the approvals desk keeps its candidates side by side so they compare in one glance). */
export function fitColumns(n: number, width: number, height: number, aspect = 1.5, gap = 16, caption = 64, minCols = 1): { cols: number; frameW: number } {
  let best = { cols: 1, frameW: 0 };
  for (let cols = Math.max(1, Math.min(n, minCols)); cols <= n; cols++) {
    const rows = Math.ceil(n / cols);
    const byW = (width - gap * (cols - 1)) / cols;
    const byH = Number.isFinite(height) ? ((height - gap * (rows - 1)) / rows - caption) * aspect : Number.POSITIVE_INFINITY;
    const w = Math.max(0, Math.min(byW, byH));
    if (w > best.frameW + 0.5) best = { cols, frameW: w };
  }
  return best;
}

export function relAge(iso: string | undefined, now: number): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return `${d} d ago`;
}

/** Metrics worth comparing across candidates, in the order shown. Values are 0–1. */
export const METRICS: { key: string; label: string }[] = [
  { key: "eyes_open_mean", label: "Eyes open" },
  { key: "sharp_faces_norm", label: "Sharp faces" },
  { key: "fiqa_mean", label: "Face quality" },
];

/** No face data: pick_best_in_cluster fills eyes-open and face quality with exactly 0.5 ("unknown") when a frame
 *  has no faces (keepframe/tools/best.py), so a 50/50 pair is a placeholder, not a measurement. */
export function noFaces(b?: Record<string, number>): boolean {
  return Boolean(b) && b!.eyes_open_mean === 0.5 && b!.fiqa_mean === 0.5;
}

/** For each metric, the photo id with the highest value among `frames` (only when it strictly leads). */
export function leaders(frames: FrameVM[]): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {};
  for (const key of ["score", ...METRICS.map((m) => m.key)]) {
    let bestId: string | undefined;
    let best = Number.NEGATIVE_INFINITY;
    let tie = false;
    for (const f of frames) {
      const raw = key === "score" ? f.score : noFaces(f.breakdown) && key !== "score" ? undefined : f.breakdown?.[key];
      if (typeof raw !== "number") continue;
      // Compare what is shown (score to 3 dp, metrics as whole percent) so a visible tie is never "best".
      const v = key === "score" ? Math.round(raw * 1000) : Math.round(raw * 100);
      if (v > best) {
        best = v;
        bestId = f.photo_id;
        tie = false;
      } else if (v === best) tie = true;
    }
    out[key] = tie ? undefined : bestId;
  }
  return out;
}

/** Frames numbered the way the burst and trace pages number them (frameOrder over the group's ranks), so
 *  "Frame 3" is the same photo on every page. Without the group, ranks are unknown and the API order stands. */
function enrichFrames(members: { photo_id: string; proxy_url: string }[], group?: Group): FrameVM[] {
  const g = new Map((group?.members ?? []).map((m) => [m.photo_id, m]));
  const withRank = members.map((m) => ({ photo_id: m.photo_id, proxy_url: m.proxy_url, rank: g.get(m.photo_id)?.rank }));
  return frameOrder(withRank).frames.map((m, i) => {
    const gm = g.get(m.photo_id);
    return { ...m, n: i + 1, score: gm?.score, breakdown: gm?.breakdown };
  });
}

/** Team: an /approvals item, optionally enriched with its group (scores, ranks). */
export function fromApproval(a: Approval, group?: Group, eventName?: string): ApprovalVM {
  const isGroup = Boolean(a.group_id && !a.photo_id);
  const frames = enrichFrames(a.members ?? [], group);
  return {
    id: a.approval_id,
    eventId: a.event_id,
    eventName,
    runId: a.run_id,
    groupId: a.group_id,
    groupLabel: a.group_label ?? (isGroup ? "Group" : "Photo"),
    isGroup,
    frames,
    proposed: proposedMap(a.proposed, a.photo_id),
    hasProposal: Boolean(a.proposed),
    question: questionFor(isGroup, frames.length),
    why: plainReasons(a.reason, frames),
    confidence: a.confidence,
    createdAt: a.created_at,
    contactSheetUrl: a.contact_sheet_url,
    resumeError: a.resume_error,
    spanS: group?.span_s,
    order: "capture",
  };
}

/** Judge (read-only) and answered history: a group rebuilt from /events/{id}/groups. Members come back best
 *  first; frames are numbered by frameOrder like every other page (rank when the ids are aliased). The verdicts are
 *  the group's current ones (for an answered group, the answer), not necessarily the agent's proposal. */
export function fromPendingGroup(g: Group, eventId: string, eventName?: string): ApprovalVM {
  const frames = enrichFrames(g.members, g);
  const proposed: Assign = {};
  for (const m of g.members) if (m.verdict) proposed[m.photo_id] = m.verdict;
  const why: string[] = [];
  if (typeof g.margin === "number") {
    why.push(
      isTie(g) ? "The top two frames scored almost exactly the same." : `The top two frames scored ${gapText(g.margin)} apart, out of 100.`,
    );
  }
  return {
    id: `${eventId}:${g.group_id}`,
    eventId,
    eventName,
    runId: g.run_id,
    groupId: g.group_id,
    groupLabel: g.group_label ?? "Group",
    isGroup: true,
    frames,
    proposed,
    hasProposal: Object.keys(proposed).length > 0,
    question: questionFor(true, frames.length),
    why,
    confidence: g.members.find((m) => m.is_best)?.confidence,
    createdAt: (g as Group & { updated_at?: string }).updated_at,
    contactSheetUrl: g.contact_sheet_url,
    spanS: g.span_s,
    order: "rank",
  };
}

/** Label of a frame: "Frame 3", the same name the burst and trace pages give it. */
export function frameName(_order: ApprovalVM["order"], n: number): string {
  return `Frame ${n}`;
}

/** Who answered an already-answered request. Neutral wording: only "ui" happened in this desk, and nothing is
 *  ever called a photographer's decision. */
export const ANSWERED_WORD: Record<DecisionSource, string> = {
  scripted: "Answered automatically (test run)",
  lead_test: "Team mechanism test",
  ui: "Answered in this desk",
  file: "Answered (imported)",
  http: "Answered by a reviewer",
  agent: "Decided by the agent",
  unknown: "Answered, source not recorded",
};

function topTwo(frames: FrameVM[]): [FrameVM, FrameVM] | null {
  const scored = frames.filter((f) => typeof f.score === "number").sort((a, b) => (b.score as number) - (a.score as number));
  return scored.length >= 2 ? [scored[0], scored[1]] : null;
}

/** Replace the agent's rounded "within 0.01" with the real scores when they are known. */
export function refineWhy(why: string[], frames: FrameVM[], order: ApprovalVM["order"] = "capture"): string[] {
  const t = topTwo(frames);
  if (!t) return why;
  const [a, b] = t;
  // Scores in points out of 100 (one decimal), and the gap is the difference of the rounded points shown, so the
  // sentence always adds up. Same scale as the groups pages ("1.4 pts apart").
  // Under one point, one decimal can show equal scores or "0.0 apart": use two.
  const fine = Math.abs((a.score as number) - (b.score as number)) * 100 < 1;
  const dp = fine ? 2 : 1;
  const pa = Math.round((a.score as number) * 10 ** (dp + 2)) / 10 ** dp;
  const pb = Math.round((b.score as number) * 10 ** (dp + 2)) / 10 ** dp;
  const d = fmtGap((pa - pb) / 100);
  const line =
    order === "rank"
      ? `The top two frames scored ${pa.toFixed(dp)} and ${pb.toFixed(dp)} out of 100, only ${d} apart.`
      : `Frame ${a.n} scored ${pa.toFixed(dp)} and Frame ${b.n} ${pb.toFixed(dp)} out of 100, only ${d} apart.`;
  return why.map((w) => (/^The top two frames scored (within|exactly|almost|[\d.]+ pts)/.test(w) ? line : w));
}

/** "Frame 3 has sharper faces" when a non-keeper strictly leads the burst on a face metric. Display-only. */
export function strengthText(vm: Pick<ApprovalVM, "frames" | "proposed" | "order">): string | undefined {
  const keeper = keeperOf(vm.frames, vm.proposed);
  const lead = leaders(vm.frames);
  const WORD: Record<string, string> = { eyes_open_mean: "eyes more open", sharp_faces_norm: "sharper faces", fiqa_mean: "better face quality" };
  for (const m of METRICS) {
    const id = lead[m.key];
    if (!id || id === keeper) continue;
    const f = vm.frames.find((x) => x.photo_id === id);
    if (f) return `${frameName(vm.order, f.n)} has ${WORD[m.key] ?? m.label.toLowerCase()}`;
  }
  return undefined;
}

/** One short line for the inbox that tells requests apart ("Frames 5 vs 4, 1.4 pts apart · eyes closed in Frame 6"). */
export function gist(vm: ApprovalVM): string {
  const parts: string[] = [];
  const t = topTwo(vm.frames);
  if (t) {
    const d = fmtGap((t[0].score as number) - (t[1].score as number));
    parts.push(`${d} gap`);
  }
  for (const w of vm.why) {
    const eyes = /eyes are closed in (.+)\.$/.exec(w);
    if (eyes) parts.push(`eyes closed in ${eyes[1]}`);
    else if (/something the keeper lacks/.test(w)) parts.push(strengthText(vm) ?? "another frame beats the keeper on one check");
  }
  if (!parts.length) return vm.why[0] ? plainTerms(vm.why[0]) : vm.question;
  const s = parts.join(" · ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

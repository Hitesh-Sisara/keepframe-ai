// Display helpers for runs on the events/runs pages. Pure functions, no React, so they are unit-tested.
import type { Run, VerdictCounts } from "./types";

/** Extra RUN# fields the API already returns (views.RUN_FIELDS) that the shared Run type does not name. */
export type RunX = Run & {
  n_photos?: number;
  n_groups?: number;
  agent_started_at?: string;
  agent_ended_at?: string;
  worker_started_at?: string;
  error_code?: string;
};

export type RunTone = "running" | "waiting" | "done" | "failed" | "cancelled";

/** Statuses during which a run still changes (the API's ACTIVE_RUN_STATES, plus the local runner's
 *  "worker_done" between the worker pass and the agent pass). */
export const LIVE_STATUSES = new Set(["starting", "running", "worker_done", "awaiting_approval"]);

export function isLive(status?: string): boolean {
  return Boolean(status && LIVE_STATUSES.has(status));
}

export function runTone(status?: string): RunTone {
  switch (status) {
    case "awaiting_approval":
      return "waiting";
    case "succeeded":
      return "done";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
    default:
      return "running";
  }
}

export const TONE_WORD: Record<RunTone, string> = {
  running: "Running",
  waiting: "Waiting for you",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const PHASES = ["Starting", "Scoring frames", "Agent review"] as const;

/** Which of PHASES a live run is in (0-2), or -1 for a finished run. Driven by the stamps each pass writes. */
export function runPhase(run: Pick<RunX, "status" | "worker_ended_at" | "agent_started_at">): number {
  if (!isLive(run.status)) return -1;
  if (run.status === "starting") return 0;
  if (run.status === "worker_done" || run.status === "awaiting_approval" || run.agent_started_at || run.worker_ended_at) return 2;
  return 1;
}

/** 0-1 when the run reports how many photos it has processed, else undefined (indeterminate). */
export function runFraction(run: Pick<RunX, "photos_processed" | "n_photos">): number | undefined {
  const done = run.photos_processed;
  const total = run.n_photos;
  if (typeof done !== "number" || typeof total !== "number" || total <= 0) return undefined;
  return Math.max(0, Math.min(1, done / total));
}

/** Per-event ordinals ("Run 3"): runs of one event numbered oldest first. Runs without a start time sort last. */
export function runOrdinals(runs: Pick<Run, "run_id" | "event_id" | "started_at">[]): Record<string, number> {
  const byEvent = new Map<string, Pick<Run, "run_id" | "started_at">[]>();
  for (const r of runs) byEvent.set(r.event_id, [...(byEvent.get(r.event_id) ?? []), r]);
  const out: Record<string, number> = {};
  for (const list of byEvent.values()) {
    const sorted = [...list].sort((a, b) => (a.started_at ?? "~").localeCompare(b.started_at ?? "~"));
    sorted.forEach((r, i) => (out[r.run_id] = i + 1));
  }
  return out;
}

/** Six characters of a long id for display in mono ("run_01M5T8JMKP…" -> "4GA31B"). */
export function shortId(id: string): string {
  const core = id.replace(/^[a-z]+_/i, "");
  return core.slice(-6).toUpperCase();
}

/** "4 min 7 s"-style compact duration: "48s", "4m 07s", "1h 05m". */
export function fmtDuration(s?: number): string {
  if (typeof s !== "number" || !Number.isFinite(s) || s < 0) return "";
  const t = Math.round(s);
  if (t < 60) return `${t}s`;
  if (t < 3600) return `${Math.floor(t / 60)}m ${String(t % 60).padStart(2, "0")}s`;
  return `${Math.floor(t / 3600)}h ${String(Math.round((t % 3600) / 60)).padStart(2, "0")}m`;
}

/** Seconds between an ISO stamp and `now` (ms), or undefined if the stamp is missing or in the future. */
export function elapsedS(iso: string | undefined, now: number): number | undefined {
  if (!iso) return undefined;
  const t = Date.parse(iso);
  if (Number.isNaN(t) || t > now + 5000) return undefined;
  return Math.max(0, (now - t) / 1000);
}

/** "just now", "5 min ago", "3 h ago", "2 days ago"; falls back to "" for missing stamps. */
export function fmtAgo(iso: string | undefined, now: number): string {
  const s = elapsedS(iso, now);
  if (s === undefined) return "";
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

/** Median duration of finished runs, for "usually takes about 4 min" on the start-run confirm. */
export function typicalDurationS(runs: Pick<Run, "status" | "duration_s">[]): number | undefined {
  const d = runs
    .filter((r) => r.status === "succeeded" && typeof r.duration_s === "number" && r.duration_s > 0)
    .map((r) => r.duration_s as number)
    .sort((a, b) => a - b);
  if (!d.length) return undefined;
  const mid = Math.floor(d.length / 2);
  return d.length % 2 ? d[mid] : (d[mid - 1] + d[mid]) / 2;
}

export interface Tally {
  keep: number;
  review: number;
  reject: number;
  total: number;
}

export function tally(c?: VerdictCounts): Tally {
  const keep = c?.keep ?? 0;
  const review = c?.review ?? 0;
  const reject = c?.reject ?? 0;
  return { keep, review, reject, total: keep + review + reject };
}

/** A stable string that changes whenever any run's visible state changes; used to refetch event cards. */
export function runsSignature(runs?: Pick<Run, "run_id" | "status" | "updated_at">[]): string {
  return (runs ?? []).map((r) => `${r.run_id}:${r.status}:${r.updated_at ?? ""}`).join("|");
}

/** Short failure reason for a failed run, without internal detail. */
export function failureText(run: Pick<RunX, "error" | "error_code">): string {
  // The free-text error is team-only; judges get just a class name, and the generic "error" code says nothing.
  const code = run.error_code && run.error_code !== "error" ? run.error_code : "";
  const raw = (run.error || code || "").toString();
  if (!raw) return run.error_code ? "Stopped before finishing" : "";
  const s = raw.replace(/_/g, " ").trim();
  return s.length > 60 ? `${s.slice(0, 57)}…` : s.charAt(0).toUpperCase() + s.slice(1);
}

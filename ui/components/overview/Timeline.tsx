"use client";

import { runStages, type Stage } from "@/lib/overview";
import type { RunX } from "@/lib/runs";

/** "1.8 s", "13 s", "3 min 36 s". */
export function fmtSecs(s?: number): string {
  if (typeof s !== "number" || !Number.isFinite(s)) return "";
  // Non-breaking spaces keep a number with its unit when a line wraps.
  if (s < 10) return `${s.toFixed(1)}\u00a0s`;
  if (s < 60) return `${Math.round(s)}\u00a0s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return r ? `${m}\u00a0min ${r}\u00a0s` : `${m}\u00a0min`;
}

const COLOR: Record<Stage["key"], string> = {
  sync: "var(--kf-ov-sync)",
  score: "var(--kf-accent)",
  agent: "var(--kf-ov-agent)",
  waiting: "var(--kf-ov-wait)",
  done: "var(--kf-keep)",
};

/**
 * Ingest → Score → Agent → Waiting → Done. A compact stepper (state of each stage and its real duration) over a
 * proportional bar, so the split between machine time and waiting for people reads at a glance. Stages too
 * short to see on the bar keep a minimum sliver and their number in the stepper.
 */
export function RunTimeline({
  run,
  now,
  title,
  compact = false,
}: {
  run: RunX & { worker_wall_s?: number; agent_wall_s?: number };
  now: number;
  title?: string;
  /** Summary line + bar only (the Overview); the event page shows the full stepper. */
  compact?: boolean;
}) {
  const stages = runStages(run, now);
  const timed = stages.filter((s) => typeof s.s === "number" && s.key !== "done");
  const total = timed.reduce((a, s) => a + (s.s ?? 0), 0);
  const label = stages.map((s) => `${s.label}${s.s !== undefined ? ` ${fmtSecs(s.s)}` : ""}${s.state === "active" ? " (now)" : ""}`).join(", ");

  return (
    <div>
      {title && <h3 className="mb-3 text-[0.875rem] font-semibold">{title}</h3>}
      <ol className={`${compact ? "sr-only" : "flex"} flex-wrap items-center gap-x-1 gap-y-2`} aria-label={`Run stages: ${label}`}>
        {stages.map((s, i) => (
          <li key={s.key} className="flex items-center gap-1" title={s.hint}>
            <span
              className={`inline-flex h-7 items-center gap-1.5 rounded-full pr-3 pl-1 text-[0.8125rem] ${
                s.state === "todo" ? "text-[var(--kf-muted)] ring-1 ring-[var(--kf-border)] ring-inset" : "bg-[var(--kf-surface-2)] ring-1 ring-[var(--kf-border)] ring-inset"
              }`}
            >
              <span
                aria-hidden="true"
                className={`grid h-5 w-5 place-items-center rounded-full ${s.state === "todo" ? "border border-dashed border-[var(--kf-border-strong)]" : ""}`}
                style={s.state === "todo" ? undefined : { background: COLOR[s.key] }}
              >
                {s.state === "done" && (
                  <svg viewBox="0 0 12 12" className="h-2.5 w-2.5">
                    <path d="M2.5 6.3l2.3 2.2 4.7-5" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
                {s.state === "active" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white motion-reduce:animate-none" />}
              </span>
              <span className="font-semibold">{s.label}</span>
              <span className="t-data text-[var(--kf-muted)]">
                {s.key === "done" ? "" : s.s !== undefined ? fmtSecs(s.s) : s.state === "active" ? "now" : ""}
              </span>
            </span>
            {i < stages.length - 1 && (
              <svg aria-hidden="true" viewBox="0 0 12 12" className="h-3 w-3 text-[var(--kf-border-strong)]">
                <path d="M4.5 2.5L8 6l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            )}
          </li>
        ))}
      </ol>
      {total > 0 && (
        <div aria-hidden="true" className={`${compact ? "" : "mt-4"} flex h-7 w-full gap-[3px] overflow-hidden rounded-[8px]`}>
          {timed.map((s) => {
            const share = (s.s ?? 0) / total;
            return (
              <span
                key={s.key}
                data-k={s.key}
                title={`${s.label}: ${fmtSecs(s.s)}`}
                className="kf-ov-grow kf-ov-seg flex min-w-[6px] items-center overflow-hidden px-2 text-[0.75rem] font-semibold whitespace-nowrap"
                style={{ flexGrow: Math.max(share, 0.01), flexBasis: 0, background: COLOR[s.key] }}
              >
                {share > 0.12 && `${s.label} ${Math.round(share * 100)}%`}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

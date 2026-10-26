"use client";

import { useEffect, useState } from "react";
import { useSession } from "@/lib/session";
import { elapsedS, fmtDuration, PHASES, runFraction, runPhase, runTone, TONE_WORD, type RunTone, type RunX } from "@/lib/runs";

const PILL: Record<RunTone, string> = {
  running: "bg-[var(--kf-accent-soft,#e8eefc)] text-[#1d48b8] ring-[#c9d6f6]",
  waiting: "bg-[#fdf1de] text-[#8a5200] ring-[#f3d9ae]",
  done: "bg-[#e5f3ea] text-[#166c3d] ring-[#c4e3cf]",
  failed: "bg-[#fbe6e8] text-[#a8232f] ring-[#f2c4c9]",
  cancelled: "bg-[var(--kf-surface-2,#f5f7f9)] text-[var(--kf-muted,#5a646d)] ring-[var(--kf-border,#dfe4e8)]",
};

/** Status pill. Shape and word carry the state; colour is a second channel. */
export function RunStatusPill({ status, size = "md" }: { status?: string; size?: "sm" | "md" }) {
  const tone = runTone(status);
  // A judge can't answer: never "Waiting for you".
  const judge = useSession()?.role === "judge";
  const pad = size === "sm" ? "px-2 py-[1px] text-[0.75rem]" : "px-2.5 py-0.5 text-[0.78rem]";
  return (
    <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full font-semibold whitespace-nowrap ring-1 ring-inset ${pad} ${PILL[tone]}`}>
      <ToneIcon tone={tone} />
      {status === "starting" ? "Starting" : tone === "waiting" && judge ? "Waiting on the team" : TONE_WORD[tone]}
    </span>
  );
}

function ToneIcon({ tone }: { tone: RunTone }) {
  const cls = "h-3 w-3 shrink-0";
  if (tone === "running")
    return (
      <svg viewBox="0 0 12 12" className={`${cls} animate-spin motion-reduce:animate-none`} aria-hidden="true">
        <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.6" />
        <path d="M6 1.5a4.5 4.5 0 0 1 4.5 4.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    );
  if (tone === "waiting")
    return (
      <span className="relative flex h-2 w-2" aria-hidden="true">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-50 motion-reduce:hidden" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-current" />
      </span>
    );
  if (tone === "done")
    return (
      <svg viewBox="0 0 12 12" className={cls} aria-hidden="true">
        <path d="M2.5 6.3l2.3 2.2 4.7-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  if (tone === "failed")
    return (
      <svg viewBox="0 0 12 12" className={cls} aria-hidden="true">
        <path d="M3.2 3.2l5.6 5.6M8.8 3.2l-5.6 5.6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  return (
    <svg viewBox="0 0 12 12" className={cls} aria-hidden="true">
      <path d="M3 6h6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

/** Wall-clock `now` that ticks only while `enabled`, for live elapsed timers. */
export function useNow(enabled: boolean, everyMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), everyMs);
    return () => window.clearInterval(id);
  }, [enabled, everyMs]);
  return now;
}

/** Live progress for an active run: phase steps, elapsed time, and a determinate bar when the run reports
 *  photos processed (indeterminate otherwise). */
export function RunProgress({ run, compact = false, showElapsed = true }: { run: RunX; compact?: boolean; showElapsed?: boolean }) {
  const phase = runPhase(run);
  const now = useNow(phase >= 0);
  if (phase < 0) return null;
  const waiting = run.status === "awaiting_approval";
  const frac = runFraction(run);
  const since = waiting ? run.paused_at ?? run.updated_at : run.started_at;
  const el = fmtDuration(elapsedS(since, now));
  const quietS = elapsedS(run.updated_at, now);
  const stalled = !waiting && quietS !== undefined && quietS > 90;
  const label = waiting ? "Paused" : stalled ? `No update for ${fmtDuration(quietS)}` : PHASES[phase];
  const count =
    !waiting && frac !== undefined && typeof run.photos_processed === "number" && typeof run.n_photos === "number"
      ? `${run.photos_processed} / ${run.n_photos}`
      : "";
  return (
    <div className="w-full min-w-0" aria-live="polite">
      <div className="flex items-baseline justify-between gap-3 text-[0.75rem]">
        <span className={`font-medium ${waiting || stalled ? "text-[#8a5200]" : "text-[var(--kf-text,#1e2327)]"}`}>
          {compact || waiting || stalled ? label : "Progress"}
          {count && <span className="ml-1.5 tabular-nums font-normal text-[var(--kf-muted,#5a646d)]">{count} frames</span>}
        </span>
        {showElapsed && el && (
          <span className="tabular-nums text-[var(--kf-muted,#5a646d)]">
            {el}
          </span>
        )}
      </div>
      <div
        className="relative mt-1 h-1 overflow-hidden rounded-full bg-[var(--kf-surface-2,#eef1f4)]"
        role="progressbar"
        aria-label={`Run progress: ${label}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={frac !== undefined ? Math.round(frac * 100) : undefined}
      >
        {waiting ? (
          <div className="h-full w-full rounded-full bg-[var(--kf-review,#b06800)] opacity-70" />
        ) : frac !== undefined ? (
          <div className="h-full rounded-full bg-[var(--kf-accent,#2457d6)] transition-[width] duration-500" style={{ width: `${frac * 100}%` }} />
        ) : (
          <div className="kf-indeterminate absolute inset-y-0 w-1/3 rounded-full bg-[var(--kf-accent,#2457d6)]" />
        )}
      </div>
      {!compact && (
        // The run timeline shows the stages visibly; this list is for screen readers.
        <ol className="sr-only">
          {PHASES.map((p, i) => (
            <li key={p} className={`flex items-center gap-1.5 ${i === phase ? "font-semibold text-[var(--kf-text,#1e2327)]" : ""}`}>
              <span
                aria-hidden="true"
                className={`h-1.5 w-1.5 rounded-full ${i < phase ? "bg-[var(--kf-keep,#1d8a4e)]" : i === phase ? "bg-[var(--kf-accent,#2457d6)]" : "bg-[var(--kf-border,#dfe4e8)]"}`}
              />
              {p}
              {i < phase && <span className="sr-only"> (done)</span>}
              {i === phase && <span className="sr-only"> (current)</span>}
            </li>
          ))}
        </ol>
      )}
      <style>{`
        @keyframes kf-indet { 0% { left: -35%; } 100% { left: 100%; } }
        .kf-indeterminate { animation: kf-indet 1.4s ease-in-out infinite; }
        @media (prefers-reduced-motion: reduce) { .kf-indeterminate { animation: none; left: 0; width: 100%; opacity: .35; } }
      `}</style>
    </div>
  );
}

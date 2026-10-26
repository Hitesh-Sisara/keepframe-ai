"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { CLOSE_BELOW, fmtGap } from "@/lib/closecall";
import { routes } from "@/lib/routes";
import type { Group } from "@/lib/types";

/** The close call to replay: a burst the agent paused on, the tightest first (else the tightest close one). */
export function pickCloseCall(groups: Group[]): Group | undefined {
  const byMargin = (a: Group, b: Group) => (a.margin ?? 1) - (b.margin ?? 1);
  const asked = groups.filter((g) => g.pending_approval && g.members.length >= 3).sort(byMargin);
  if (asked.length) return asked[0];
  return groups.filter((g) => typeof g.margin === "number" && g.margin < CLOSE_BELOW && g.members.length >= 3).sort(byMargin)[0];
}

const STEP_MS = [0, 2600, 4400, 6400];

/**
 * "Watch it ask": a ~8 s replay of one real close call from the latest run. The frames are scored one by one,
 * the top two are pulled forward with the gap between them, and the request lands in Approvals. Plays once
 * when scrolled into view; with reduced motion it shows the finished storyboard.
 */
export function WatchItAsk({ group, eventId, runId, team, pending }: { group: Group; eventId: string; runId?: string; team: boolean; pending?: number }) {
  const frames = [...group.members].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99)).slice(0, 5);
  // Starts on the finished storyboard (so it is never blank, e.g. below the fold or in a screenshot), then
  // replays once the first time it scrolls into view.
  const [step, setStep] = useState(3);
  const [tick, setTick] = useState(0);
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setStep(3);
      return;
    }
    const el = ref.current;
    if (!el) return;
    const timers: number[] = [];
    const io = new IntersectionObserver(
      ([e]) => {
        if (!e.isIntersecting) return;
        io.disconnect();
        setStep(-1);
        timers.push(window.setTimeout(() => setStep(0), 60));
        timers.push(...STEP_MS.slice(1).map((ms, i) => window.setTimeout(() => setStep(i + 1), ms + 60)));
      },
      { threshold: 0.6 },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      timers.forEach((t) => window.clearTimeout(t));
    };
  }, [tick]);

  const replay = () => {
    setStep(-1);
    setTick((t) => t + 1);
  };

  // "Tied" only when the top two are exactly equal; a sub-threshold gap is "close".
  const gap = typeof group.margin === "number" ? (group.margin === 0 ? "a tie" : fmtGap(group.margin)) : undefined;
  const label = group.group_label ?? "A group";
  const s = Math.max(step, 0);

  return (
    <section ref={ref} aria-labelledby="wia-h" className="kf-card overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-4 px-6 pt-6">
        <div>
          <h2 id="wia-h" className="t-title">
            Close call
          </h2>
          <p className="t-data mt-1 text-[0.875rem] text-[var(--kf-muted)]">
            {label} · {group.size} frames{typeof group.span_s === "number" ? ` · ${group.span_s.toFixed(1)} s` : ""}
          </p>
        </div>
        <button type="button" onClick={replay} className="btn btn-ghost btn-sm" aria-label="Replay the close call">
          <svg aria-hidden="true" viewBox="0 0 16 16" className="h-3.5 w-3.5">
            <path d="M3 8a5 5 0 1 0 1.5-3.6M3 2.5v2.5h2.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Replay
        </button>
      </div>

      {/* Storyboard captions double as the screen-reader version of the animation. */}
      <ol className="mt-5 grid grid-cols-3 gap-2 px-6 text-[0.8125rem]" aria-label="What happens">
        {["Scored", gap === "a tie" ? "Top two tied" : gap ? `Top two ${gap} apart` : "Top two are close", "Asked you"].map((t, i) => (
          <li key={t} className={`flex items-center gap-2 transition-opacity duration-300 motion-reduce:transition-none ${s >= i + 1 || (i === 0 && step >= 0) ? "opacity-100" : "opacity-40"}`}>
            <span className={`t-data grid h-5 w-5 shrink-0 place-items-center rounded-full text-[0.6875rem] font-semibold ${s >= i + 1 || (i === 0 && step >= 0) ? "bg-[var(--kf-accent)] text-white" : "bg-[var(--kf-surface-2)] text-[var(--kf-muted)]"}`}>{i + 1}</span>
            <span className="font-medium">{t}</span>
          </li>
        ))}
      </ol>

      <div aria-hidden="true" className="relative mx-6 mt-4 mb-6 overflow-hidden rounded-[12px] bg-[#0f1420] px-5 pt-5 pb-5">
        <div className="grid grid-cols-5 gap-3">
          {frames.map((f, i) => {
            const top = i < 2;
            const shown = step >= 0;
            const dim = s >= 1 && !top;
            return (
              <figure
                key={f.photo_id}
                className="m-0 transition-[transform,opacity] duration-500 ease-out motion-reduce:transition-none"
                style={{
                  transitionDelay: step === 0 ? `${i * 260}ms` : "0ms",
                  opacity: shown ? 1 : 0,
                  transform: shown ? (s >= 1 && top ? "translateY(-4px) scale(1.04)" : "none") : "translateY(14px) scale(0.96)",
                }}
              >
                <div className={`relative aspect-[3/2] overflow-hidden rounded-[8px] bg-black ring-2 transition-[box-shadow,opacity] duration-500 ${dim ? "opacity-40" : ""} ${s >= 1 && top ? (i === 0 ? "ring-[#5b8cff]" : "ring-[#f0a531]") : "ring-white/10"}`}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={f.proxy_url} alt="" referrerPolicy="no-referrer" loading="lazy" className="h-full w-full object-cover" />
                  {/* scan line while scoring */}
                  {step === 0 && <span className="kf-ov-scan absolute inset-y-0 w-1/3" style={{ animationDelay: `${i * 260}ms` }} />}
                </div>
                <figcaption className="mt-2 flex items-baseline justify-between text-[0.75rem] text-white/85">
                  <span>#{i + 1}</span>
                  <span className={`t-data font-semibold ${s >= 1 && top ? "text-white" : ""}`}>{typeof f.score === "number" ? (f.score * 100).toFixed(1) : "—"}</span>
                </figcaption>
              </figure>
            );
          })}
        </div>

        <div
          className="mt-4 flex min-h-9 flex-wrap items-center justify-center gap-3 transition-[opacity,transform] duration-500 motion-reduce:transition-none"
          style={{ opacity: s >= 2 ? 1 : 0, transform: s >= 2 ? "none" : "translateY(6px)" }}
        >
          <span className="rounded-full bg-[#3d2c12] px-3 py-1.5 text-[0.8125rem] font-semibold text-[#f5cf8e]">
            Under {CLOSE_BELOW * 100} pts → ask
          </span>
          <span
            className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-[0.8125rem] font-semibold text-[#14171c] transition-transform duration-500 motion-reduce:transition-none"
            style={{ transform: s >= 3 ? "scale(1)" : "scale(0.6)", opacity: s >= 3 ? 1 : 0 }}
          >
            <span className="h-2 w-2 rounded-full bg-[var(--kf-review)]" />
            Approvals +1{typeof pending === "number" && pending > 0 ? ` · ${pending} waiting` : ""}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-[var(--kf-border)] px-6 py-4">
        <Link href={routes.approvals(group.group_id)} className="btn btn-primary">
          {team && group.pending_approval ? "Answer" : "View"}
        </Link>
        <Link href={`${routes.group(eventId, group.group_id, runId ?? group.run_id)}#trace`} className="btn btn-secondary">
          Why it asked
        </Link>
      </div>
    </section>
  );
}

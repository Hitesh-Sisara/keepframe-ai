"use client";

import { useMemo, useState } from "react";
import { buildStory, type StoryKind, type StoryStep } from "@/lib/story";
import { Tooltip } from "../ui";
import { PIVOT_INK, PIVOT_RED, PIVOT_RULE, PIVOT_TINT } from "../TraceChain";
import type { Pivot, TraceLine } from "@/lib/types";

const KIND_ICON: Record<StoryKind, string> = {
  start: "M3 8h10M9 4l4 4-4 4",
  sharp: "M8 2.5v2M8 11.5v2M2.5 8h2M11.5 8h2M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
  exposure: "M8 3a5 5 0 1 0 0 10V3z",
  faces: "M8 3.2a2.6 2.6 0 1 0 0 5.2 2.6 2.6 0 0 0 0-5.2zM3.2 13.4c.7-2.4 2.5-3.6 4.8-3.6s4.1 1.2 4.8 3.6",
  eyes: "M1.8 8s2.3-4.2 6.2-4.2S14.2 8 14.2 8s-2.3 4.2-6.2 4.2S1.8 8 1.8 8z",
  quality: "M3 12.5l3-4 2.5 2.5L13 4.5",
  rank: "M4 13V8M8 13V3.5M12 13V6",
  ask: "M8 2.8a3 3 0 1 1 0 6M8 11.8v.4M5.4 5.2A2.8 2.8 0 0 1 8 2.8",
  answer: "M3.5 8.4l3 3 6-6.6",
  write: "M3 13h10M4.5 10.5 11 4l1.5 1.5L6 12H4.5z",
  other: "M4 8h8",
};

const TONE: Record<StoryStep["tone"], { ring: string; text: string }> = {
  normal: { ring: "var(--kf-border-strong)", text: "var(--kf-text)" },
  warn: { ring: "var(--kf-review)", text: "var(--kf-review)" },
  ask: { ring: "var(--kf-accent)", text: "var(--kf-accent-text)" },
};

/** The trace as short steps, one line each. Plan changes get one compact marker; the reason is in its tooltip.
 *  "Frame N" mentions light up the frame's thumbnail in the filmstrip above. */
export function TraceStory({
  lines,
  pivots,
  names,
  hot,
  onHot,
  onShowRaw,
  emptyText,
}: {
  lines: TraceLine[];
  pivots: Pivot[];
  names: Record<string, string>;
  hot?: string;
  onHot: (id?: string) => void;
  onShowRaw: (seq: number) => void;
  /** Shown when there are no steps and the run is over (the agent never reached this burst). */
  emptyText?: string;
}) {
  const steps = useMemo(() => buildStory(lines, pivots, names), [lines, pivots, names]);
  const byName = useMemo(() => Object.fromEntries(Object.entries(names).map(([id, n]) => [n, id])), [names]);
  const sortedPivots = useMemo(() => [...pivots].sort((a, b) => a.result_seq - b.result_seq), [pivots]);
  const [all, setAll] = useState(false);
  if (!steps.length) return <p className="t-small text-[var(--kf-muted)]">{emptyText ?? "No steps yet."}</p>;
  const rich = (t: string) => <Mentions text={t} byName={byName} hot={hot} onHot={onHot} />;
  // Checks that passed are routine: hidden until asked for, so the steps that decided the burst stand alone.
  const notable = steps.filter((s) => s.pivot || s.tone !== "normal" || s.kind === "rank" || s.kind === "answer");
  const shown = all || notable.length === 0 ? steps : notable;

  return (
    <>
    <ol className="relative flex flex-col before:absolute before:top-3 before:bottom-3 before:left-[13px] before:w-px before:bg-[var(--kf-border)]">
      {shown.map((s, i) => {
        const pv = s.pivot ? sortedPivots[s.pivot.index - 1] : undefined;
        const t = TONE[s.pivot ? "warn" : s.tone];
        const label = s.short[0].toUpperCase() + s.short.slice(1);
        return (
          <li key={i} className="group/step relative grid grid-cols-[28px_1fr_auto] items-center gap-x-3 rounded-[8px] py-1 pr-1" title={s.text}>
            <span
              className="relative z-[1] grid h-[26px] w-[26px] place-items-center rounded-full bg-[var(--kf-surface)]"
              style={{ boxShadow: `inset 0 0 0 1.5px ${s.pivot ? PIVOT_RED : t.ring}`, color: s.pivot ? PIVOT_RED : t.text }}
            >
              <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
                <path d={KIND_ICON[s.kind]} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <p className="flex min-w-0 flex-wrap items-center gap-x-2 text-[0.875rem] leading-snug" style={{ color: s.pivot || s.tone !== "normal" ? t.text : "var(--kf-text)" }}>
              <span>{rich(label)}</span>
              {s.pivot && pv && (
                <Tooltip label={pivotWhy(pv.kind)} side="top">
                  <span tabIndex={0} className="inline-flex h-5 cursor-help items-center rounded-full px-1.5 text-[0.6875rem] font-semibold ring-1"
                    style={{ background: PIVOT_TINT, color: PIVOT_INK, ["--tw-ring-color" as string]: PIVOT_RULE }}>
                    Plan changed
                  </span>
                </Tooltip>
              )}
            </p>
            <button
              type="button"
              onClick={() => onShowRaw(s.seqs[0])}
              aria-label={`Show in timeline: ${label}`}
              title="Show in timeline"
              className="grid h-6 w-6 place-items-center rounded-[6px] text-[var(--kf-muted)] opacity-0 transition-opacity group-hover/step:opacity-100 hover:bg-[var(--kf-surface-2)] hover:text-[var(--kf-text)] focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-[var(--kf-accent)]"
            >
              <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
                <path d="M3 4h10M3 8h10M3 12h6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            </button>
          </li>
        );
      })}
    </ol>
    {notable.length > 0 && notable.length < steps.length && (
      <button type="button" aria-expanded={all} onClick={() => setAll((a) => !a)} className="mt-1.5 ml-10 text-[0.75rem] font-medium text-[var(--kf-accent-text)] hover:underline">
        {all ? "Key steps only" : `All ${steps.length} steps`}
      </button>
    )}
    </>
  );
}

function pivotWhy(kind: string): string {
  if (kind === "sharpness_rescore") return "Borderline on the preview, so re-checked full size";
  if (kind === "eyes_compare_in_group") return "A blink, so compared the whole burst";
  if (kind === "small_margin_to_human") return "Top two under 5 pts apart";
  return "Unexpected result";
}

/** Text where "Frame N" becomes a hover/focus target that lights the frame's thumbnail. */
function Mentions({ text, byName, hot, onHot }: { text: string; byName: Record<string, string>; hot?: string; onHot: (id?: string) => void }) {
  const parts = text.split(/(Frame \d+)/g);
  return (
    <>
      {parts.map((p, i) => {
        const id = byName[p];
        if (!id) return <span key={i}>{p}</span>;
        return (
          <button
            key={i}
            type="button"
            onMouseEnter={() => onHot(id)}
            onMouseLeave={() => onHot(undefined)}
            onFocus={() => onHot(id)}
            onBlur={() => onHot(undefined)}
            className={`rounded-[4px] px-0.5 font-semibold underline decoration-dotted underline-offset-[3px] focus-visible:outline-2 focus-visible:outline-[var(--kf-accent)] ${
              hot === id ? "bg-[var(--kf-accent-soft)] text-[var(--kf-accent-text)]" : ""
            }`}
          >
            {p}
          </button>
        );
      })}
    </>
  );
}

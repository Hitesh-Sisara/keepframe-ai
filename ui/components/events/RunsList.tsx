"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { fmtNum, fmtTime } from "@/lib/format";
import { elapsedS, failureText, fmtAgo, fmtDuration, isLive, runOrdinals, runTone, type RunTone, type RunX } from "@/lib/runs";
import { RunProgress, RunStatusPill, useNow } from "./RunStatus";
import { card, focusRing } from "./tokens";
import { VerdictBar } from "./VerdictBar";
import { routes } from "@/lib/routes";

type Filter = "all" | "active" | "done" | "failed";
const FILTERS: { k: Filter; word: string; match: (t: RunTone) => boolean }[] = [
  { k: "all", word: "All", match: () => true },
  { k: "active", word: "Active", match: (t) => t === "running" || t === "waiting" },
  { k: "done", word: "Done", match: (t) => t === "done" },
  { k: "failed", word: "Failed", match: (t) => t === "failed" || t === "cancelled" },
];

// One column template for header and rows, so headers sit exactly over their values.
// Full table from xl (1280) only: below that, with the icon rail, the six columns no longer fit and fold into
// a two-line row instead of clipping.
const COLS_LG = "xl:grid-cols-[minmax(220px,1.4fr)_136px_72px_minmax(200px,1.3fr)_132px_16px]";
const COLS = COLS_LG;

const KIND_WORD: Record<string, string> = { deployed: "Cloud", agentcore: "Cloud" };

export function RunsList({ runs, names }: { runs: RunX[]; names: Record<string, string> }) {
  const [filter, setFilter] = useState<Filter>("all");
  const ordinals = useMemo(() => runOrdinals(runs), [runs]);
  const anyLive = runs.some((r) => isLive(r.status));
  const now = useNow(anyLive, 1000);
  const counts = Object.fromEntries(FILTERS.map((f) => [f.k, runs.filter((r) => f.match(runTone(r.status))).length])) as Record<Filter, number>;
  const shown = runs.filter((r) => FILTERS.find((f) => f.k === filter)!.match(runTone(r.status)));
  const multiEvent = new Set(runs.map((r) => r.event_id)).size > 1 || Object.keys(names).length > 1;

  return (
    <div>
      {runs.length > 3 && (
      <div role="group" aria-label="Filter runs" className="mb-3 inline-flex rounded-[8px] border border-[var(--kf-border,#dfe4e8)] bg-[var(--kf-surface-2,#f5f7f9)] p-0.5">
        {FILTERS.map((f) => (
          <button
            key={f.k}
            type="button"
            aria-pressed={filter === f.k}
            onClick={() => setFilter(f.k)}
            className={`inline-flex items-center gap-1.5 rounded-[6px] px-3 py-1 text-[0.8rem] font-medium transition-colors motion-reduce:transition-none ${focusRing} ${
              filter === f.k
                ? "bg-[var(--kf-surface,#fff)] text-[var(--kf-text,#1e2327)] shadow-[0_1px_2px_rgb(16_24_32/0.1),0_0_0_1px_rgb(16_24_32/0.05)]"
                : "text-[var(--kf-muted,#5a646d)] hover:text-[var(--kf-text,#1e2327)]"
            }`}
          >
            {f.word}
            <span className="tabular-nums text-[0.75rem] text-[var(--kf-text,#1e2327)] opacity-70">{counts[f.k]}</span>
          </button>
        ))}
      </div>
      )}

      <div className={`${card} overflow-hidden`}>
        <div
          aria-hidden="true"
          className={`hidden ${COLS} items-center gap-x-7 border-b border-[var(--kf-border,#dfe4e8)] bg-[var(--kf-surface-2,#f5f7f9)] px-5 py-2 text-[0.75rem] font-semibold tracking-[0.04em] text-[var(--kf-muted,#5a646d)] uppercase xl:grid`}
        >
          <span>Run</span>
          <span>Status</span>
          <span className="text-right">Duration</span>
          <span>Verdicts</span>
          <span className="text-right">Agent</span>
          <span />
        </div>
        {shown.length === 0 ? (
          <p className="px-5 py-10 text-center text-[0.875rem] text-[var(--kf-muted,#5a646d)]">No {filter === "all" ? "" : `${FILTERS.find((f) => f.k === filter)!.word.toLowerCase()} `}runs.</p>
        ) : (
          <ul>
            {shown.map((r) => (
              <RunRow key={r.run_id} run={r} n={ordinals[r.run_id]} eventName={names[r.event_id] ?? "Event"} showEvent={multiEvent} now={now} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function RunRow({ run, n, eventName, showEvent, now }: { run: RunX; n?: number; eventName: string; showEvent: boolean; now: number }) {
  const live = isLive(run.status);
  const tone = runTone(run.status);
  const href = routes.event(run.event_id, run.run_id);
  const title = `Run ${n ?? ""}`.trim();
  const dur = typeof run.duration_s === "number" ? fmtDuration(run.duration_s) : live ? fmtDuration(elapsedS(run.started_at, now)) : "";
  const noLlm = run.llm_provider === "none";
  const asked = run.escalations?.agent ?? run.escalation_count;
  const kind = run.run_kind ? KIND_WORD[run.run_kind] : undefined;
  const why = tone === "failed" ? failureText(run) : "";

  return (
    <li className="group relative border-b border-[var(--kf-border,#dfe4e8)] last:border-0 hover:bg-[var(--kf-surface-2,#f7f9fa)]">
      <div className={`grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-7 gap-y-2 px-5 py-3.5 ${COLS_LG}`}>
        <div className="min-w-0">
          <p className="flex min-w-0 items-baseline gap-2">
            <Link
              href={href}
              className={`truncate rounded-[4px] text-[0.92rem] font-semibold text-[var(--kf-text,#1e2327)] after:absolute after:inset-0 after:content-[''] ${focusRing}`}
            >
              {showEvent ? `${eventName} · ${title}` : title}
            </Link>
          </p>
          <p className="mt-0.5 text-[0.78rem] text-[var(--kf-muted,#5a646d)]">
            <span title={fmtTime(run.started_at)}>{fmtAgo(run.started_at, now) || fmtTime(run.started_at)}</span>
            {kind && <> · {kind}</>}
            {typeof run.n_photos === "number" && <> · {fmtNum(run.n_photos)} photos</>}
          </p>
        </div>

        <div className="justify-self-end xl:justify-self-start">
          <RunStatusPill status={run.status} />
        </div>

        <div className="tabular-nums hidden text-right text-[0.85rem] text-[var(--kf-text,#1e2327)] xl:block">
          {dur || <span className="text-[var(--kf-muted,#5a646d)]">—</span>}
        </div>

        <div className="col-span-2 min-w-0 xl:col-span-1">
          {live ? (
            <RunProgress run={run} compact showElapsed={false} />
          ) : tone === "failed" ? (
            <p className="truncate text-[0.8rem] text-[var(--kf-muted,#5a646d)]">
              <span className="font-medium text-[#a8232f]">{why || "Stopped early"}</span>
            </p>
          ) : (
            <VerdictBar counts={run.verdict_counts} legend="numbers" emptyText="No verdicts" />
          )}
        </div>

        <div className="hidden text-right text-[0.78rem] leading-snug xl:block">
          {noLlm ? (
            <span className="text-[var(--kf-muted,#5a646d)]">No agent</span>
          ) : !run.tool_call_count ? (
            <span className="text-[var(--kf-muted,#5a646d)]">{live ? "Pending" : "—"}</span>
          ) : (
            <>
              <span className="tabular-nums block text-[0.85rem] font-semibold text-[var(--kf-text,#1e2327)]">
                {fmtNum(run.tool_call_count ?? 0)}
                <span className="ml-1 text-[0.75rem] font-normal text-[var(--kf-muted,#5a646d)]">steps</span>
              </span>
              {typeof asked === "number" && asked > 0 && (
                <span className="tabular-nums block text-[var(--kf-muted,#5a646d)]">{asked} close calls raised</span>
              )}
            </>
          )}
        </div>

        <svg viewBox="0 0 16 16" className="hidden h-4 w-4 text-[var(--kf-muted,#5a646d)] opacity-0 transition-opacity group-hover:opacity-100 xl:block" aria-hidden="true">
          <path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>

        {/* Below lg the duration and agent columns fold into one line. */}
        <p className="col-span-2 -mt-1 flex gap-3 text-[0.75rem] text-[var(--kf-muted,#5a646d)] xl:hidden">
          {dur && <span className="tabular-nums">{dur}</span>}
          {!noLlm && Boolean(run.tool_call_count) && <span className="tabular-nums">{run.tool_call_count} agent steps</span>}
          {typeof asked === "number" && asked > 0 && <span className="tabular-nums">asked for review {asked}×</span>}
        </p>
      </div>
    </li>
  );
}

export function RunsSkeleton() {
  return (
    <div aria-hidden="true" className={`${card} overflow-hidden`}>
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-center gap-6 border-b border-[var(--kf-border,#dfe4e8)] px-5 py-4 last:border-0">
          <div className="flex-1 space-y-2">
            <div className="h-3.5 w-48 rounded bg-[var(--kf-surface-2,#eef1f4)]" />
            <div className="h-3 w-32 rounded bg-[var(--kf-surface-2,#eef1f4)]" />
          </div>
          <div className="h-5 w-20 rounded-full bg-[var(--kf-surface-2,#eef1f4)]" />
          <div className="hidden h-1.5 w-48 rounded-full bg-[var(--kf-surface-2,#eef1f4)] lg:block" />
        </div>
      ))}
    </div>
  );
}

"use client";

import Link from "next/link";
import { fmtNum } from "@/lib/format";
import { pivotExamples, pivotTitle, toolMix, toolWord } from "@/lib/overview";
import { routes } from "@/lib/routes";
import type { RunX } from "@/lib/runs";
import type { Group, Pivot, TraceLine } from "@/lib/types";

/** "What the agent did": tool calls, plan changes, questions asked, the tool mix, and two real plan changes
 *  that open the trace of the burst where they happened. */
export function AgentCard({
  run,
  lines,
  pivots,
  groups,
  eventId,
}: {
  run: RunX;
  lines?: TraceLine[];
  pivots?: Pivot[];
  groups?: Group[];
  eventId: string;
}) {
  const asked = run.escalations?.agent ?? run.escalation_count;
  const mix = lines ? toolMix(lines).slice(0, 6) : [];
  const maxN = mix[0]?.n ?? 1;
  const examples = pivots && lines ? pivotExamples(pivots, lines, 2) : [];
  const label = (gid?: string) => groups?.find((g) => g.group_id === gid)?.group_label;
  const loading = !lines && !pivots;

  return (
    <section aria-labelledby="agent-h" className="kf-card flex flex-col p-6">
      <h2 id="agent-h" className="t-title">
        {!pivots ? "Agent" : pivots.length === 0 ? "Agent kept its plan" : `Agent changed plan ${pivots.length}×`}
      </h2>

      <dl className="mt-5 grid grid-cols-2 gap-4">
        <Kpi k="Steps" v={fmtNum(run.tool_call_count ?? lines?.filter((l) => l.type === "tool_call").length ?? 0)} />
        <Kpi k="Plan changes" v={pivots ? fmtNum(pivots.length) : undefined} loading={loading} />
      </dl>

      {mix.length > 0 && (
        <div className="mt-6">
          <p className="mb-2 text-[0.8125rem] font-semibold">Tools</p>
          <ul className="grid gap-1.5">
            {mix.map((m) => (
              <li key={m.tool} className="grid grid-cols-[8.5rem_minmax(0,1fr)_2.5rem] items-center gap-3 text-[0.8125rem]">
                <span className="truncate text-[var(--kf-muted)]">{toolWord(m.tool)}</span>
                <span aria-hidden="true" className="h-1.5 overflow-hidden rounded-full bg-[var(--kf-surface-2)]">
                  <span className="kf-ov-grow block h-full rounded-full bg-[var(--kf-accent)]" style={{ width: `${(m.n / maxN) * 100}%` }} />
                </span>
                <span className="t-data text-right font-medium">{m.n}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-6">
        <p className="mb-2 text-[0.8125rem] font-semibold">Examples</p>
        {loading ? (
          <div className="grid gap-2" aria-hidden="true">
            <div className="kf-skeleton h-16" />
            <div className="kf-skeleton h-16" />
          </div>
        ) : examples.length === 0 ? (
          <p className="text-[0.8125rem] text-[var(--kf-muted)]">None.</p>
        ) : (
          <ul className="grid gap-2">
            {examples.map(({ pivot, groupId }) => (
              <li key={pivot.result_seq}>
                <Link
                  href={groupId ? `${routes.group(eventId, groupId, run.run_id)}#trace` : routes.event(eventId, run.run_id)}
                  className="group flex items-start gap-3 rounded-[var(--kf-radius)] border border-[var(--kf-border)] px-3.5 py-3 transition-colors hover:border-[var(--kf-border-strong)] hover:bg-[var(--kf-surface-2)]"
                >
                  <span aria-hidden="true" className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[var(--kf-accent-soft)] text-[var(--kf-accent-text)]">
                    <svg viewBox="0 0 16 16" className="h-4 w-4">
                      <path d="M3 4h5a3 3 0 0 1 0 6H5m0 0 2-2m-2 2 2 2" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </span>
                  <span className="min-w-0 flex-1" title={pivot.summary}>
                    <span className="flex items-baseline justify-between gap-2">
                      <span className="text-[0.875rem] font-semibold">{pivotTitle(pivot.kind)}</span>
                      {label(groupId) && <span className="shrink-0 text-[0.75rem] text-[var(--kf-muted)]">{label(groupId)}</span>}
                    </span>
                    <span className="sr-only">{pivot.summary}</span>
                  </span>
                  <svg aria-hidden="true" viewBox="0 0 16 16" className="mt-1.5 h-4 w-4 shrink-0 text-[var(--kf-muted)] transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none">
                    <path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function Kpi({ k, v, loading }: { k: string; v?: string; loading?: boolean }) {
  return (
    <div className="min-w-0 rounded-[var(--kf-radius)] bg-[var(--kf-surface-2)] px-3.5 py-3">
      <dt className="text-[0.75rem] font-medium text-[var(--kf-muted)]">{k}</dt>
      <dd className="t-data mt-1 text-[1.5rem] leading-none font-semibold tracking-[-0.02em]">
        {v ?? (loading ? <span className="kf-skeleton inline-block h-6 w-10 align-middle" aria-hidden="true" /> : "—")}
      </dd>
    </div>
  );
}

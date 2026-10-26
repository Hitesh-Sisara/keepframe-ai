"use client";

import Link from "next/link";
import { useApi } from "@/lib/api";
import { approvalsSourceText, lastAnswerText } from "@/lib/decision";
import { agentHostText, agentLiveText, cleanText, fmtNum, fmtTime } from "@/lib/format";
import { elapsedS, failureText, fmtDuration, isLive, runOrdinals, runTone, TONE_WORD, type RunTone, type RunX } from "@/lib/runs";
import { routes } from "@/lib/routes";

const DOT: Record<RunTone, string> = {
  running: "bg-[var(--kf-accent,#2457d6)]",
  waiting: "bg-[var(--kf-review,#b06800)]",
  done: "bg-[var(--kf-keep,#1d8a4e)]",
  failed: "bg-[var(--kf-reject,#c8323f)]",
  cancelled: "bg-[var(--kf-muted,#5a646d)]",
};
import type { Page, RunAgent } from "@/lib/types";
import { RunProgress, RunStatusPill, useNow } from "./RunStatus";
import { card, focusRing } from "./tokens";
import { RunTimeline } from "@/components/overview/Timeline";

const PROVIDER_WORD: Record<string, string> = {
  bedrock: "Claude on Amazon Bedrock",
  anthropic: "Claude via an Anthropic-compatible API",
  fake: "Built-in planner (no LLM calls)",
  none: "None, deterministic worker",
};

/** The selected run for an event: status, live progress, the headline numbers, a run switcher, and the
 *  build/model details folded behind a disclosure. */
export function RunPanel({
  run,
  ordinal,
  eventId,
  allRuns,
}: {
  run: RunX;
  ordinal?: number;
  eventId: string;
  allRuns?: Page<RunX>;
}) {
  const ordinals = runOrdinals(allRuns?.items ?? []);
  const live = isLive(run.status);
  const now = useNow(live);
  const agent = useApi<RunAgent>(live ? `/runs/${encodeURIComponent(run.run_id)}/agent` : null, undefined, 10000);
  const host = agentHostText(run);
  const liveText = agentLiveText(run.status, agent.data?.live);
  const asked = run.escalations?.agent ?? run.escalation_count;
  const answer = lastAnswerText(run.last_answer_source);
  const approvals = approvalsSourceText(run.approvals_source);
  const others = (allRuns?.items ?? []).filter((r) => r.run_id !== run.run_id).slice(0, 5);

  const stats: { k: string; v: string; sub?: string }[] = [
    { k: "Started", v: fmtTime(run.started_at) || "—" },
    { k: live ? "Elapsed" : "Duration", v: typeof run.duration_s === "number" ? fmtDuration(run.duration_s) : live ? fmtDuration(elapsedS(run.started_at, now)) || "—" : "—" },
    ...(run.llm_provider !== "none" && !(live && !run.tool_call_count)
      ? [
          { k: "Steps", v: typeof run.tool_call_count === "number" ? fmtNum(run.tool_call_count) : "—" },
          { k: "Close calls", v: typeof asked === "number" ? `${fmtNum(asked)} raised` : "—", sub: approvals ?? undefined },
        ]
      : run.llm_provider === "none"
        ? [{ k: "Agent", v: "Not used" }]
        : [{ k: "Agent", v: "Starts after scoring" }]),
    ...(typeof run.cost_estimate_usd === "number" ? [{ k: "Model cost", v: `$${run.cost_estimate_usd.toFixed(3)}` }] : []),
  ];

  return (
    <section aria-label="Run" className={`${card} mb-8`}>
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b border-[var(--kf-border,#dfe4e8)] px-5 py-4">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <h2 className="text-[1rem] font-semibold text-[var(--kf-text,#1e2327)]">Run {ordinal ?? ""}</h2>
          <RunStatusPill status={run.status} />
          {run.status === "failed" && failureText(run) && (
            <span className="text-[0.82rem] font-medium text-[#a8232f]">{failureText(run)}</span>
          )}
        </div>
        {others.length > 0 && (
          <nav aria-label="Other runs of this event" className="flex flex-wrap items-center gap-1.5 text-[0.78rem]">
            {others.map((r) => (
              <Link
                key={r.run_id}
                href={routes.event(eventId, r.run_id)}
                aria-label={`Run ${ordinals[r.run_id] ?? ""}, ${fmtTime(r.started_at)}, ${TONE_WORD[runTone(r.status)]}`}
                className={`tabular-nums inline-flex items-center gap-1.5 rounded-[6px] border border-[var(--kf-border,#dfe4e8)] px-2 py-0.5 text-[var(--kf-text,#1e2327)] hover:bg-[var(--kf-surface-2,#f5f7f9)] ${focusRing}`}
                title={`${fmtTime(r.started_at)}, ${r.status}`}
              >
                <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${DOT[runTone(r.status)]}`} />
                Run {ordinals[r.run_id] ?? ""}
              </Link>
            ))}
          </nav>
        )}
      </div>

      {live && (
        <div className="border-b border-[var(--kf-border,#dfe4e8)] px-5 py-4">
          <RunProgress run={run} showElapsed={false} />
        </div>
      )}

      <div className="border-b border-[var(--kf-border,#dfe4e8)] px-5 py-4">
        <RunTimeline run={run} now={now} />
      </div>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 px-5 py-4 sm:grid-cols-3 lg:grid-cols-5">
        {stats.map((s) => (
          <div key={s.k} className="min-w-0">
            <dt className="text-[0.75rem] font-medium tracking-[0.04em] text-[var(--kf-muted,#5a646d)] uppercase">{s.k}</dt>
            <dd className="tabular-nums mt-0.5 truncate text-[0.95rem] font-semibold text-[var(--kf-text,#1e2327)]">{s.v}</dd>
            {s.sub && <dd className="mt-0.5 text-[0.75rem] font-medium text-[#8a5200]">{s.sub}</dd>}
          </div>
        ))}
      </dl>

      <details className="group/d border-t border-[var(--kf-border,#dfe4e8)]">
        <summary
          className={`flex cursor-pointer list-none items-center gap-2 rounded-b-[var(--kf-radius,10px)] px-5 py-2.5 text-[0.8rem] font-medium text-[var(--kf-muted,#5a646d)] select-none hover:text-[var(--kf-text,#1e2327)] [&::-webkit-details-marker]:hidden ${focusRing}`}
        >
          <svg viewBox="0 0 12 12" className="h-3 w-3 transition-transform group-open/d:rotate-90 motion-reduce:transition-none" aria-hidden="true">
            <path d="M4.5 2.5L8 6l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Details
        </summary>
        <dl className="grid gap-x-8 gap-y-2 px-5 pt-1 pb-4 text-[0.8rem] sm:grid-cols-2">
          <Detail k="OpenCV" v={run.opencv_version ? `${run.opencv_version}, stock wheel` : undefined} />
          <Detail k="HAL" v={run.opencv_hal} mono />
          <Detail k="Machine" v={run.instance_type} mono />
          <Detail k="Agent host" v={[host, liveText].filter(Boolean).join(", ") || undefined} live={live && Boolean(liveText)} />
          <Detail k="Model" v={run.llm_provider ? PROVIDER_WORD[run.llm_provider] ?? run.llm_provider : undefined} />
          <Detail k="Planner" v={run.planner_model ? cleanText(run.planner_model) : undefined} mono />
          <Detail k="Reasons" v={run.reasons_model ? cleanText(run.reasons_model) : undefined} mono />
          <Detail k="Last answer" v={answer ?? undefined} />
          <Detail k="Thresholds" v={run.thresholds_sha ? run.thresholds_sha.slice(0, 6) : undefined} mono />
        </dl>
      </details>
    </section>
  );
}

function Detail({ k, v, mono = false, live = false }: { k: string; v?: string; mono?: boolean; live?: boolean }) {
  if (!v) return null;
  return (
    <div className="flex min-w-0 gap-3">
      <dt className="w-24 shrink-0 text-[var(--kf-muted,#5a646d)]">{k}</dt>
      <dd className={`min-w-0 break-words text-[var(--kf-text,#1e2327)] ${mono ? "font-mono text-[0.76rem]" : ""}`} aria-live={live ? "polite" : undefined}>
        {v}
      </dd>
    </div>
  );
}

export function RunPanelSkeleton() {
  return (
    <div aria-hidden="true" className={`${card} mb-8 space-y-4 p-5`}>
      <div className="h-4 w-40 rounded bg-[var(--kf-surface-2,#eef1f4)]" />
      <div className="grid grid-cols-4 gap-6">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="space-y-2">
            <div className="h-2.5 w-14 rounded bg-[var(--kf-surface-2,#eef1f4)]" />
            <div className="h-4 w-20 rounded bg-[var(--kf-surface-2,#eef1f4)]" />
          </div>
        ))}
      </div>
    </div>
  );
}

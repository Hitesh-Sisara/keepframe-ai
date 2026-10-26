"use client";

import Link from "next/link";
import { fmtNum } from "@/lib/format";
import { routes } from "@/lib/routes";
import { computeSeconds } from "@/lib/overview";
import { tally, type RunX } from "@/lib/runs";
import { fmtSecs } from "./Timeline";

const SEG = [
  { k: "keep", word: "Keep", color: "var(--kf-keep)", soft: "var(--kf-keep-soft)" },
  { k: "review", word: "Review", color: "var(--kf-review)", soft: "var(--kf-review-soft)" },
  { k: "reject", word: "Reject", color: "var(--kf-reject)", soft: "var(--kf-reject-soft)" },
] as const;

/** Big keep / review / reject bar with the share and count of each, labelled in text (never colour alone). */
export function VerdictStack({ counts, reviewAsked }: { counts?: RunX["verdict_counts"]; reviewAsked?: number }) {
  const t = tally(counts);
  if (!t.total) return <div className="h-4 rounded-full bg-[var(--kf-surface-2)]" />;
  // Review splits into photos inside a close call the agent asked about, and photos flagged on their own.
  const asked = typeof reviewAsked === "number" ? Math.min(reviewAsked, t.review) : undefined;
  const flagged = asked !== undefined ? t.review - asked : undefined;
  return (
    <div>
      <div role="img" aria-label={`${t.keep} keep, ${t.review} review, ${t.reject} reject`} className="flex h-4 w-full gap-[3px] overflow-hidden rounded-full">
        <span className="kf-ov-grow h-full" style={{ flexGrow: t.keep, flexBasis: 0, background: SEG[0].color }} />
        {asked !== undefined && flagged !== undefined ? (
          <>
            {asked > 0 && <span className="kf-ov-grow h-full" style={{ flexGrow: asked, flexBasis: 0, background: SEG[1].color }} />}
            {flagged > 0 && <span className="kf-ov-grow kf-ov-hatch h-full" style={{ flexGrow: flagged, flexBasis: 0 }} />}
          </>
        ) : (
          t.review > 0 && <span className="kf-ov-grow h-full" style={{ flexGrow: t.review, flexBasis: 0, background: SEG[1].color }} />
        )}
        {t.reject > 0 && <span className="kf-ov-grow h-full" style={{ flexGrow: t.reject, flexBasis: 0, background: SEG[2].color }} />}
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-4">
        {SEG.map((s) => (
          <div key={s.k} className="min-w-0">
            <dt className="flex items-center gap-1.5 text-[0.8125rem] font-medium text-[var(--kf-muted)]">
              <VerdictGlyph k={s.k} />
              {s.word}
            </dt>
            <dd className="mt-0.5 flex items-baseline gap-1.5">
              <span className="t-data text-[1.5rem] leading-none font-semibold tracking-[-0.02em]">{fmtNum(t[s.k])}</span>
              <span className="t-data text-[0.8125rem] text-[var(--kf-muted)]">{Math.round((t[s.k] / t.total) * 100)}%</span>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** ✓ ? ✕ shapes so the verdict never rests on colour alone. */
export function VerdictGlyph({ k }: { k: "keep" | "review" | "reject" }) {
  const color = k === "keep" ? "var(--kf-keep)" : k === "review" ? "var(--kf-review)" : "var(--kf-reject)";
  return (
    <span aria-hidden="true" className="grid h-4 w-4 place-items-center rounded-full" style={{ background: color }}>
      <svg viewBox="0 0 12 12" className="h-2.5 w-2.5">
        {k === "keep" && <path d="M3 6.2l2 2 4-4.4" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />}
        {k === "review" && <path d="M4.4 4.4a1.7 1.7 0 1 1 2.3 1.6c-.5.2-.7.5-.7 1v.3M6 9.2v.01" fill="none" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" />}
        {k === "reject" && <path d="M3.8 3.8l4.4 4.4M8.2 3.8l-4.4 4.4" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />}
      </svg>
    </span>
  );
}

/**
 * Overview hero for the latest run: photos culled and the compute it took, how long the run took end to end
 * (with the stage timeline showing where that time went), the verdict split, and the one thing a person has to
 * do, as the primary action.
 */
export function Hero({
  run,
  eventName,
  eventId,
  waiting,
  reviewInAsked,
  team,
  timeline,
  runLabel,
}: {
  runLabel?: string;
  run: RunX & { worker_wall_s?: number; agent_wall_s?: number };
  eventName: string;
  eventId: string;
  /** Close calls (groups) waiting for a person. */
  waiting?: number;
  /** Photos marked review that sit inside the close calls the agent asked about. */
  reviewInAsked?: number;
  team: boolean;
  timeline?: React.ReactNode;
}) {
  const t = tally(run.verdict_counts);
  const photos = run.n_photos ?? t.total;
  const auto = t.total ? Math.round(((t.keep + t.reject) / t.total) * 100) : undefined;
  const compute = computeSeconds(run);
  const perSec = compute && photos ? photos / compute : undefined;
  const raised = run.escalations?.agent ?? run.escalation_count;

  return (
    <section aria-labelledby="hero-h" className="kf-card kf-ov-hero relative overflow-hidden">
      <div className="grid gap-8 p-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:p-8">
        <div className="min-w-0">
          <p className="t-eyebrow">{eventName}{runLabel ? ` · ${runLabel}` : ""}</p>
          <h2 id="hero-h" className="mt-2 text-[2.25rem] leading-[1.05] font-semibold tracking-[-0.035em] text-balance sm:text-[2.75rem]">
            <span className="t-data">{fmtNum(photos)}</span> photos
            {typeof compute === "number" && (
              <>
                {" "}
                <span aria-hidden="true" className="text-[var(--kf-faint)]">·</span>{" "}
                <span className="t-data text-[var(--kf-accent)]" title="Scoring plus the agent">
                  {fmtSecs(compute)}
                </span>
                <span className="text-[0.5em] font-medium tracking-normal text-[var(--kf-muted)]"> compute</span>
              </>
            )}
          </h2>
          <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[0.9375rem] text-[var(--kf-muted)]">
            {typeof raised === "number" && (
              <li title="Close calls the agent raised in this run, the photos inside them, and how many still wait for a person">
                <span className="t-data font-semibold text-[var(--kf-text)]">{raised}</span> close calls raised
                {typeof reviewInAsked === "number" && (
                  <>
                    {" · "}
                    <span className="t-data font-semibold text-[var(--kf-text)]">{reviewInAsked}</span> photos
                  </>
                )}
                {typeof waiting === "number" && (
                  <>
                    {" · "}
                    <span className="t-data font-semibold text-[var(--kf-text)]">{waiting}</span> still open
                  </>
                )}
              </li>
            )}
            {perSec ? (
              <li title={`Scoring plus the agent, on ${run.instance_type === "local" || !run.instance_type ? "a laptop" : run.instance_type}`}>
                <span className="t-data font-semibold text-[var(--kf-text)]">{perSec.toFixed(1)}</span> photos/s
              </li>
            ) : null}
          </ul>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            {typeof waiting === "number" && waiting > 0 ? (
              <Link href={routes.approvals()} className={`btn btn-lg ${team ? "btn-primary kf-ov-cta" : "btn-secondary"}`}>
                {team && (
                  <span aria-hidden="true" className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-white/70 motion-reduce:hidden" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-white" />
                  </span>
                )}
                {team ? (
                  <>
                    <span className="t-data">{waiting}</span> close {waiting === 1 ? "call" : "calls"} waiting
                  </>
                ) : (
                  <>
                    Waiting on the team (<span className="t-data">{waiting}</span> {waiting === 1 ? "group" : "groups"})
                  </>
                )}
                <svg aria-hidden="true" viewBox="0 0 16 16" className="h-4 w-4">
                  <path d="M3.5 8h9m-3.5-3.5L12.5 8 9 11.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </Link>
            ) : typeof waiting === "number" ? (
              <span className="inline-flex h-10 items-center gap-2 text-[0.9375rem] font-medium text-[var(--kf-muted)]">
                <VerdictGlyph k="keep" />
                Nothing waiting
              </span>
            ) : (
              <span className="kf-skeleton h-10 w-64" aria-hidden="true" />
            )}
            <Link href={routes.event(eventId, run.run_id)} className="btn btn-ghost btn-lg">
              Open event
            </Link>
          </div>
        </div>

        <div className="min-w-0 self-center rounded-[var(--kf-radius-lg)] bg-[var(--kf-surface-2)] p-5 ring-1 ring-[var(--kf-border)] ring-inset">
          <p className="mb-3 text-[0.8125rem] font-semibold">Verdicts</p>
          <VerdictStack counts={run.verdict_counts} />
        </div>
      </div>
      {timeline && <div className="border-t border-[var(--kf-border)] px-6 py-5 lg:px-8">{timeline}</div>}
    </section>
  );
}

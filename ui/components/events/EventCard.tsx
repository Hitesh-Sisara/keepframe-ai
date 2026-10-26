"use client";

import Link from "next/link";
import { fmtNum } from "@/lib/format";
import { fmtAgo, isLive, type RunX } from "@/lib/runs";
import type { EventSummary, Run } from "@/lib/types";
import { CoverMosaic, useCoverFrames } from "./CoverMosaic";
import { RunProgress, RunStatusPill, useNow } from "./RunStatus";
import { StartRun } from "./StartRun";
import { btnSecondary, card } from "./tokens";
import { VerdictBar } from "./VerdictBar";
import { routes } from "@/lib/routes";
import { categoryLabel } from "@/lib/category";

function fmtEventDate(d?: string): string {
  if (!d) return "";
  const t = new Date(`${d}T00:00:00`);
  return Number.isNaN(t.getTime()) ? d : t.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function EventCard({
  event,
  latest,
  canWrite,
  typicalS,
  refreshKey,
  onStarted,
  featured = false,
}: {
  /** One event on the page: a wide horizontal card instead of a lone grid tile. */
  featured?: boolean;
  event: EventSummary;
  /** The freshest run for this event from the polled runs list (newer than event.latest_run). */
  latest?: RunX;
  canWrite: boolean;
  typicalS?: number;
  refreshKey: string;
  onStarted: (run: Run) => void;
}) {
  const name = event.name ?? event.label;
  const cover = useCoverFrames(event.event_id, refreshKey);
  const status = latest?.status ?? event.latest_run?.status;
  const live = isLive(status);
  const now = useNow(true, 30_000);
  const href = routes.event(event.event_id);
  const meta = [categoryLabel(event.category), fmtEventDate(event.event_date)].filter(Boolean);
  const reviewN = event.verdict_counts?.review ?? 0;

  return (
    <li className={`${card} group relative flex ${featured ? "flex-col md:flex-row" : "flex-col"} transition-shadow duration-150 hover:shadow-[0_8px_24px_-10px_rgb(16_24_32/0.25),0_0_0_1px_rgb(16_24_32/0.08)] motion-reduce:transition-none`}>
      <Link href={href} className={`block overflow-hidden focus-visible:outline-none ${featured ? "rounded-t-[var(--kf-radius,10px)] md:relative md:min-h-[280px] md:w-[46%] md:max-w-[620px] md:shrink-0 md:rounded-l-[var(--kf-radius,10px)] md:rounded-tr-none" : "rounded-t-[var(--kf-radius,10px)]"}`} tabIndex={-1} aria-hidden="true">
        <CoverMosaic frames={cover.frames} loading={cover.loading} title={name} fill={featured} />
      </Link>
      <div className={`flex min-w-0 flex-1 flex-col gap-4 ${featured ? "p-6 md:p-7" : "p-5"}`}>
        <div className="min-w-0">
          <div className="flex items-center justify-between gap-3">
          <h3 className={`min-w-0 truncate leading-tight ${featured ? "text-[1.35rem]" : "text-[1.06rem]"} font-semibold tracking-[-0.01em] text-[var(--kf-text,#1e2327)]`}>
            <Link
              href={href}
              className="rounded-[4px] after:absolute after:inset-0 after:content-[''] hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--kf-accent,#2457d6)]"
            >
              {name}
            </Link>
          </h3>
          {status && <RunStatusPill status={status} size="sm" />}
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 text-[0.82rem] text-[var(--kf-muted,#5a646d)]">
            {meta.map((m, i) => (
              <span key={m} className="inline-flex items-center gap-2">
                {i > 0 && <span aria-hidden="true">·</span>}
                {m}
              </span>
            ))}
            {!featured && (
              <>
                {meta.length > 0 && <span aria-hidden="true">·</span>}
                <span className="tabular-nums">{fmtNum(event.n_photos)} photos</span>
              </>
            )}
          </p>
        </div>

        {live && latest && <RunProgress run={latest} compact={!featured} />}

        {featured && <EventStats event={event} latest={latest} stale={live} />}

        {!live && <VerdictBar counts={event.verdict_counts} emptyText="Not culled yet" />}

        <div className="mt-auto flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t border-[var(--kf-border,#dfe4e8)] pt-4">
          <p className="min-w-0 text-[0.8rem] text-[var(--kf-muted,#5a646d)]">
            {status === "awaiting_approval" ? null : live ? (
              `Started ${fmtAgo(latest?.started_at ?? event.latest_run?.started_at, now)}`
            ) : latest?.started_at || event.latest_run?.started_at ? (
              fmtAgo(latest?.started_at ?? event.latest_run?.started_at, now)
            ) : (
              "No runs yet"
            )}
          </p>
          {/* Above the stretched card link so the buttons stay clickable. */}
          <div className="relative z-10 flex shrink-0 items-center gap-2">
            {reviewN > 0 && !live && (
              <Link href={routes.groups(event.event_id, { verdict: "review" })} className={btnSecondary}>
                {canWrite ? "Review" : "View"} {reviewN} {reviewN === 1 ? "photo" : "photos"}
              </Link>
            )}
            <StartRun
              eventId={event.event_id}
              eventName={name}
              nPhotos={event.n_photos}
              canWrite={canWrite}
              activeText={live ? "Run in progress" : undefined}
              typicalS={typicalS}
              onStarted={onStarted}
            />
          </div>
        </div>
      </div>
    </li>
  );
}

function EventStats({ event, latest, stale }: { event: EventSummary; latest?: RunX; stale: boolean }) {
  const items = [
    { k: "Photos", v: fmtNum(event.n_photos) },
    { k: "Cameras", v: event.n_cameras ? fmtNum(event.n_cameras) : "" },
    { k: "Groups", v: typeof latest?.n_groups === "number" ? fmtNum(latest.n_groups) : "", fromRun: true },
  ].filter((i) => i.v);
  return (
    <dl className="grid max-w-[640px] grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
      {items.map((i) => (
        <div key={i.k}>
          <dt className="text-[0.75rem] font-medium text-[var(--kf-muted,#5a646d)]">
            {i.k}
            {stale && "fromRun" in i && <span className="sr-only"> (previous run)</span>}
          </dt>
          <dd title={stale && "fromRun" in i ? "From the previous run" : undefined} className={`mt-0.5 text-[1.25rem] leading-tight font-semibold tabular-nums tracking-[-0.01em] text-[var(--kf-text,#1e2327)] ${stale && "fromRun" in i ? "opacity-60" : ""}`}>{i.v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function EventCardSkeleton() {
  return (
    <li aria-hidden="true" className={`${card} overflow-hidden`}>
      <div className="aspect-[16/9] animate-pulse bg-[var(--kf-surface-2,#eef1f4)] motion-reduce:animate-none" />
      <div className="space-y-3 p-5">
        <div className="h-4 w-2/3 rounded bg-[var(--kf-surface-2,#eef1f4)]" />
        <div className="h-3 w-1/2 rounded bg-[var(--kf-surface-2,#eef1f4)]" />
        <div className="h-1.5 w-full rounded-full bg-[var(--kf-surface-2,#eef1f4)]" />
        <div className="flex justify-between border-t border-[var(--kf-border,#dfe4e8)] pt-4">
          <div className="h-3 w-24 rounded bg-[var(--kf-surface-2,#eef1f4)]" />
          <div className="h-8 w-24 rounded-[8px] bg-[var(--kf-surface-2,#eef1f4)]" />
        </div>
      </div>
    </li>
  );
}

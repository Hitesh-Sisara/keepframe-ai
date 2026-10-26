"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { GroupCard, GroupCardSkeleton } from "@/components/events/GroupCard";
import { EmptyPanel, ErrorPanel } from "@/components/events/Notice";
import { RunPanel, RunPanelSkeleton } from "@/components/events/RunPanel";
import { StartRun } from "@/components/events/StartRun";
import { btnPrimary, btnSecondary, card, focusRing } from "@/components/events/tokens";
import { PageHeader } from "@/components/ui";
import { routes } from "@/lib/routes";
import { categoryLabel } from "@/lib/category";
import { VerdictBar } from "@/components/events/VerdictBar";
import { api, ApiError, useApi } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { isLive, runOrdinals, tally, typicalDurationS, type RunX } from "@/lib/runs";
import { useSession } from "@/lib/session";
import type { Group, GroupsPage, Page, Run, Verdict } from "@/lib/types";

export default function EventPage() {
  return (
    <Suspense fallback={<EventSkeleton />}>
      <EventView />
    </Suspense>
  );
}

type Filter = "all" | "needs" | Verdict;

function EventView() {
  const params = useSearchParams();
  const id = params.get("id") ?? "";
  const runParam = params.get("run") ?? undefined;
  const session = useSession();
  const team = session?.role === "team";

  const [fast, setFast] = useState(false);
  const runs = useApi<Page<RunX>>(id ? "/runs" : null, { event_id: id, limit: 50 }, fast ? 3000 : 15000);
  const run = runParam ?? runs.data?.items[0]?.run_id;
  const runDetail = useApi<RunX>(run ? `/runs/${encodeURIComponent(run)}` : null, undefined, fast ? 3000 : 15000);
  const live = isLive(runDetail.data?.status) || (runs.data?.items ?? []).some((r) => isLive(r.status));
  useEffect(() => setFast(live), [live]);

  const first = useApi<GroupsPage>(id ? `/events/${encodeURIComponent(id)}/groups` : null, { limit: 24 });
  const [more, setMore] = useState<Group[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<ApiError>();
  const initialFilter = params.get("filter");
  const [filter, setFilter] = useState<Filter>(
    initialFilter === "needs" || initialFilter === "keep" || initialFilter === "reject" ? initialFilter : "all",
  );

  useEffect(() => {
    setMore([]);
    setCursor(first.data?.next_cursor ?? null);
  }, [first.data]);

  // Refetch groups and counts when the selected run's state changes (e.g. it finishes), so nothing goes stale.
  const stateKey = runDetail.data ? `${runDetail.data.run_id}:${runDetail.data.status}:${runDetail.data.updated_at ?? ""}` : "";
  const lastKey = useRef(stateKey);
  const reloadGroups = first.reload;
  useEffect(() => {
    if (stateKey && lastKey.current && stateKey !== lastKey.current) reloadGroups();
    lastKey.current = stateKey;
  }, [stateKey, reloadGroups]);

  // Load the rest of the event's groups (up to MAX_AUTO) so the counts and filters cover the whole event.
  const MAX_AUTO = 240;
  useEffect(() => {
    if (cursor && !loadingMore && !moreError && (first.data?.items.length ?? 0) + more.length < MAX_AUTO) void loadMore();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor, loadingMore, moreError]);

  const ordinals = useMemo(() => runOrdinals(runs.data?.items ?? []), [runs.data]);

  async function loadMore() {
    if (!cursor) return;
    setLoadingMore(true);
    setMoreError(undefined);
    try {
      const page = await api.get<GroupsPage>(`/events/${encodeURIComponent(id)}/groups`, { limit: 24, cursor });
      setMore((m) => [...m, ...page.items]);
      setCursor(page.next_cursor);
    } catch (e) {
      setMoreError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setLoadingMore(false);
    }
  }

  if (!id) return <EmptyPanel title="No event selected" body={<Link href={routes.home} className="text-[var(--kf-accent,#2457d6)] hover:underline">Pick an event</Link>} />;
  if (first.error) return <ErrorPanel error={first.error} retry={first.reload} />;
  if (!first.data) return <EventSkeleton />;

  const { event } = first.data;
  const name = event.name ?? event.label;
  const groups = [...first.data.items, ...more];
  const counts = first.data.event_verdict_counts;
  const t = tally(counts);
  const pending = groups.filter((g) => g.pending_approval).length;
  const filtered = groups.filter((g) =>
    filter === "all" ? true : filter === "needs" ? g.pending_approval || (g.verdict_counts.review ?? 0) > 0 : (g.verdict_counts[filter] ?? 0) > 0,
  );
  // Groups that need a person come first.
  const sorted = [...filtered].sort((a, b) => Number(b.pending_approval) - Number(a.pending_approval));
  const needsN = groups.filter((g) => g.pending_approval || (g.verdict_counts.review ?? 0) > 0).length;
  const FILTERS: { k: Filter; word: string; n: number }[] = [
    { k: "all", word: "All", n: groups.length },
    { k: "needs", word: "Review", n: needsN },
    { k: "keep", word: "Keeps", n: groups.filter((g) => (g.verdict_counts.keep ?? 0) > 0).length },
    { k: "reject", word: "Rejects", n: groups.filter((g) => (g.verdict_counts.reject ?? 0) > 0).length },
  ].filter((f) => f.k === "all" || f.k === filter || (f.n > 0 && f.n < groups.length)) as { k: Filter; word: string; n: number }[];
  const activeRun = (runs.data?.items ?? []).find((r) => isLive(r.status));
  // "Previous run's results" only when there is an earlier run whose results these are.
  const hasEarlierRun = (runs.data?.items ?? []).some((r) => r.run_id !== activeRun?.run_id && !isLive(r.status));
  // The real group total: the latest run's count when known, else what has loaded (more may follow).
  const totalGroups = runDetail.data?.n_groups ?? groups.length;

  function started(r: Run) {
    setFast(true);
    runs.reload();
    void r;
  }

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Overview", href: routes.home }, { label: name }]}
        title={name}
        subtitle={
          <>
            <p className="flex flex-wrap items-center gap-x-2">
              {[categoryLabel(event.category), event.event_date && fmtDate(event.event_date), event.n_photos ? `${fmtNum(event.n_photos)} photos` : "", cursor ? "" : `${groups.length} groups`]
                .filter(Boolean)
                .map((s, i) => (
                  <span key={i} className="inline-flex items-center gap-2">
                    {i > 0 && <span aria-hidden="true">·</span>}
                    {s}
                  </span>
                ))}
            </p>
            {first.data.source === "s3" && <p className="mt-1 text-[0.8rem]">Decisions pending</p>}
          </>
        }
        actions={
          <>
            <StartRun
              eventId={id}
              eventName={name}
              nPhotos={event.n_photos}
              canWrite={team}
              activeText={activeRun ? "A run is already in progress for this event" : undefined}
              typicalS={typicalDurationS(runs.data?.items ?? [])}
              onStarted={started}
              variant={t.total > 0 ? "secondary" : "primary"}
              label={t.total > 0 ? "Re-run" : "Start run"}
            />
            {groups.length > 0 && (needsN === 0 || !team) && (
              <Link href={routes.groups(id)} className={btnPrimary}>
                View all {fmtNum(totalGroups)} {totalGroups === 1 ? "group" : "groups"}
              </Link>
            )}
            {needsN > 0 && team && (
              <Link href={routes.groups(id, { verdict: "review" })} className={btnPrimary}>
                Review {needsN} {needsN === 1 ? "group" : "groups"}
              </Link>
            )}
          </>
        }
      />

      {t.total > 0 && (
        <div className={`${card} mb-4 flex flex-wrap items-center gap-x-8 gap-y-3 px-5 py-4`}>
          <VerdictBar counts={counts} className="min-w-[240px] flex-1" />
        </div>
      )}

      {runDetail.data ? (
        <RunPanel run={runDetail.data} ordinal={ordinals[runDetail.data.run_id]} eventId={id} allRuns={runs.data} />
      ) : run && !runDetail.error ? (
        <RunPanelSkeleton />
      ) : null}
      <ErrorPanel error={runDetail.error} retry={runDetail.reload} />

      {activeRun && t.total > 0 && hasEarlierRun && (
        <div className="mb-6 flex items-center gap-3 rounded-[var(--kf-radius,10px)] border border-[#9db4ee] bg-[var(--kf-accent-soft,#e8eefc)] px-4 py-3 text-[0.88rem] font-medium text-[#14306f]">
          <span aria-hidden="true" className="h-2 w-2 animate-pulse rounded-full bg-[var(--kf-accent,#2457d6)] motion-reduce:animate-none" />
          Previous run's results
        </div>
      )}

      {pending > 0 && (
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-[var(--kf-radius,10px)] border border-[#f3d9ae] bg-[#fdf6ea] px-4 py-3">
          <p className="text-[0.88rem] font-medium text-[#6e4200]">
            {team ? (
              <>
                <span className="tabular-nums">{pending}</span> close {pending === 1 ? "call" : "calls"} open
              </>
            ) : (
              <>
                Waiting on the team (<span className="tabular-nums">{pending}</span> {pending === 1 ? "group" : "groups"})
              </>
            )}
          </p>
          <Link href={routes.approvals()} className={`${btnSecondary} btn-sm`}>
            {team ? "Answer" : "View"}
          </Link>
        </div>
      )}

      {groups.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div role="group" aria-label="Filter groups" className="inline-flex flex-wrap rounded-[8px] border border-[var(--kf-border,#dfe4e8)] bg-[var(--kf-surface-2,#f5f7f9)] p-0.5">
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
                <span className="tabular-nums text-[0.75rem] text-[var(--kf-muted,#5a646d)]">{f.n}</span>
              </button>
            ))}
          </div>
          {cursor && <span className="text-[0.8rem] text-[var(--kf-muted,#5a646d)]" aria-live="polite">Loading…</span>}
        </div>
      )}

      {groups.length === 0 ? (
        <EmptyPanel title="No groups yet" body="Start a run to group bursts." />
      ) : sorted.length === 0 ? (
        <EmptyPanel title="No matches" body="Try another filter." />
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-5">
          {sorted.map((g) => (
            <GroupCard
              key={g.group_id}
              group={g}
              href={`/groups/?event=${encodeURIComponent(id)}&id=${encodeURIComponent(g.group_id)}${run ?? g.run_id ? `&run=${encodeURIComponent((run ?? g.run_id)!)}` : ""}`}
            />
          ))}
          {loadingMore && [0, 1, 2].map((i) => <GroupCardSkeleton key={`s${i}`} />)}
        </ul>
      )}

      {cursor && (
        <div className="mt-8 flex flex-col items-center gap-3">
          <button type="button" className={btnSecondary} onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? "Loading…" : "Load more groups"}
          </button>
          <ErrorPanel error={moreError} retry={loadMore} />
        </div>
      )}
    </>
  );
}

function fmtDate(d: string): string {
  const t = new Date(`${d}T00:00:00`);
  return Number.isNaN(t.getTime()) ? d : t.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function EventSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading event">
      <div className="mb-4 h-3 w-32 rounded bg-[var(--kf-surface-2,#e6eaee)]" />
      <div className="mb-8 space-y-2">
        <div className="h-7 w-72 rounded bg-[var(--kf-surface-2,#e6eaee)]" />
        <div className="h-3.5 w-96 max-w-full rounded bg-[var(--kf-surface-2,#e6eaee)]" />
      </div>
      <RunPanelSkeleton />
      <ul className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <GroupCardSkeleton key={i} />
        ))}
      </ul>
    </div>
  );
}

"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo, useState } from "react";
import { BurstDetail } from "@/components/groups/BurstDetail";
import { BurstGrid, type GridState } from "@/components/groups/BurstGrid";
import { useEventGroups, useExpiryRefresh } from "@/components/groups/data";
import { ImageRefreshContext } from "@/components/Frame";
import { ErrorNote } from "@/components/States";
import { EmptyState, PageHeader, Skeleton } from "@/components/ui";
import type { GroupSort, VerdictFilter } from "@/lib/cull";
import { routes } from "@/lib/routes";

// /groups/?event=<id>            event-wide burst grid (the cull view)
// /groups/?event=<id>&id=<group> one burst: preview, filmstrip, inspector, agent trace
export default function GroupsPage() {
  return (
    <Suspense fallback={<GridSkeleton />}>
      <Route />
    </Suspense>
  );
}

function Route() {
  const params = useSearchParams();
  const eventId = params.get("event") ?? "";
  const groupId = params.get("id") ?? "";
  if (!eventId) {
    return (
      <EmptyState
        icon="frames"
        title="Pick an event"
        description="Open an event first."
        action={
          <Link href="/" className="btn btn-primary btn-sm">
            Go to events
          </Link>
        }
      />
    );
  }
  if (groupId) return <BurstDetail key={groupId} eventId={eventId} groupId={groupId} runParam={params.get("run") ?? undefined} />;
  return <EventGrid eventId={eventId} />;
}

const VERDICTS = new Set(["all", "keep", "review", "reject"]);
const SORTS = new Set(["capture", "close", "review", "size"]);

function EventGrid({ eventId }: { eventId: string }) {
  const q = useSearchParams();
  const all = useEventGroups(eventId);
  const [state, setState] = useState<GridState>(() => ({
    verdict: (VERDICTS.has(q.get("v") ?? "") ? q.get("v") : "all") as VerdictFilter,
    reason: q.get("reason") ?? "",
    sort: (SORTS.has(q.get("sort") ?? "") ? q.get("sort") : "capture") as GroupSort,
  }));
  const onState = useCallback((s: Partial<GridState>) => {
    setState((prev) => {
      const next = { ...prev, ...s };
      // Filters live in the URL (replaceState) so a reload or a back-navigation keeps them.
      const u = new URL(window.location.href);
      for (const [k, v, d] of [
        ["v", next.verdict, "all"],
        ["reason", next.reason, ""],
        ["sort", next.sort, "capture"],
      ] as const) {
        if (v && v !== d) u.searchParams.set(k, v);
        else u.searchParams.delete(k);
      }
      window.history.replaceState(window.history.state, "", u);
      return next;
    });
  }, []);
  const urls = useMemo(() => all.data?.groups.flatMap((g) => g.members.map((m) => m.proxy_url)) ?? [], [all.data]);
  useExpiryRefresh(urls, all.refresh);

  if (all.error) return <ErrorNote error={all.error} retry={() => void all.refresh()} />;
  if (!all.data) return <GridSkeleton />;
  const { event } = all.data;
  // Dev-only: ?stress=N repeats the groups N times to exercise the windowed grid (never in a production build).
  const stress = process.env.NODE_ENV !== "production" ? Math.min(20, Number(q.get("stress")) || 1) : 1;
  const groups = stress > 1 ? Array.from({ length: stress }, (_, k) => all.data!.groups.map((g) => ({ ...g, group_id: `${g.group_id}~${k}` }))).flat() : all.data.groups;
  const pending = groups.filter((g) => g.pending_approval).length;
  const frames = groups.reduce((n, g) => n + g.members.length, 0);
  // Close calls that went to review; the rest of the close calls the agent settled with a further check.
  const needs = groups.filter((g) => (g.verdict_counts?.review ?? 0) > 0 || g.pending_approval).length;
  return (
    <ImageRefreshContext.Provider value={all.refresh}>
      <PageHeader
        className="!mb-4"
        crumbs={[{ label: "Events", href: routes.home }, { label: event.name ?? event.label, href: routes.event(eventId) }, { label: "Groups" }]}
        title="Groups"
        subtitle={
          <Dotted
            items={[
              <>{groups.length} groups</>,
              needs > 0 ? <>{needs} to review</> : null,
              pending > 0 ? (
                <Link href={routes.approvals()} className="font-semibold text-[var(--kf-review)] hover:underline">
                  {pending} waiting
                </Link>
              ) : null,
            ]}
          />
        }
      />
      {groups.length === 0 ? (
        <EmptyState icon="frames" title="No groups yet" description="Run the event to group bursts." />
      ) : (
        <BurstGrid eventId={eventId} groups={groups} state={state} onState={onState} />
      )}
      {all.data.truncated && <p className="t-small mt-4 text-[var(--kf-muted)]">Showing the first {groups.length} groups.</p>}
    </ImageRefreshContext.Provider>
  );
}

/** Items joined by "·", where each dot trails the item before it: a wrap never starts a line with a dot. */
function Dotted({ items }: { items: React.ReactNode[] }) {
  const shown = items.filter(Boolean);
  return (
    <span className="flex flex-wrap items-baseline gap-x-2">
      {shown.map((it, i) => (
        <span key={i} className="inline-flex items-baseline gap-2">
          {it}
          {i < shown.length - 1 && <span aria-hidden="true">·</span>}
        </span>
      ))}
    </span>
  );
}

function GridSkeleton() {
  return (
    <div aria-busy="true">
      <span className="sr-only" role="status">
        Loading groups…
      </span>
      <Skeleton className="h-3 w-40" />
      <Skeleton className="mt-2.5 h-7 w-72" />
      <Skeleton className="mt-2 h-3.5 w-56" />
      <Skeleton className="mt-6 h-9 w-full !rounded-[9px]" />
      <div className="mt-5 grid grid-cols-[repeat(auto-fill,minmax(min(100%,424px),1fr))] gap-4">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="kf-card p-2.5">
            <div className="grid grid-cols-[1.6fr_1fr] gap-1.5">
              <Skeleton className="aspect-[3/2] !rounded-[7px]" />
              <div className="grid gap-1.5">
                <Skeleton className="!rounded-[6px]" />
                <Skeleton className="!rounded-[6px]" />
              </div>
            </div>
            <Skeleton className="mt-3 h-3.5 w-1/3" />
          </div>
        ))}
      </div>
    </div>
  );
}

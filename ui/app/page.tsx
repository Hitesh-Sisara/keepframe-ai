"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "@/components/ui";
import { EventCard, EventCardSkeleton } from "@/components/events/EventCard";
import { EmptyPanel, ErrorPanel } from "@/components/events/Notice";
import { RunsList, RunsSkeleton } from "@/components/events/RunsList";
import { useNow } from "@/components/events/RunStatus";
import { AgentCard } from "@/components/overview/AgentCard";
import { Hero } from "@/components/overview/Hero";
import { RunTimeline } from "@/components/overview/Timeline";
import { useFeaturedRunId, useRunTrace } from "@/components/overview/useOverview";
import { pickCloseCall, WatchItAsk } from "@/components/overview/WatchItAsk";
import { useApi } from "@/lib/api";
import { isLive, runOrdinals, runsSignature, typicalDurationS, type RunX } from "@/lib/runs";
import { useSession } from "@/lib/session";
import type { Approval, EventSummary, GroupsPage, Page, Run } from "@/lib/types";

const POLL_LIVE_MS = 3000;
const POLL_IDLE_MS = 15000;

type RunFull = RunX & { worker_wall_s?: number; agent_wall_s?: number };

export default function OverviewPage() {
  const session = useSession();
  const team = session?.role === "team";
  // Poll fast only while something is running; the events list refetches whenever any run changes, so the
  // event cards' verdict counts never sit at a stale 0/0/0 after a run finishes.
  const [fast, setFast] = useState(false);
  const runs = useApi<Page<RunX>>("/runs", { limit: 50 }, fast ? POLL_LIVE_MS : POLL_IDLE_MS);
  const events = useApi<Page<EventSummary>>("/events", { limit: 100 });
  const approvals = useApi<Page<Approval>>(team ? "/approvals" : null, { limit: 100 }, 15000);
  const [optimistic, setOptimistic] = useState<RunX[]>([]);

  const items = useMemo(() => {
    const server = runs.data?.items ?? [];
    const ids = new Set(server.map((r) => r.run_id));
    return [...optimistic.filter((r) => !ids.has(r.run_id)), ...server];
  }, [runs.data, optimistic]);

  const anyLive = items.some((r) => isLive(r.status));
  useEffect(() => setFast(anyLive), [anyLive]);
  const now = useNow(anyLive, 1000);

  const sig = runsSignature(runs.data?.items);
  const lastSig = useRef(sig);
  const reloadEvents = events.reload;
  useEffect(() => {
    if (sig && lastSig.current && sig !== lastSig.current) reloadEvents();
    lastSig.current = sig;
  }, [sig, reloadEvents]);

  const latestByEvent = useMemo(() => {
    const m: Record<string, RunX> = {};
    for (const r of items) {
      const cur = m[r.event_id];
      if (!cur || (r.started_at ?? "") > (cur.started_at ?? "")) m[r.event_id] = r;
    }
    return m;
  }, [items]);

  // The run the dashboard is about: the newest one with a trace (live runs always count).
  const sorted = useMemo(() => [...items].sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? "")), [items]);
  const featuredId = useFeaturedRunId(sorted, isLive);
  const latest = sorted.find((r) => r.run_id === featuredId) ?? sorted[0];
  const latestFull = useApi<RunFull>(latest ? `/runs/${encodeURIComponent(latest.run_id)}` : null, undefined, latest && isLive(latest.status) ? POLL_LIVE_MS : undefined);
  const run: RunFull | undefined = latestFull.data ?? latest;
  const groups = useApi<GroupsPage>(run ? `/events/${encodeURIComponent(run.event_id)}/groups` : null, { limit: 100, _r: run ? `${run.run_id}:${run.status}` : undefined });
  const trace = useRunTrace(run?.run_id, run ? `${run.status}` : "");

  const names = Object.fromEntries((events.data?.items ?? []).map((e) => [e.event_id, e.name ?? e.label]));
  const typical = typicalDurationS(items);
  const ordinals = useMemo(() => runOrdinals(items), [items]);

  // One definition of "waiting": open approval requests (team), else bursts still pending a person (judge).
  const pendingGroups = groups.data?.items.filter((g) => g.pending_approval);
  const waiting = team ? (approvals.data ? approvals.data.items.length : undefined) : pendingGroups?.length;
  // Close calls = bursts the agent asked about: still pending, or answered outside the agent (person or automatic).
  const askedGroups = groups.data?.items.filter((g) => g.pending_approval || g.members.some((m) => m.decided_by && m.decided_by !== "agent" && m.decided_by !== "worker"));
  const reviewInAsked = askedGroups?.reduce((a, g) => a + (g.verdict_counts.review ?? 0), 0);
  const closeCall = groups.data ? pickCloseCall(groups.data.items) : undefined;

  function started(r: Run) {
    setOptimistic((o) => [{ ...r, status: r.status || "starting" }, ...o]);
    setFast(true);
    runs.reload();
    events.reload();
  }

  const noRuns = runs.data && items.length === 0;

  return (
    <>
      <PageHeader
        title="Overview"
      />

      <ErrorPanel error={runs.error} retry={runs.reload} />

      {/* Hero + timeline for the latest run */}
      {!runs.data && !runs.error ? (
        <HeroSkeleton />
      ) : run ? (
        <div className="grid gap-6">
          <Hero
            run={run}
            eventName={names[run.event_id] ?? run.event?.name ?? "Event"}
            eventId={run.event_id}
            waiting={waiting}
            reviewInAsked={reviewInAsked}
            team={team}
            runLabel={ordinals[run.run_id] ? `Run ${ordinals[run.run_id]}` : undefined}
            timeline={<RunTimeline run={run} now={now} title="Where the time went" compact />}
          />

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
            {closeCall ? (
              <WatchItAsk group={closeCall} eventId={run.event_id} runId={run.run_id} team={team} pending={waiting} />
            ) : groups.data ? (
              <EmptyPanel title="No close calls" body="Every burst had a clear winner." />
            ) : (
              <div className="kf-card kf-skeleton min-h-[420px]" aria-hidden="true" />
            )}
            <AgentCard run={run} lines={trace.lines} pivots={trace.pivots} groups={groups.data?.items} eventId={run.event_id} />
          </div>
        </div>
      ) : null}

      {noRuns && (
        <EmptyPanel
          title="No runs yet"
          body={team ? "Start one from an event below." : "Nothing has run yet."}
          icon={
            <svg viewBox="0 0 20 20" className="h-5 w-5">
              <path d="M7 5v10l8-5z" fill="currentColor" />
            </svg>
          }
        />
      )}

      <section aria-labelledby="runs-h" className="mt-12">
        <h2 id="runs-h" className="t-title mb-4">
          Recent runs
        </h2>
        {!runs.data && !runs.error && <RunsSkeleton />}
        {runs.error && !runs.data && <EmptyPanel title="Runs unavailable" body="Retry when the server is back." />}
        {runs.data && items.length > 0 && <RunsList runs={items} names={names} />}
      </section>

      <section aria-labelledby="events-h" className="mt-12">
        <h2 id="events-h" className="t-title mb-4">
          Events
        </h2>
        {/* One error per page: the top banner already covers a desk that is unreachable. */}
        {!(runs.error && events.error?.status === runs.error.status) && <ErrorPanel error={events.error} retry={events.reload} />}
        {!events.data && !events.error && (
          <ul className="grid gap-6 sm:grid-cols-2 xl:grid-cols-3" aria-busy="true" aria-label="Loading events">
            <EventCardSkeleton />
            <EventCardSkeleton />
            <EventCardSkeleton />
          </ul>
        )}
        {events.data && events.data.items.length === 0 && <EmptyPanel title="No events yet" body="Import photos to add one." />}
        {events.data && events.data.items.length > 0 && (
          <ul className={events.data.items.length === 1 ? "grid" : "grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-6"}>
            {events.data.items.map((e) => {
              const l = latestByEvent[e.event_id];
              return (
                <EventCard
                  key={e.event_id}
                  event={e}
                  latest={l}
                  canWrite={team}
                  typicalS={typical}
                  refreshKey={l ? `${l.run_id}:${l.status}` : ""}
                  onStarted={started}
                  featured={events.data!.items.length === 1}
                />
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}

function HeroSkeleton() {
  return (
    <div className="grid gap-6" aria-busy="true" aria-label="Loading the overview">
      <div className="kf-card grid gap-8 p-8 lg:grid-cols-2">
        <div className="space-y-4">
          <div className="kf-skeleton h-3 w-40" />
          <div className="kf-skeleton h-12 w-4/5" />
          <div className="kf-skeleton h-4 w-3/5" />
          <div className="kf-skeleton h-10 w-64" />
        </div>
        <div className="kf-skeleton h-40" />
      </div>
      <div className="kf-card kf-skeleton h-36" />
    </div>
  );
}

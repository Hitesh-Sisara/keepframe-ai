"use client";

import "@/components/bench/tokens.css";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ErrorNote } from "@/components/States";
import { EmptyState, PageHeader } from "@/components/ui";
import { routes } from "@/lib/routes";
import { Labeller, LabelSkeleton } from "@/components/label/Labeller";
import { useApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { EventSummary, Page } from "@/lib/types";

// Labelling tool for the evaluation set (docs/EVALUATION.md §1, eval/LABEL_GUIDE.md). Labels live in this tab's
// sessionStorage until exported as eval/labels/<eventId>.csv. Nothing is written back to the API.

export default function LabelPage() {
  const session = useSession();
  const events = useApi<Page<EventSummary>>(session?.role === "team" ? "/events" : null, { limit: 100 });
  const [eventId, setEventId] = useState("");
  const items = events.data?.items ?? [];
  const chosen = eventId || items[0]?.event_id || "";
  const event = items.find((e) => e.event_id === chosen);

  useEffect(() => {
    document.title = "Label · KeepFrame";
  }, []);

  if (session && session.role !== "team") {
    return (
      <div className="kf-page">
        <PageHeader title="Label" />
        <EmptyState
          icon="lock"
          title="Team token required"
          description="Judge view is read-only."
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <Link href={routes.home} className="btn btn-primary">
                Browse events
              </Link>
              <Link href={routes.bench} className="btn btn-secondary">
                View benchmark
              </Link>
            </div>
          }
        />
      </div>
    );
  }

  return (
    <div className="kf-page text-(--p-text)">
      <ErrorNote error={events.error} retry={events.reload} />
      {!events.data && !events.error && <LabelSkeleton />}
      {events.data && !items.length && (
        <div className="kf-card mx-auto mt-10 max-w-[520px] px-8 py-10 text-center">
          <p className="text-[1.125rem] font-semibold">No events to label</p>
        </div>
      )}
      {chosen && (
        <Labeller
          key={chosen}
          eventId={chosen}
          eventName={event?.name ?? event?.label ?? "Event"}
          picker={
            items.length > 1 ? (
              <label className="flex items-center gap-2 text-[0.875rem]">
                <span className="sr-only">Event</span>
                <select
                  value={chosen}
                  onChange={(e) => setEventId(e.target.value)}
                  className="kf-focus rounded-[8px] border border-(--p-border) bg-(--p-surface) px-2.5 py-1.5 font-medium outline-none"
                >
                  {items.map((e) => (
                    <option key={e.event_id} value={e.event_id}>
                      {e.name ?? e.label}
                    </option>
                  ))}
                </select>
              </label>
            ) : null
          }
        />
      )}
    </div>
  );
}


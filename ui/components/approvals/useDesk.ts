"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import type { Approval, DecisionSource, EventSummary, Group, GroupsPage, Page } from "@/lib/types";
import { fromApproval, fromPendingGroup, type ApprovalVM } from "./logic";

export interface AnsweredVM extends ApprovalVM {
  source: DecisionSource;
  note?: string;
}

interface EventGroups {
  event: EventSummary;
  groups: Group[];
}

const NON_AGENT = new Set(["human", "scripted", "lead_test"]);

/** Every group of every event (the groups list is the only approval view a judge may read). */
function useAllGroups(enabled: boolean, pollMs: number): { data?: EventGroups[]; error?: ApiError; reload: () => void } {
  const [data, setData] = useState<EventGroups[]>();
  const [error, setError] = useState<ApiError>();
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let live = true;
    (async () => {
      try {
        const events = await api.get<Page<EventSummary>>("/events", { limit: 100 });
        const out: EventGroups[] = [];
        for (const ev of events.items) {
          const groups: Group[] = [];
          let cursor: string | null = null;
          for (let i = 0; i < 20; i++) {
            const page: GroupsPage = await api.get<GroupsPage>(`/events/${encodeURIComponent(ev.event_id)}/groups`, { limit: 100, cursor });
            groups.push(...page.items);
            cursor = page.next_cursor;
            if (!cursor) break;
          }
          out.push({ event: ev, groups });
        }
        if (live) {
          setData(out);
          setError(undefined);
        }
      } catch (e) {
        if (live) setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      }
    })();
    return () => {
      live = false;
    };
  }, [enabled, tick]);

  useEffect(() => {
    if (!enabled || !pollMs) return;
    const id = window.setInterval(() => document.visibilityState === "visible" && setTick((t) => t + 1), pollMs);
    return () => window.clearInterval(id);
  }, [enabled, pollMs]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, reload };
}

function answerSource(g: Group): DecisionSource | null {
  const counts = new Map<DecisionSource, number>();
  for (const m of g.members) {
    if (!m.decided_by || !NON_AGENT.has(m.decided_by)) continue;
    const s = (m.decision_source ?? "unknown") as DecisionSource;
    counts.set(s, (counts.get(s) ?? 0) + 1);
  }
  if (!counts.size) return null;
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0];
}

export function useDesk(role: "team" | "judge") {
  const team = role === "team";
  const list = useApi<Page<Approval>>(team ? "/approvals" : null, { limit: 100 }, 10000);
  const all = useAllGroups(true, 20000);

  const eventsById = useMemo(() => new Map((all.data ?? []).map((e) => [e.event.event_id, e])), [all.data]);
  const eventName = useCallback((id: string) => {
    const e = eventsById.get(id)?.event;
    return e ? e.name || e.label : undefined;
  }, [eventsById]);

  const waiting: ApprovalVM[] | undefined = useMemo(() => {
    if (team) {
      if (!list.data) return undefined;
      return list.data.items.map((a) => {
        const g = a.group_id ? eventsById.get(a.event_id)?.groups.find((x) => x.group_id === a.group_id) : undefined;
        return fromApproval(a, g, eventName(a.event_id));
      });
    }
    if (!all.data) return undefined;
    return all.data.flatMap((e) =>
      e.groups.filter((g) => g.pending_approval).map((g) => fromPendingGroup(g, e.event.event_id, e.event.name || e.event.label)),
    );
  }, [team, list.data, all.data, eventsById, eventName]);

  const answered: AnsweredVM[] | undefined = useMemo(() => {
    if (!all.data) return undefined;
    return all.data.flatMap((e) =>
      e.groups
        .filter((g) => !g.pending_approval)
        .flatMap((g) => {
          const source = answerSource(g);
          if (!source) return [];
          const vm = fromPendingGroup(g, e.event.event_id, e.event.name || e.event.label);
          // Raw test-harness notes ("lead-mechanism-test: …") are bookkeeping, not a reviewer's words.
          const raw = g.members.find((m) => m.human_note)?.human_note;
          const note = raw && /^lead-mechanism-test:/i.test(raw.trim()) ? undefined : raw;
          return [{ ...vm, id: `answered:${vm.id}`, question: vm.question, source, note }];
        }),
    );
  }, [all.data]);

  return {
    waiting,
    answered,
    error: (team ? list.error : undefined) ?? all.error,
    loading: team ? !list.data && !list.error : !all.data && !all.error,
    reload: () => {
      list.reload();
      all.reload();
    },
  };
}

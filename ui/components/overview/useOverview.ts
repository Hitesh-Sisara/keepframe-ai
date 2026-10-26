"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Pivot, TraceLine, TracePage } from "@/lib/types";

/** The whole agent trace of one run (paged), with the plan changes the API found on each page. Fetched once per
 *  run and refetched when `key` changes (the run's state), never polled. */
export function useRunTrace(runId: string | undefined, key: string): { lines?: TraceLine[]; pivots?: Pivot[]; error?: ApiError } {
  const [state, setState] = useState<{ lines?: TraceLine[]; pivots?: Pivot[]; error?: ApiError }>({});
  useEffect(() => {
    if (!runId) return;
    let live = true;
    (async () => {
      try {
        const lines: TraceLine[] = [];
        const pivots: Pivot[] = [];
        let after = -1;
        for (let i = 0; i < 8; i++) {
          const page = await api.get<TracePage>(`/runs/${encodeURIComponent(runId)}/trace`, { after, limit: 500 });
          lines.push(...page.items);
          pivots.push(...(page.pivots ?? []));
          if (page.next_after === null || page.next_after === undefined) break;
          after = page.next_after;
        }
        if (live) setState({ lines, pivots });
      } catch (e) {
        if (live) setState({ error: e instanceof ApiError ? e : new ApiError(0, String(e)) });
      }
    })();
    return () => {
      live = false;
    };
  }, [runId, key]);
  return state;
}

/** The run the Overview features: the newest run whose trace has at least one item (a run with an empty trace
 *  has nothing to show in the agent card or the close-call replay). Live runs always qualify, since their trace
 *  is still filling in. Falls back to the newest run when none of the first few has a trace. */
export function useFeaturedRunId(sorted: { run_id: string; status?: string }[], isLive: (s?: string) => boolean): string | undefined {
  const ids = sorted.slice(0, 6).map((r) => `${r.run_id}|${isLive(r.status) ? 1 : 0}`).join(",");
  const [picked, setPicked] = useState<{ key: string; id?: string }>();
  useEffect(() => {
    if (!ids) return;
    let live = true;
    (async () => {
      for (const part of ids.split(",")) {
        const [id, liveRun] = part.split("|");
        if (liveRun === "1") {
          if (live) setPicked({ key: ids, id });
          return;
        }
        try {
          const page = await api.get<TracePage>(`/runs/${encodeURIComponent(id)}/trace`, { after: -1, limit: 1 });
          if (page.items.length > 0) {
            if (live) setPicked({ key: ids, id });
            return;
          }
        } catch {
          /* try the next run */
        }
      }
      if (live) setPicked({ key: ids, id: ids.split(",")[0]?.split("|")[0] });
    })();
    return () => {
      live = false;
    };
  }, [ids]);
  // Until the check finishes, the newest run stands in, so the page never waits on it to render.
  return picked?.key === ids ? picked.id : sorted[0]?.run_id;
}

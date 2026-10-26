"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { earliestExpiry } from "@/lib/cull";
import type { Group, GroupsPage } from "@/lib/types";

export interface Loaded<T> {
  data: T | undefined;
  error: ApiError | undefined;
  /** Refetch; resolves when fresh data (with fresh signed image links) is on screen. Concurrent calls share one request. */
  refresh: () => Promise<void>;
}

/** Single-flight loader: one request at a time, callers share the promise. Keeps old data while refetching. */
function useLoader<T>(key: string | null, load: () => Promise<T>): Loaded<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError>();
  const inflight = useRef<Promise<void> | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const keyRef = useRef(key);
  keyRef.current = key;

  const refresh = useCallback((): Promise<void> => {
    if (!keyRef.current) return Promise.resolve();
    if (inflight.current) return inflight.current;
    const k = keyRef.current;
    const p = loadRef
      .current()
      .then((d) => {
        if (keyRef.current === k) {
          setData(d);
          setError(undefined);
        }
      })
      .catch((e: unknown) => {
        if (keyRef.current === k) setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      })
      .finally(() => {
        inflight.current = null;
      });
    inflight.current = p;
    return p;
  }, []);

  useEffect(() => {
    setData(undefined);
    setError(undefined);
    inflight.current = null;
    if (key) void refresh();
  }, [key, refresh]);

  return { data, error, refresh };
}

const MAX_PAGES = 20;

export interface EventGroups {
  event: GroupsPage["event"];
  groups: Group[];
  counts?: GroupsPage["event_verdict_counts"];
  truncated: boolean;
}

/** Every group of an event (follows the cursor; the API caps a page at 100). */
export function useEventGroups(eventId: string): Loaded<EventGroups> {
  const path = eventId ? `/events/${encodeURIComponent(eventId)}/groups` : null;
  return useLoader(path, async () => {
    const first = await api.get<GroupsPage>(path as string, { limit: 100 });
    const groups = [...first.items];
    let cursor = first.next_cursor;
    let pages = 1;
    while (cursor && pages < MAX_PAGES) {
      const next = await api.get<GroupsPage>(path as string, { limit: 100, cursor });
      groups.push(...next.items);
      cursor = next.next_cursor;
      pages++;
    }
    return { event: first.event, groups, counts: first.event_verdict_counts, truncated: Boolean(cursor) };
  });
}

export function useGroupDetail(eventId: string, groupId: string): Loaded<GroupsPage> {
  const path = eventId && groupId ? `/events/${encodeURIComponent(eventId)}/groups` : null;
  return useLoader(path ? `${path}#${groupId}` : null, () => api.get<GroupsPage>(path as string, { group_id: groupId }));
}

/** Refetch shortly before the earliest signed image link expires, so frames never go stale on screen. */
export function useExpiryRefresh(urls: (string | undefined)[], refresh: () => Promise<void>) {
  const exp = earliestExpiry(urls);
  useEffect(() => {
    if (!exp) return;
    const ms = Math.max(5_000, exp * 1000 - Date.now() - 45_000);
    const id = window.setTimeout(() => {
      if (document.visibilityState === "visible") void refresh();
    }, ms);
    // A tab that was hidden past the expiry refreshes as soon as it is visible again.
    const onVis = () => {
      if (document.visibilityState === "visible" && Date.now() > exp * 1000 - 45_000) void refresh();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearTimeout(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [exp, refresh]);
}

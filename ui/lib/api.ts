"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { DemoMiss, demoGet, demoPost, DEMO_NOTICE } from "./demo";
import { clearSession, currentToken, isDemo } from "./session";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "").replace(/\/+$/, "");

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

type Query = Record<string, string | number | undefined | null>;

function url(path: string, query?: Query): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  const q = qs.toString();
  return `${API_BASE}${path}${q ? `?${q}` : ""}`;
}

let noticeShown = false;

/** Demo mode answers from the static dataset: same shapes, same errors, no network. */
async function demoRequest<T>(method: "GET" | "POST", path: string, query?: Query, body?: unknown): Promise<T> {
  try {
    if (method === "GET") return await demoGet<T>(path, query);
    const out = await demoPost<T>(path, body);
    // Lazy import: the toast store is client-only UI and api.ts is also imported by node unit tests.
    const { toast } = await import("@/components/ui/Toast");
    if (!noticeShown) toast.info(DEMO_NOTICE, "Nothing is sent anywhere. Close the tab to start over.");
    noticeShown = true;
    return out;
  } catch (e) {
    if (e instanceof DemoMiss) throw new ApiError(e.status, e.message);
    throw new ApiError(0, "The demo data couldn't be loaded. Reload the page to try again.");
  }
}

async function request<T>(method: "GET" | "POST", path: string, query?: Query, body?: unknown, token?: string): Promise<T> {
  if (token === undefined && isDemo()) return demoRequest<T>(method, path, query, body);
  const bearer = token ?? currentToken();
  if (!bearer) throw new ApiError(401, "Enter an access token to continue.");
  let resp: Response;
  try {
    resp = await fetch(url(path, query), {
      method,
      headers: {
        Authorization: `Bearer ${bearer}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
    });
  } catch {
    throw new ApiError(0, `Can't reach the API at ${API_BASE || "(NEXT_PUBLIC_API_BASE is not set)"}.`);
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const doc = await resp.json();
      if (typeof doc?.detail === "string") detail = doc.detail;
    } catch {
      /* non-JSON error body */
    }
    if (resp.status === 401 && token === undefined) clearSession();
    throw new ApiError(resp.status, detail || `Request failed (${resp.status})`);
  }
  return (await resp.json()) as T;
}

export const api = {
  get: <T>(path: string, query?: Query, token?: string) => request<T>("GET", path, query, undefined, token),
  post: <T>(path: string, body: unknown) => request<T>("POST", path, undefined, body),
};

export interface Loaded<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

/** GET with stale-while-revalidate: keeps the previous data on screen while refetching. */
export function useApi<T>(path: string | null, query?: Query, pollMs?: number): Loaded<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError>();
  const [loading, setLoading] = useState(Boolean(path));
  const [tick, setTick] = useState(0);
  const key = path ? url(path, query) : null;
  const latest = useRef(key);
  latest.current = key;
  // A poll tick must not cancel a request that is still running: when the API is slower than pollMs the
  // effect cleanup would discard every response and the page would show "Loading" forever.
  const inFlight = useRef(false);

  useEffect(() => {
    if (!path || !key) return;
    let live = true;
    inFlight.current = true;
    setLoading(true);
    api
      .get<T>(path, query)
      .then((d) => {
        if (live && latest.current === key) {
          setData(d);
          setError(undefined);
        }
      })
      .catch((e: unknown) => {
        if (live) setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      })
      .finally(() => {
        if (live) {
          inFlight.current = false;
          setLoading(false);
        }
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick]);

  useEffect(() => {
    if (!pollMs || !key) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible" && !inFlight.current) setTick((t) => t + 1);
    }, pollMs);
    return () => window.clearInterval(id);
  }, [pollMs, key]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

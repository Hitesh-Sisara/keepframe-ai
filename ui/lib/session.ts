"use client";

// The bearer token is entered once per browser tab and kept in sessionStorage only (cleared when the
// tab closes). It is never written to localStorage, cookies, URLs or logs.
import { useSyncExternalStore } from "react";
import { DEMO_BUILD } from "./demo";
import type { Role } from "./types";

const TOKEN_KEY = "keepframe.token";
const ROLE_KEY = "keepframe.role";
const listeners = new Set<() => void>();

export interface Session {
  token: string;
  /** What the desk lets this session do. A demo session acts as "team" (every write works, in memory only). */
  role: Role;
  /** Demo mode: no token, no network, data from /demo/*.json (lib/demo.ts). */
  demo?: boolean;
}

/** The demo session: a NEXT_PUBLIC_DEMO=1 build always is one; any build becomes one after "Explore the demo". */
export const DEMO_SESSION: Session = { token: "demo", role: "team", demo: true };

function read(): Session | null {
  if (typeof window === "undefined") return null;
  const role = window.sessionStorage.getItem(ROLE_KEY);
  if (DEMO_BUILD || role === "demo") return DEMO_SESSION;
  const token = window.sessionStorage.getItem(TOKEN_KEY);
  return token && (role === "judge" || role === "team") ? { token, role } : null;
}

/** "Explore the demo (no sign-in)": this tab only, no token. */
export function startDemo(): void {
  window.sessionStorage.removeItem(TOKEN_KEY);
  window.sessionStorage.setItem(ROLE_KEY, "demo");
  // The guided tour starts only right after this click, never on a deep link (see components/demo/Tour.tsx).
  window.sessionStorage.setItem("keepframe.tour.pending", "1");
  cached = DEMO_SESSION;
  listeners.forEach((l) => l());
}

export function isDemo(): boolean {
  return Boolean(snapshot()?.demo);
}

let cached: Session | null | undefined;
function snapshot(): Session | null {
  if (cached === undefined) cached = read();
  return cached;
}

export function saveSession(s: Session): void {
  window.sessionStorage.setItem(TOKEN_KEY, s.token);
  window.sessionStorage.setItem(ROLE_KEY, s.role);
  cached = s;
  listeners.forEach((l) => l());
}

export function clearSession(): void {
  window.sessionStorage.removeItem("keepframe.demo.overlay");
  window.sessionStorage.removeItem(TOKEN_KEY);
  window.sessionStorage.removeItem(ROLE_KEY);
  // A demo-only build has no sign-in: leaving the demo just starts it over.
  cached = DEMO_BUILD ? DEMO_SESSION : null;
  listeners.forEach((l) => l());
}

export function currentToken(): string | null {
  return snapshot()?.token ?? null;
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** `undefined` during prerender/hydration, then the session (or null). */
export function useSession(): Session | null | undefined {
  return useSyncExternalStore(subscribe, snapshot, () => undefined);
}

// Your picks: the verdicts a person sets while culling (K keep, X reject, U review / unsure). They live in this browser
// (localStorage, per event) and never go to the API; the agent's verdict stays what the run wrote. The reducer
// is pure so the undo stack can be tested without a DOM.

import type { Verdict } from "./types";

export type Pick = Verdict;
export interface PicksState {
  picks: Record<string, Pick>;
  /** Undo stack, newest last: the photo and the pick it had before the change (undefined = no pick). */
  undo: { id: string; before?: Pick }[];
  redo: { id: string; before?: Pick }[];
}

export const EMPTY_PICKS: PicksState = { picks: {}, undo: [], redo: [] };
const UNDO_DEPTH = 200;

export type PicksAction = { type: "set"; id: string; pick: Pick } | { type: "clear"; id: string } | { type: "undo" } | { type: "redo" } | { type: "reset" } | { type: "load"; picks: Record<string, Pick> };

export function picksReducer(s: PicksState, a: PicksAction): PicksState {
  if (a.type === "reset") return EMPTY_PICKS;
  if (a.type === "load") return { picks: { ...a.picks }, undo: [], redo: [] };
  if (a.type === "set" || a.type === "clear") {
    const before = s.picks[a.id];
    const after = a.type === "set" ? a.pick : undefined;
    if (before === after) return s;
    const picks = { ...s.picks };
    if (after) picks[a.id] = after;
    else delete picks[a.id];
    return { picks, undo: [...s.undo, { id: a.id, before }].slice(-UNDO_DEPTH), redo: [] };
  }
  const from = a.type === "undo" ? s.undo : s.redo;
  const step = from[from.length - 1];
  if (!step) return s;
  const current = s.picks[step.id];
  const picks = { ...s.picks };
  if (step.before) picks[step.id] = step.before;
  else delete picks[step.id];
  const back = { id: step.id, before: current };
  return a.type === "undo"
    ? { picks, undo: s.undo.slice(0, -1), redo: [...s.redo, back] }
    : { picks, redo: s.redo.slice(0, -1), undo: [...s.undo, back] };
}

/** The photo the last undo/redo touched, so the view can jump back to it. */
export function lastTouched(prev: PicksState, next: PicksState): string | undefined {
  if (next.undo.length < prev.undo.length) return prev.undo[prev.undo.length - 1]?.id;
  if (next.redo.length < prev.redo.length) return prev.redo[prev.redo.length - 1]?.id;
  return undefined;
}

/** Auto-advance after a pick: the next frame in the burst without a pick of yours, wrapping once; else the
 *  next frame. Returns undefined at the end of a fully picked burst (the view then offers the next group). */
export function nextUnpicked(order: string[], at: string, picks: Record<string, Pick>): string | undefined {
  const i = order.indexOf(at);
  for (let k = 1; k < order.length; k++) {
    const id = order[(i + k) % order.length];
    if (!picks[id]) return id;
  }
  return i >= 0 && i < order.length - 1 ? order[i + 1] : undefined;
}

export const PICK_WORD: Record<Pick, string> = { keep: "Keep", reject: "Reject", review: "Review" };

// ------------------------------------------------------------------ persistence (browser only)

const key = (eventId: string) => `keepframe.picks.${eventId}`;

export function loadPicks(eventId: string): Record<string, Pick> {
  try {
    const raw = JSON.parse(window.localStorage.getItem(key(eventId)) ?? "{}") as Record<string, unknown>;
    return Object.fromEntries(Object.entries(raw).filter((e): e is [string, Pick] => e[1] === "keep" || e[1] === "reject" || e[1] === "review"));
  } catch {
    return {};
  }
}

export function savePicks(eventId: string, picks: Record<string, Pick>) {
  try {
    if (Object.keys(picks).length) window.localStorage.setItem(key(eventId), JSON.stringify(picks));
    else window.localStorage.removeItem(key(eventId));
  } catch {
    /* storage full or blocked: picks stay in memory for this tab */
  }
}

// The short "why" on a group card, and a categorical confidence chip (Confident / Close call / Unsure). Built
// from what the group list carries (verdicts, reason codes, rank, margin); no per-frame scores are needed.

import { CLOSE_BELOW, fmtGap, isTie } from "./closecall.ts";
import { frameNames } from "./cull.ts";
import type { Group } from "./types";

export type Confidence = "confident" | "close" | "unsure";

export const CONFIDENCE_WORD: Record<Confidence, string> = { confident: "Confident", close: "Close call", unsure: "Unsure" };

export const CONFIDENCE_HINT = `How clear the group's pick is (lead over the runner-up), not a frame's verdict: Confident ${CLOSE_BELOW * 100}+ pts · Close call under ${CLOSE_BELOW * 100} pts or tied · Unsure no pick yet or waiting for a person`;

type G = Pick<Group, "members" | "margin" | "tie" | "pending_approval">;

/** The group's decision confidence. "Unsure" only when nothing is picked or a person still has to decide: once a
 *  frame is the Keep / Best pick, a tie or a thin lead is a "Close call", so the chip never reads "Unsure" beside a
 *  frame the panel calls a confident Keep. */
export function confidenceOf(g: G): Confidence {
  const keeper = g.members.some((m) => m.is_best || m.verdict === "keep");
  if (!keeper || g.pending_approval) return "unsure";
  if (isTie(g) || (typeof g.margin === "number" && g.margin < CLOSE_BELOW)) return "close";
  return "confident";
}

// What sank the other frames, in the order a photographer cares about.
const FACTORS: [code: string, one: string, many: (n: number) => string][] = [
  ["EYES_CLOSED", "1 blink", (n) => `${n} blinks`],
  ["BLUR_MOTION", "1 motion blur", (n) => `${n} motion blur`],
  ["BLUR_FOCUS", "1 missed focus", (n) => `${n} missed focus`],
  ["EXPOSURE_UNDER", "1 too dark", (n) => `${n} too dark`],
  ["EXPOSURE_OVER", "1 too bright", (n) => `${n} too bright`],
  ["LOW_CONTRAST", "1 flat", (n) => `${n} flat`],
  ["DUPLICATE_OF", "1 duplicate", (n) => `${n} duplicates`],
];

const MAX_WORDS = 6;
const words = (s: string) => s.split(" ").filter((w) => w && w !== "·").length;

/** The card's one reason, at most 6 words: "2 blinks · leads by 2.7", "Leads by 20.0", "Tied with Frame 2". */
export function cardReason(g: G): string {
  const names = frameNames(g.members);
  const keeper = g.members.find((m) => m.is_best) ?? g.members.find((m) => m.verdict === "keep");
  if (!keeper) {
    const r = g.members.filter((m) => m.verdict === "review").length;
    return r ? `No pick · ${r} to review` : "No pick yet";
  }
  const others = g.members.filter((m) => m !== keeper);
  if (!others.length) return "Only frame · passes checks";
  const counts = new Map<string, number>();
  for (const m of others) for (const c of new Set((m.reason_codes ?? []).map((c) => c.split(":", 1)[0]))) counts.set(c, (counts.get(c) ?? 0) + 1);
  const factors = FACTORS.filter(([c]) => counts.get(c)).map(([c, one, many]) => ((counts.get(c) as number) === 1 ? one : many(counts.get(c) as number)));

  const runner = others.slice().sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9))[0];
  const gap = typeof g.margin === "number" ? g.margin * 100 : undefined;
  const tie = isTie(g);
  const tail = tie ? `tied with ${names[runner.photo_id]}` : gap !== undefined ? `leads by ${fmtGap(gap / 100).replace(" pts", "")}` : "";
  const close = tie || (gap ?? 99) < CLOSE_BELOW * 100;

  const parts: string[] = [];
  for (const f of factors) if (words([...parts, f].join(" · ")) <= MAX_WORDS) parts.push(f);
  if (!parts.length) parts.push(tail || "Top score");
  else if (tail && close && words([...parts, tail].join(" · ")) <= MAX_WORDS) parts.push(tail);
  const out = parts.join(" · ");
  return out[0].toUpperCase() + out.slice(1);
}

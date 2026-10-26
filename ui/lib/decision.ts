import type { DecisionSource } from "./types";

export const SCRIPTED_LABEL = "Answered automatically (test run)";
export const LEAD_TEST_LABEL = "Team mechanism test, not a photographer judgement";

/** Plain words for who decided. Only an explicit person channel is ever described as a person. */
export const SOURCE_WORD: Record<DecisionSource, string> = {
  agent: "by the agent",
  ui: "confirmed by a person in the review desk",
  file: "approved by a reviewer (imported decisions)",
  http: "approved by a reviewer",
  scripted: SCRIPTED_LABEL,
  lead_test: LEAD_TEST_LABEL,
  unknown: "confirmed outside the agent, source not recorded",
};

export const ANSWER_WORD: Record<DecisionSource, string> = {
  agent: "Agent answered",
  ui: "Person answered (review desk)",
  file: "Reviewer answered (imported)",
  http: "Reviewer answered",
  scripted: "Answered automatically (test run)",
  lead_test: "Team mechanism test",
  unknown: "Answered, source not recorded",
};

export function isScripted(s?: DecisionSource): boolean {
  return s === "scripted";
}

export function isLeadTest(s?: DecisionSource): boolean {
  return s === "lead_test";
}

/** Sources rendered as a badge instead of a sentence: neither is a photographer's judgement. */
export function isBadged(s?: DecisionSource): boolean {
  return s === "scripted" || s === "lead_test";
}

/** A person verdict, for any human-agreement figure. Scripted, lead_test and unknown answers never count. */
export function isPerson(s?: DecisionSource): boolean {
  return s === "ui" || s === "file" || s === "http";
}

function pctOf(v: number | undefined): string | null {
  return typeof v === "number" && Number.isFinite(v) ? `${Math.round(v * 100)}%` : null;
}

/** Agent verdicts: "86% confident, by the agent". Anything else: who confirmed it, plus the agent's own
 *  proposal confidence when known ("agent confidence 50%"). Never a made-up 100%. */
export function decisionLine(source: DecisionSource | undefined, confidence: number | undefined): string {
  const s = source ?? "agent";
  const p = pctOf(confidence);
  if (s === "agent") return p ? `${p} confident, ${SOURCE_WORD.agent}` : SOURCE_WORD.agent;
  return p ? `${SOURCE_WORD[s]}, agent confidence ${p}` : SOURCE_WORD[s];
}

export function approvalsSourceText(v?: string): string | null {
  if (!v) return null;
  if (v === "scripted") return "approvals answered automatically (test run)";
  if (v === "ui") return "approvals answered in the review desk";
  if (v === "file" || v === "http") return `approvals answered by a person (${v === "file" ? "decisions file" : "API"})`;
  return `approvals: ${v}`;
}

/** Run banner clause for the latest approval answer. Only non-person answers are called out. */
export function lastAnswerText(source?: DecisionSource): string | null {
  if (source === "lead_test") return "last answer: team mechanism test";
  if (source === "scripted") return "last answer: answered automatically (test run)";
  return null;
}

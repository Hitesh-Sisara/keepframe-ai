// One definition of "close call" for every page. The score is 0-1, so a margin reads as percentage points:
// 0.014 -> "1.4 pts apart". Below TIE_BELOW the two frames are shown as a tie.

export const CLOSE_BELOW = 0.05;
export const TIE_BELOW = 0.005;

interface Margined {
  margin?: number | null;
  tie?: boolean | null;
}

export function isClose(g: Margined): boolean {
  return Boolean(g.tie) || (typeof g.margin === "number" && g.margin < CLOSE_BELOW);
}

export function isTie(g: Margined): boolean {
  return Boolean(g.tie) || (typeof g.margin === "number" && g.margin < TIE_BELOW);
}

/** "1.4 pts" (one decimal, never "0.0": a sub-0.05-point gap is a tie and shown as such). */
export function gapText(margin: number): string {
  return `${(margin * 100).toFixed(1)} pts`;
}

/** A score gap in points out of 100, at a precision that never rounds a real gap to zero:
 *  0.0024 → "0.24 pts", 0.0042 → "0.42 pts", 0.014 → "1.4 pts", 0.00004 → "<0.01 pts". */
export function fmtGap(margin: number): string {
  const p = Math.abs(margin) * 100;
  if (p === 0) return "0 pts";
  if (p < 0.01) return "<0.01 pts";
  return `${p < 1 ? p.toFixed(2) : p.toFixed(1)} pts`;
}

/** Tag text for a group: "Tie", "Close call · 1.4 pts apart", "Clear pick · 12.0 pts apart", or "". */
export function closeCallText(g: Margined): string {
  if (isTie(g)) return "Tie";
  if (typeof g.margin !== "number") return "";
  return g.margin < CLOSE_BELOW ? `Close call · ${gapText(g.margin)} apart` : `Clear pick · ${gapText(g.margin)} apart`;
}

/** Tooltip for the tag. */
export const CLOSE_CALL_HINT = `How far the best frame's score is ahead of the runner-up, out of 100. Under ${CLOSE_BELOW * 100} points is a close call, and the agent asks a person.`;

/** Tooltip for a group's tag: ties say how the keeper was still chosen. */
export function closeCallHint(g: Margined): string {
  return isTie(g)
    ? "The top two frames scored the same to within half a point. The agent kept the frame it ranked first; a person can swap it."
    : CLOSE_CALL_HINT;
}

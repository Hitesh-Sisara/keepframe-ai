// Plain-language explanations for the measurement names and policy codes that show up in agent traces.
// One definition per term; the trace, the raw-data view and the bench all read from here.

/** `plain`: the everyday word shown in place of the raw name where people decide (the approvals desk). */
export const GLOSSARY: Record<string, { title: string; body: string; plain?: string }> = {
  lap_var: {
    title: "Sharpness (Laplacian variance)",
    plain: "sharpness",
    body: "How much fine edge detail the frame has. Higher means sharper. Compare frames in the same step, not across steps: a face crop scores much higher than a whole frame.",
  },
  tenengrad: {
    title: "Edge strength (Tenengrad)",
    plain: "edge strength",
    body: "A second sharpness check based on how strong the edges are. Higher means crisper edges. Used to confirm the Laplacian reading.",
  },
  brisque: {
    title: "Overall image quality (BRISQUE)",
    plain: "image quality",
    body: "A no-reference quality score that looks for noise, blur and compression damage. Lower is better.",
  },
  margin: {
    title: "Gap to the runner-up",
    body: "How far the best frame's score is ahead of the next best, in points out of 100. Under 5 points counts as a close call and goes to a person.",
  },
  fft_hf: {
    title: "Fine-detail energy",
    plain: "fine detail",
    body: "How much of the image is very fine detail. Low values point to blur.",
  },
  motion_ratio: {
    title: "Motion streak ratio",
    plain: "motion streaks",
    body: "How much stronger edges are in one direction than the other. High values mean motion blur rather than missed focus.",
  },
  open_p: {
    title: "Eyes-open score (left/right)",
    plain: "eyes-open score",
    body: "How likely each eye is open, from 0 (closed) to 100 (open), written left/right, the same scale as the Eyes open bar. Under about 30 counts as closed.",
  },
  fiqa: {
    title: "Face quality",
    plain: "face quality",
    body: "How usable the face crop is (sharpness, pose, lighting), from 0 to 1. Higher is better.",
  },
  clip_lo: {
    title: "Shadow clipping",
    plain: "shadow clipping",
    body: "Share of pixels crushed to pure black. A little is normal; a lot means lost shadow detail.",
  },
  clip_hi: {
    title: "Highlight clipping",
    plain: "highlight clipping",
    body: "Share of pixels blown out to pure white. A lot means lost highlight detail.",
  },
  mean_L: {
    title: "Brightness (mean lightness)",
    plain: "brightness",
    body: "Average lightness of the frame, from 0 (black) to 255 (white).",
  },
  std_L: {
    title: "Contrast (lightness spread)",
    plain: "contrast",
    body: "How spread out the lightness values are. Low values mean a flat, low-contrast frame.",
  },
};

/** The agent's policy rules (docs/AGENT.md), each as one plain sentence: the rule, then what it does. */
export const POLICIES: Record<number, { name: string; body: string }> = {
  1: { name: "group first", body: "Score every frame of a burst before picking one, so frames are compared side by side." },
  2: { name: "borderline sharpness → look closer", body: "When sharpness is borderline, measure again on the faces, then on the full-resolution original, before calling it blurry." },
  3: { name: "check eyes face by face", body: "Only reject for closed eyes when the main subject's eyes are clearly closed. A blink in the background sends the frame to review instead." },
  4: { name: "never reject a whole burst", body: "At least one frame of every burst is kept, even when they all have a flaw." },
  5: { name: "close call → ask a person", body: "When the best frame is less than 5 points ahead, or the agent is unsure about a reject, it pauses and asks a person." },
  6: { name: "respect artistic intent", body: "Deliberate slow-shutter blur or dark mood lighting goes to review, not reject." },
  7: { name: "stay on budget", body: "Optional extra checks are skipped when the run is nearly out of time or tool calls." },
  8: { name: "never delete", body: "Verdicts are keep, review or reject. Nothing is ever deleted, and a reject can be undone." },
  9: { name: "explain every verdict", body: "Every verdict carries a reason code and a short reason written from the measured numbers." },
};

export type GlossPart = { text: string; term?: string; policy?: number };

// "Policy 5", "Policy 1/3", "P5", "P1–P7", plus the measurement names written either as keys or in prose.
const PATTERN =
  /\b(?:Policy|Policies|P)\s?([1-9])(?:\s?[/–-]\s?P?([1-9]))?\b|\b(lap_var|tenengrad|fft_hf|motion_ratio|open_p|fiqa|clip_(?:lo|hi)(?:_pct)?|mean_L|std_L|BRISQUE|brisque|Laplacian variance|Laplacian(?=, Sobel))\b/g;

/** Raw match → GLOSSARY key. */
function termKey(t: string): string {
  if (t.startsWith("Laplacian")) return "lap_var";
  if (t === "mean_L" || t === "std_L") return t;
  return t.toLowerCase().replace(/_pct$/, "");
}

/** Split free text into plain runs and glossary hits, so the caller can wrap the hits in a tooltip. */
export function glossParts(text: string): GlossPart[] {
  const out: GlossPart[] = [];
  let last = 0;
  for (const m of text.matchAll(PATTERN)) {
    const i = m.index ?? 0;
    if (i > last) out.push({ text: text.slice(last, i) });
    if (m[1] && m[2]) {
      // "Policy 1/3": each number gets its own tooltip.
      const firstEnd = m[0].indexOf(m[1]) + 1;
      out.push({ text: m[0].slice(0, firstEnd), policy: Number(m[1]) });
      out.push({ text: m[0].slice(firstEnd, -1) });
      out.push({ text: m[2], policy: Number(m[2]) });
    } else if (m[1]) {
      out.push({ text: m[0], policy: Number(m[1]) });
    } else {
      const t = m[3];
      out.push({ text: t, term: termKey(t) });
    }
    last = i + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out.filter((p) => p.text);
}

/** "Policy 5: close call → ask a person". */
export function policyTitle(n: number): string {
  const p = POLICIES[n];
  return p ? `Policy ${n}: ${p.name}` : `Policy ${n}`;
}

/** "Laplacian variance 4229" → "4,229", so free text matches the bars. Leaves years, ids and decimals alone. */
export function groupDigits(text: string): string {
  return text.replace(/(?<![\w.,])(\d{4,})(?![\w.,]\d)/g, (n) => (/^(19|20)\d\d$/.test(n) ? n : Number(n).toLocaleString("en-GB")));
}

// Eyes-open readings in agent text are left/right pairs on 0-1 ("eyes open (0.94/0.81)", "open_p 0.10/0.01",
// "(f1, 0.0001/0.008)"); the agent writes no other value as a slash pair. A range follows open_p ("0.15-0.21").
const UNIT = String.raw`(?:0?\.\d+|1(?:\.0+)?|0)`;
const EYE_PAIR = new RegExp(String.raw`(?<![\w.])(${UNIT})/(${UNIT})(?![\w.]*\d)`, "g");
const EYE_RANGE = new RegExp(String.raw`\b(open[_ ]p\s+)(${UNIT})-(${UNIT})(?![\w.]*\d)`, "g");
const pct = (v: string) => String(Math.round(Number(v) * 100));

/** Eyes-open readings on the 0-100 scale the Eyes open bars use: "eyes open (0.94/0.81)" → "eyes open (94/81)",
 *  "open_p 0.15-0.21" → "open_p 15-21". Other numbers (scores, lap_var, fiqa, margins) are left alone. */
export function eyesTo100(text: string): string {
  return text.replace(EYE_RANGE, (_m, pre: string, a: string, b: string) => `${pre}${pct(a)}-${pct(b)}`).replace(EYE_PAIR, (_m, a: string, b: string) => `${pct(a)}/${pct(b)}`);
}

/** Free text with raw measurement names swapped for their plain words ("lap_var 1231" → "sharpness 1231"), for
 *  places that need a plain string (inbox lines, toasts). Glossed with `plain` does the same with tooltips. */
export function plainTerms(text: string): string {
  return glossParts(text)
    .map((p) => (p.term && GLOSSARY[p.term]?.plain) || p.text)
    .join("");
}

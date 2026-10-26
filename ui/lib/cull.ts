// Display-only helpers for the cull view (groups page). Pure functions so they can be unit-tested with
// node --test; nothing here changes the API contract.

import { fmtGap } from "./closecall.ts";
import type { Member, Pivot, TraceLine, Verdict } from "./types";

// ------------------------------------------------------------------ scores

export interface FaceInfo {
  face_id: string;
  /** [x, y, w, h] in pixels of the processed image the worker analysed (not the 1024-px proxy). */
  bbox_px?: number[];
  min_px?: number;
  state?: string;
  left_open_p?: number | null;
  right_open_p?: number | null;
  fiqa?: number | null;
  main_subject: boolean;
}

export interface Scores {
  lap_var?: number;
  tenengrad?: number;
  fft_hf?: number;
  brisque?: number;
  sharp_label?: string;
  blur_kind?: string | null;
  roi_used?: string;
  roi_rect?: number[];
  mean_L?: number;
  clip_hi_pct?: number;
  clip_lo_pct?: number;
  exposure_label?: string;
  breakdown: Record<string, number>;
  best_score?: number;
  faces: FaceInfo[];
}

const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

/** Worker rows nest scores (sharpness.*, exposure.*, eyes.per_face, face_quality.per_face, faces[]); fixtures are
 *  flat. Normalise both into one shape. */
export function normalizeScores(raw: Record<string, unknown> | undefined, breakdown?: Record<string, number>): Scores {
  const r = raw ?? {};
  const sh = obj(r.sharpness);
  const ex = obj(r.exposure);
  const pick = (k: string, ...srcs: Record<string, unknown>[]) => {
    for (const s of srcs) if (num(s[k]) !== undefined) return num(s[k]);
    return undefined;
  };
  // Nested exposure stores clipping as a fraction; flat fixtures store a percentage already.
  const clip = (k: string) => (num(ex[k]) !== undefined ? Math.round((num(ex[k]) as number) * 1000) / 10 : num(r[k]));
  const bd = { ...(breakdown ?? {}), ...(obj(r.breakdown) as Record<string, number>) };

  let faces: FaceInfo[] = [];
  if (Array.isArray(r.faces)) {
    faces = (r.faces as Record<string, unknown>[]).map((f) => ({
      face_id: String(f.face_id),
      bbox_px: Array.isArray(f.bbox_px) ? (f.bbox_px as number[]) : undefined,
      min_px: num(f.min_px) ?? num(f.minPx),
      state: typeof f.state === "string" ? f.state : undefined,
      left_open_p: num(f.left_open_p) ?? null,
      right_open_p: num(f.right_open_p) ?? null,
      fiqa: num(f.fiqa) ?? null,
      main_subject: Boolean(f.main_subject ?? f.is_main),
    }));
  } else {
    const eyes = (obj(r.eyes).per_face as Record<string, unknown>[] | undefined) ?? [];
    const fq = new Map(((obj(r.face_quality).per_face as Record<string, unknown>[] | undefined) ?? []).map((f) => [f.face_id, f]));
    faces = eyes.map((e) => ({
      face_id: String(e.face_id),
      min_px: num(e.min_px),
      state: typeof e.state === "string" ? e.state : undefined,
      left_open_p: num(e.left_open_p) ?? null,
      right_open_p: num(e.right_open_p) ?? null,
      fiqa: num(fq.get(e.face_id)?.fiqa) ?? null,
      main_subject: Boolean(e.is_main),
    }));
  }
  // Left to right in the frame when boxes are known (so chips match the boxes), else main subject first.
  if (faces.every((f) => f.bbox_px)) faces.sort((a, b) => (a.bbox_px as number[])[0] - (b.bbox_px as number[])[0]);
  else faces.sort((a, b) => Number(b.main_subject) - Number(a.main_subject));

  return {
    lap_var: pick("lap_var", sh, r),
    tenengrad: pick("tenengrad", sh, r),
    fft_hf: pick("fft_hf", sh, r),
    brisque: pick("brisque", sh, r),
    sharp_label: typeof sh.label === "string" ? sh.label : undefined,
    blur_kind: typeof sh.blur_kind === "string" ? sh.blur_kind : null,
    roi_used: typeof sh.roi_used === "string" ? sh.roi_used : undefined,
    roi_rect: Array.isArray(sh.roi_rect) ? (sh.roi_rect as number[]) : undefined,
    mean_L: pick("mean_L", ex, r),
    clip_hi_pct: clip("clip_hi_pct"),
    clip_lo_pct: clip("clip_lo_pct"),
    exposure_label: typeof ex.label === "string" ? ex.label : undefined,
    breakdown: bd,
    best_score: num(r.best_score),
    faces,
  };
}

export interface ScoreBar {
  key: "sharpness" | "exposure" | "eyes" | "face";
  label: string;
  /** 0..1, or undefined when the frame has no value (bar shows as empty, with the reason). */
  value?: number;
  text: string;
  tone: "good" | "warn" | "bad" | "none";
  hint: string;
}

const SHARP_WORD: Record<string, string> = { sharp: "Sharp", borderline: "Borderline", blurry: "Blurry", blur: "Blurry" };
const EXPO_WORD: Record<string, string> = { ok: "Well exposed", under: "Underexposed", over: "Overexposed", low_contrast: "Low contrast" };
const EYE_WORD: Record<string, string> = { open: "open", partial: "half open", closed: "closed", unknown: "not readable" };

/** One scale for every value-driven bar. */
const toneOf = (v?: number): ScoreBar["tone"] => (v === undefined ? "none" : v >= 0.55 ? "good" : v >= 0.3 ? "warn" : "bad");

/** The four quality bars a photographer reads first. Values come from the ranking breakdown the agent used. */
export function scoreBars(s: Scores): ScoreBar[] {
  const bd = s.breakdown;
  const main = s.faces.find((f) => f.main_subject) ?? s.faces[0];
  const sharpV = num(bd.sharp_faces_norm) ?? num(bd.sharpness_norm);
  const blur = s.blur_kind ? ` (${s.blur_kind === "motion" ? "motion blur" : "focus blur"})` : "";
  const sharpWord = s.sharp_label ? `${SHARP_WORD[s.sharp_label] ?? s.sharp_label}${blur}` : sharpV === undefined ? "Not measured" : "";
  const expoV = num(bd.exposure_ok);
  const eyesV = num(bd.eyes_open_mean);
  const fqV = num(bd.fiqa_mean);
  const noFaces = s.faces.length === 0;
  const mainKnown = main?.state && main.state !== "unknown";
  return [
    {
      key: "sharpness",
      label: "Sharpness",
      value: sharpV,
      text: sharpWord || "—",
      // The worse of what the label says and where the frame sits in its burst.
      tone: worst(s.sharp_label === "borderline" ? "warn" : s.sharp_label === "blurry" ? "bad" : s.sharp_label === "sharp" ? "good" : "none", toneOf(sharpV)),
      hint: `Relative to the sharpest frame in this burst${s.roi_used === "faces" ? ", measured on the faces" : ""}.`,
    },
    {
      key: "exposure",
      label: "Exposure",
      value: expoV,
      text: joinText(
        s.exposure_label ? (EXPO_WORD[s.exposure_label] ?? s.exposure_label) : expoV !== undefined ? (expoV >= 0.5 ? "Well exposed" : "Off") : "Not measured",
        undefined,
      ),
      tone: s.exposure_label ? (s.exposure_label === "ok" ? "good" : "bad") : toneOf(expoV),
      hint:
        s.clip_hi_pct !== undefined || s.clip_lo_pct !== undefined
          ? `Highlights clipped ${fmt1(s.clip_hi_pct)}%, shadows clipped ${fmt1(s.clip_lo_pct)}%.`
          : "Whether highlights and shadows hold detail.",
    },
    {
      key: "eyes",
      label: "Eyes open",
      value: noFaces ? undefined : eyesV,
      text: noFaces ? "No faces" : [typeof eyesV === "number" ? `avg ${Math.round(eyesV * 100)}%` : "", mainKnown ? `main ${EYE_WORD[main.state as string] ?? main.state}` : "main not readable"].filter(Boolean).join(" · "),
      // The main subject's eye state decides the tone; the bar is the average over every readable face.
      tone: noFaces
        ? "none"
        : main?.state === "closed"
          ? "bad"
          : main?.state === "partial"
            ? "warn"
            : main?.state === "open"
              ? "good"
              : toneOf(eyesV),
      hint: "Bar: average eye-open probability over the faces the model could read. Colour: the main subject.",
    },
    {
      key: "face",
      label: "Face quality",
      value: noFaces ? undefined : fqV,
      text: noFaces ? "No faces" : fqV !== undefined ? `avg ${Math.round(fqV * 100)}% · ${fqV >= 0.55 ? "good" : fqV >= 0.3 ? "fair" : "poor"}` : "Not measured",
      tone: noFaces ? "none" : toneOf(fqV),
      hint: "Face image quality (focus, pose, occlusion) averaged over the faces.",
    },
  ];
}

const RANK: Record<ScoreBar["tone"], number> = { none: 0, good: 1, warn: 2, bad: 3 };
function worst(a: ScoreBar["tone"], b: ScoreBar["tone"]): ScoreBar["tone"] {
  return RANK[a] >= RANK[b] ? a : b;
}

function joinText(word: string, v?: number): string {
  const p = typeof v === "number" ? `${Math.round(v * 100)}%` : "";
  return [word, p].filter(Boolean).join(" · ") || "—";
}

function fmt1(v?: number): string {
  return typeof v === "number" ? v.toFixed(1) : "—";
}

// ------------------------------------------------------------------ face crops

const LONG_EDGES = [1920, 2048, 2560, 1600, 1365, 1280, 1024];
const FACE_ROI_EXPAND = 0.2; // config/thresholds.yaml sharpness.face_roi_expand

/** Scale from bbox_px (processed-image pixels) to proxy pixels. Uses the sharpness faces-ROI (proxy pixels)
 *  when present; otherwise the smallest common processed size the boxes fit in. */
export function faceScale(s: Scores, proxyW: number, proxyH: number): number {
  const boxes = s.faces.map((f) => f.bbox_px).filter((b): b is number[] => Array.isArray(b) && b.length >= 4);
  if (!boxes.length || !proxyW || !proxyH) return 1;
  const longProxy = Math.max(proxyW, proxyH);
  const candidates = LONG_EDGES.map((L) => longProxy / L);
  if (s.roi_used === "faces" && s.roi_rect && s.roi_rect.length >= 4) {
    const x0 = Math.min(...boxes.map(([x, , w]) => x - (w * FACE_ROI_EXPAND) / 2));
    const x1 = Math.max(...boxes.map(([x, , w]) => x + w + (w * FACE_ROI_EXPAND) / 2));
    const [rx, , rw] = s.roi_rect;
    const clipped = x0 <= 0 || rx + rw >= proxyW - 1;
    const measured = clipped ? (x0 > 0 ? rx / x0 : 0) : rw / (x1 - x0);
    if (measured > 0) {
      const near = candidates.reduce((a, b) => (Math.abs(b - measured) < Math.abs(a - measured) ? b : a));
      return Math.abs(near - measured) / measured < 0.04 ? near : measured;
    }
  }
  const maxX = Math.max(...boxes.map(([x, , w]) => x + w));
  const maxY = Math.max(...boxes.map(([, y, , h]) => y + h));
  const fits = candidates.filter((k) => maxX * k <= proxyW + 1 && maxY * k <= proxyH + 1).sort((a, b) => b - a);
  return fits[0] ?? Math.min(proxyW / maxX, proxyH / maxY);
}

/** Square crop around a face box (proxy pixels), padded so the chip shows the whole head. */
export function faceCrop(bbox: number[], scale: number, pad = 0.22): { cx: number; cy: number; side: number } {
  const [x, y, w, h] = bbox.map((v) => v * scale);
  // Detector boxes on large faces run tall (hair to chin); centre slightly high and size on the width so the
  // chip frames the face, not the shoulders.
  const side = Math.min(Math.max(w, h), w * 1.25) * (1 + 2 * pad);
  return { cx: x + w / 2, cy: y + Math.min(h, w * 1.25) / 2, side };
}

// ------------------------------------------------------------------ reasons

/** "Auto-approved by a script (no human review). Keep: best frame of its group [sharpness 43904 (faces)]" →
 *  "Best frame of its group." The bracketed numbers move to the score bars; who decided is shown separately. */
export function plainReason(text?: string): string {
  if (!text) return "";
  let t = text.trim();
  if (/^Decided by a person:/i.test(t)) return "";
  t = t.replace(/^Auto-approved by a script \(no human review\)\.\s*/i, "");
  t = t.replace(/\s*\[[^\]]*\]\s*$/, "");
  t = t.replace(/^(Keep|Reject|Review):\s*/i, "");
  t = t.replace(/\bnear-identical to (p_[A-Za-z0-9]+)/g, "near-identical to $1");
  if (!t) return "";
  t = t[0].toUpperCase() + t.slice(1);
  return /[.!?]$/.test(t) ? t : `${t}.`;
}

/** Reason filter key: DUPLICATE_OF:p_0049 collapses to DUPLICATE_OF. */
export function reasonBase(code: string): string {
  return code.split(":", 1)[0];
}

// ------------------------------------------------------------------ grid

export type VerdictFilter = "all" | Verdict;
export type GroupSort = "capture" | "close" | "review" | "size";

export interface GroupLike {
  group_id: string;
  group_label?: string;
  size: number;
  margin?: number;
  members: Pick<Member, "photo_id" | "verdict" | "reason_codes">[];
  verdict_counts?: Partial<Record<Verdict, number>>;
}

/** Capture-order index of a group: the trailing number of g_<event>_<n>, else of its label ("Group 12"). Judge
 *  views get hashed aliases (gx_3e26…), so the label is the only order signal there; else list order. */
export function groupIndex(id: string, label?: string): number {
  // Alias ids (gx_<hex>) can end in all-digit hex ("gx_0843288799"), so only g_<event>_<n> ids are trusted.
  const m = (/^g_.+_(\d+)$/.exec(id) ?? null) || (label ? /(\d+)\s*$/.exec(label) : null);
  return m ? Number(m[1]) : Number.MAX_SAFE_INTEGER;
}

export function sortGroups<T extends GroupLike>(groups: T[], sort: GroupSort): T[] {
  const out = groups.map((g, i) => ({ g, i }));
  const cap = (a: { g: T; i: number }, b: { g: T; i: number }) => groupIndex(a.g.group_id, a.g.group_label) - groupIndex(b.g.group_id, b.g.group_label) || a.i - b.i;
  const review = (g: T) => g.verdict_counts?.review ?? g.members.filter((m) => m.verdict === "review").length;
  const margin = (g: T) => (typeof g.margin === "number" ? g.margin : Number.POSITIVE_INFINITY);
  out.sort((a, b) => {
    if (sort === "close") return margin(a.g) - margin(b.g) || cap(a, b);
    if (sort === "review") return review(b.g) - review(a.g) || margin(a.g) - margin(b.g) || cap(a, b);
    if (sort === "size") return b.g.size - a.g.size || cap(a, b);
    return cap(a, b);
  });
  return out.map((x) => x.g);
}

export function memberMatches(m: Pick<Member, "verdict" | "reason_codes">, verdict: VerdictFilter, reason: string): boolean {
  if (verdict !== "all" && m.verdict !== verdict) return false;
  if (reason && !(m.reason_codes ?? []).some((c) => reasonBase(c) === reason)) return false;
  return true;
}

/** Capture order inside a burst: photo ids are assigned in capture order (p_0068, p_0069, …). Hashed aliases
 *  (judge view, ph_f0e4…) carry no order, so those keep the API's order. */
export function captureOrder<T extends { photo_id: string }>(members: T[]): T[] {
  if (!members.every((m) => /^p_\d+$/.test(m.photo_id))) return [...members];
  return [...members].sort((a, b) => a.photo_id.localeCompare(b.photo_id, "en", { numeric: true }));
}

/** How a burst's frames are ordered on screen: by capture when the ids carry it, else by score rank (judge view). */
export function frameOrder<T extends { photo_id: string; rank?: number }>(members: T[]): { frames: T[]; by: "capture" | "rank" } {
  if (members.length && members.every((m) => /^p_\d+$/.test(m.photo_id))) return { frames: captureOrder(members), by: "capture" };
  return { frames: [...members].sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9)), by: "rank" };
}

/** Display name per photo id: "Frame N" by that order. */
export function frameNames<T extends { photo_id: string; rank?: number }>(members: T[]): Record<string, string> {
  const { frames } = frameOrder(members);
  return Object.fromEntries(frames.map((m, i) => [m.photo_id, `Frame ${i + 1}`]));
}

/** Short mono alias for a photo id: p_0068 stays, long aliases (ph_2eccb434ad) become their first 6 chars. */
export function shortPhoto(id: string): string {
  const m = /^([a-z]+)_([0-9a-z]+)$/i.exec(id);
  if (!m) return id.slice(0, 8);
  return m[2].length > 6 ? m[2].slice(0, 6) : id;
}

/** Replace raw photo ids in free text (p_0069, ph_2eccb434ad) with display names; unknown ones become "a frame". */
export function nameIdsInText(text: string, names: Record<string, string>): string {
  return text.replace(/\b(?:p|ph)_[0-9a-f]{4,}\b/gi, (id) => names[id] ?? "a frame");
}

/** Agent text writes score gaps as fractions ("margin 0.03 < 0.05"); the UI uses points out of 100 everywhere. */
export function marginsToPts(text: string, exact?: number): string {
  return text.replace(/\bmargin (0?\.\d+)(\s*<\s*(0?\.\d+))?/gi, (_m, a: string, _r, b?: string) => {
    // The agent rounds (0.03, or even 0.00 for 0.004). When the group's exact margin rounds to the agent's text,
    // show the exact one so the trace and the header agree and a real gap never reads "0.0".
    const dp = a.split(".")[1]?.length ?? 2;
    const close = typeof exact === "number" && exact > 0 && Math.abs(exact - Number(a)) <= 0.5 * 10 ** -dp + 1e-12;
    const v = close ? (exact as number) : Number(a);
    const txt = v === 0 ? `under ${+(0.5 * 10 ** -dp * 100).toFixed(4)} pts` : fmtGap(v);
    return `gap ${txt}${b ? ` (under ${(Number(b) * 100).toFixed(0)})` : ""}`;
  });
}

// ------------------------------------------------------------------ signed URLs

/** Earliest `exp` (unix seconds) across signed image URLs, or undefined when none carry one. */
export function earliestExpiry(urls: (string | undefined)[]): number | undefined {
  let min: number | undefined;
  for (const u of urls) {
    if (!u) continue;
    const m = /[?&](?:exp|Expires|X-Amz-Expires)=(\d+)/.exec(u);
    if (!m) continue;
    let v = Number(m[1]);
    // X-Amz-Expires is a duration; ignore it (no issue time to anchor), only absolute stamps count.
    if (u.includes("X-Amz-Expires=") && !u.includes("exp=")) continue;
    if (v < 1e9) continue;
    v = Math.floor(v);
    if (min === undefined || v < min) min = v;
  }
  return min;
}

// ------------------------------------------------------------------ trace

export type TraceRow =
  | { kind: "line"; line: TraceLine; attached: boolean }
  | { kind: "pivot"; pivot: Pivot; index: number; rows: { line: TraceLine; attached: boolean }[] };

/** Trace lines in order, with each pivot's lines (result → reasoning/helpers → changed call) bracketed. A
 *  tool_result directly after its own call is "attached" so the timeline draws them as one step. */
export function traceRows(lines: TraceLine[], pivots: Pivot[]): TraceRow[] {
  const sorted = [...lines].sort((a, b) => a.seq - b.seq);
  const attached = (i: number) => {
    const l = sorted[i];
    const prev = sorted[i - 1];
    return l.type === "tool_result" && !!prev && prev.type === "tool_call" && prev.tool === l.tool;
  };
  const ordered = [...pivots].sort((a, b) => a.result_seq - b.result_seq);
  const out: TraceRow[] = [];
  let p = 0;
  let afterPivot = false;
  for (let i = 0; i < sorted.length; i++) {
    const l = sorted[i];
    while (p < ordered.length && ordered[p].result_seq < l.seq) p++;
    const pv = ordered[p];
    if (pv && pv.result_seq === l.seq && pv.call_seq >= pv.result_seq) {
      const rows: { line: TraceLine; attached: boolean }[] = [];
      let j = i;
      while (j < sorted.length && sorted[j].seq <= pv.call_seq) {
        rows.push({ line: sorted[j], attached: j > i && attached(j) });
        j++;
      }
      out.push({ kind: "pivot", pivot: pv, index: p + 1, rows });
      p++;
      i = j - 1;
      afterPivot = true;
      continue;
    }
    // A result whose call sits inside the pivot box above is drawn as its own step, not glued to the box.
    out.push({ kind: "line", line: l, attached: !afterPivot && attached(i) });
    afterPivot = false;
  }
  return out;
}

/** "+0.32 s" offset from the first trace line, which reads better than identical wall-clock stamps. */
export function offsetText(ts: string | undefined, t0: number | undefined): string {
  if (!ts || t0 === undefined) return "";
  const t = Date.parse(ts);
  if (Number.isNaN(t)) return "";
  const d = (t - t0) / 1000;
  return `+${Math.max(0, d).toFixed(2)} s`;
}

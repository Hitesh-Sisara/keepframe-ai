// Face strip: the same person across every frame of a burst. Face ids are per frame (f0 in one frame can be a
// different person in the next), so faces are matched by where they sit in the frame.

import type { FaceInfo } from "./cull";

export interface FaceCell {
  face: FaceInfo;
  /** "closed" = a blink, "partial" = one eye or half-closed, "open", or "unknown" (too small to read). */
  eyes: "open" | "partial" | "closed" | "unknown";
}

export interface FaceRow {
  /** True when this person is the main subject in most frames. */
  main: boolean;
  /** One cell per frame, in the frame order passed in; undefined when the face wasn't found in that frame. */
  cells: (FaceCell | undefined)[];
}

const centre = (b: number[]) => [b[0] + b[2] / 2, b[1] + b[3] / 2];

function eyesOf(f: FaceInfo): FaceCell["eyes"] {
  if (f.state === "open" || f.state === "partial" || f.state === "closed") return f.state;
  return "unknown";
}

/** Match faces across frames to the frame with the most faces. A face matches when its centre is within 1.2
 *  face-widths of the reference face's centre; each face is used once (closest pairs first). */
export function matchFaces(frames: FaceInfo[][]): FaceRow[] {
  const boxed = frames.map((fs) => fs.filter((f) => Array.isArray(f.bbox_px) && f.bbox_px.length >= 4));
  const refIdx = boxed.reduce((best, fs, i) => (fs.length > boxed[best].length ? i : best), 0);
  const ref = boxed[refIdx] ?? [];
  if (!ref.length) return [];
  const rows: FaceRow[] = ref.map(() => ({ main: false, cells: frames.map(() => undefined) }));
  boxed.forEach((fs, fi) => {
    const pairs: { r: number; f: number; d: number }[] = [];
    ref.forEach((rf, r) => {
      const [rx, ry] = centre(rf.bbox_px as number[]);
      const lim = 1.2 * (rf.bbox_px as number[])[2];
      fs.forEach((f, k) => {
        const [x, y] = centre(f.bbox_px as number[]);
        const d = Math.hypot(x - rx, y - ry);
        if (d <= lim) pairs.push({ r, f: k, d });
      });
    });
    pairs.sort((a, b) => a.d - b.d);
    const usedR = new Set<number>();
    const usedF = new Set<number>();
    for (const p of pairs) {
      if (usedR.has(p.r) || usedF.has(p.f)) continue;
      usedR.add(p.r);
      usedF.add(p.f);
      rows[p.r].cells[fi] = { face: fs[p.f], eyes: eyesOf(fs[p.f]) };
    }
  });
  for (const row of rows) {
    const seen = row.cells.filter(Boolean) as FaceCell[];
    row.main = seen.filter((c) => c.face.main_subject).length * 2 > seen.length;
  }
  // Main subject first, then left to right.
  const x = (row: FaceRow) => (row.cells.find(Boolean)?.face.bbox_px as number[])[0];
  return rows.sort((a, b) => Number(b.main) - Number(a.main) || x(a) - x(b));
}

/** The best pick still shows a closed eye on the main face, and so does every other frame that has that face:
 *  the keep is the best available, not a clean frame. Index of the face row (0-based) or -1. */
export function bestAvailableFace(rows: FaceRow[], bestCol: number): number {
  if (bestCol < 0) return -1;
  const r = rows.findIndex((row) => row.main);
  if (r < 0) return -1;
  const seen = rows[r].cells.filter(Boolean) as FaceCell[];
  const shut = (c?: FaceCell) => c?.eyes === "closed" || c?.eyes === "partial";
  return shut(rows[r].cells[bestCol]) && seen.length > 0 && seen.every(shut) ? r : -1;
}

/** Frames (by index) where the row's person blinked. */
export function blinkFrames(row: FaceRow): number[] {
  return row.cells.flatMap((c, i) => (c?.eyes === "closed" ? [i] : []));
}

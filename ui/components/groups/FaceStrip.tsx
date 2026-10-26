"use client";

import { useMemo } from "react";
import { faceCrop, faceScale, normalizeScores, type FaceInfo } from "@/lib/cull";
import { matchFaces, type FaceCell, type FaceRow } from "@/lib/facestrip";
import type { Member } from "@/lib/types";
import { SignedImg } from "../Frame";

/** A face cropped out of a proxy frame, from the worker's face box (display-only; nothing is re-computed). */
export function FaceCrop({ src, face, scale, proxy, size }: { src: string; face: FaceInfo; scale: number; proxy?: [number, number]; size: number }) {
  if (!face.bbox_px || !proxy) return <span aria-hidden="true" className="kf-skeleton absolute inset-0 !rounded-none" />;
  const { cx, cy, side } = faceCrop(face.bbox_px, scale, 0.28);
  const z = size / side;
  return (
    <div className="absolute" style={{ width: proxy[0] * z, height: proxy[1] * z, left: -(cx - side / 2) * z, top: -(cy - side / 2) * z, maxWidth: "none" }}>
      <SignedImg src={src} alt="" onDark />
    </div>
  );
}

const EYES: Record<FaceCell["eyes"], { word: string; color: string }> = {
  open: { word: "Eyes open", color: "#4cc38a" },
  partial: { word: "One eye shut", color: "#f0a43a" },
  closed: { word: "Blink", color: "#ff6b6b" },
  unknown: { word: "Too small", color: "rgb(255 255 255 / 0.6)" },
};

export function EyeGlyph({ eyes, className = "h-3 w-3" }: { eyes: FaceCell["eyes"]; className?: string }) {
  const p = { fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true">
      {eyes === "open" && (
        <>
          <path {...p} d="M1.8 8s2.3-4.2 6.2-4.2S14.2 8 14.2 8s-2.3 4.2-6.2 4.2S1.8 8 1.8 8z" />
          <circle cx="8" cy="8" r="1.9" fill="currentColor" />
        </>
      )}
      {eyes === "closed" && <path {...p} d="M2 7c1.6 2.2 3.6 3.3 6 3.3S12.4 9.2 14 7M4.2 9.4 3 11.2M8 10.4v2M11.8 9.4l1.2 1.8" />}
      {eyes === "partial" && (
        <>
          <path {...p} d="M1.5 8.5s1.3-2.6 3.3-2.6 3.2 2.6 3.2 2.6" />
          <path {...p} d="M9 8.2c.9 1 1.9 1.6 3 1.6s2.1-.6 3-1.6" />
        </>
      )}
      {eyes === "unknown" && <path {...p} d="M5.8 6.2a2.2 2.2 0 1 1 3 2c-.6.3-.8.7-.8 1.4M8 12.2v.1" />}
    </svg>
  );
}

/** Faces × frames: every person in the burst, cropped from every frame, with an eyes badge. The AI pick's column
 *  is outlined in gold, the frame on screen in blue; click a face to show its frame. */
export function FaceStrip({
  frames,
  names,
  proxySizes,
  selected,
  bestId,
  onSelect,
}: {
  frames: Member[];
  names: Record<string, string>;
  proxySizes: Record<string, [number, number]>;
  selected?: string;
  bestId?: string;
  onSelect: (id: string) => void;
}) {
  const scores = useMemo(() => frames.map((m) => normalizeScores(m.scores as Record<string, unknown> | undefined, m.breakdown)), [frames]);
  const rows = useMemo(() => matchFaces(scores.map((s) => s.faces)), [scores]);
  if (!rows.length) return null;
  const bestCol = frames.findIndex((m) => m.photo_id === bestId);
  const note = pickNote(rows, frames, bestCol, names);
  const cell = frames.length > 5 ? 64 : 76;
  const shown = rows.slice(0, 4);
  return (
    <section aria-labelledby="kf-faces-h" className="border-t border-white/10 bg-[#1a1c21] px-3 pt-3 pb-3.5">
      <div className="mb-2.5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 id="kf-faces-h" className="text-[0.8125rem] font-semibold text-white" title={note || undefined}>
          Faces
        </h3>
        {note && <span className="sr-only">{note}</span>}
      </div>
      <div className="overflow-x-auto pb-1 [scrollbar-color:#4a4f59_transparent]">
        <table className="border-separate border-spacing-x-1.5 border-spacing-y-1">
          <caption className="sr-only">Each person (rows) in each frame (columns), with whether their eyes are open</caption>
          <thead>
            <tr>
              <th scope="col" className="sr-only">
                Person
              </th>
              {frames.map((m, i) => (
                <th
                  key={m.photo_id}
                  scope="col"
                  className={`t-data px-0.5 pb-0.5 text-center text-[0.6875rem] font-semibold ${m.photo_id === selected ? "text-[#9cc2ff]" : i === bestCol ? "text-[#f6c343]" : "text-white/80"}`}
                >
                  {names[m.photo_id]?.replace("Frame ", "F") ?? ""}
                  {i === bestCol && <span className="sr-only">, AI pick</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, r) => (
              <tr key={r}>
                <th scope="row" className="pr-1.5 text-left align-middle text-[0.75rem] font-medium whitespace-nowrap text-white/85">
                  P{r + 1}
                  <span className="sr-only">Person {r + 1}</span>
                  {row.main && (
                    <span className="ml-1 text-[#9cc2ff]" title="Main subject">
                      ★<span className="sr-only">, main subject</span>
                    </span>
                  )}
                </th>
                {row.cells.map((c, i) => {
                  const m = frames[i];
                  const on = m.photo_id === selected;
                  const best = i === bestCol;
                  const tone = c ? EYES[c.eyes] : undefined;
                  const label = `${names[m.photo_id]}, person ${r + 1}: ${c ? EYES[c.eyes].word : "not found in this frame"}`;
                  return (
                    <td key={m.photo_id} className="p-0 align-top">
                      <button
                        type="button"
                        onClick={() => onSelect(m.photo_id)}
                        aria-label={label}
                        title={label}
                        className={`relative block overflow-hidden rounded-[7px] bg-[#2a2e36] transition-[box-shadow] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white ${
                          on ? "ring-2 ring-[#6ea8ff]" : best ? "ring-2 ring-[#f6c343]" : "ring-1 ring-white/10 hover:ring-white/40"
                        }`}
                        style={{ width: cell, height: cell }}
                      >
                        {c ? (
                          <FaceCrop src={m.proxy_url} face={c.face} scale={proxySizes[m.photo_id] ? faceScale(scores[i], ...proxySizes[m.photo_id]) : 1} proxy={proxySizes[m.photo_id]} size={cell} />
                        ) : (
                          <span className="absolute inset-0 grid place-items-center text-[0.625rem] text-white/60">not in frame</span>
                        )}
                        {c?.eyes === "closed" && <span aria-hidden="true" className="pointer-events-none absolute inset-0 rounded-[7px] shadow-[inset_0_0_0_2px_#ff6b6b]" />}
                        {c && tone && (
                          <span
                            aria-hidden="true"
                            className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-0.5 bg-black/70 py-[1px] text-[0.625rem] font-semibold"
                            style={{ color: tone.color }}
                          >
                            <EyeGlyph eyes={c.eyes} className="h-2.5 w-2.5" />
                            {c.eyes === "closed" ? "Blink" : c.eyes === "partial" ? "1 eye" : c.eyes === "open" ? "Open" : "Small"}
                          </span>
                        )}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** When the AI pick has a shut eye, say why it still won. */
function pickNote(rows: FaceRow[], frames: Member[], bestCol: number, names: Record<string, string>): string {
  if (bestCol < 0) return "";
  const pick = names[frames[bestCol].photo_id];
  const shut = rows.map((r, i) => ({ i, c: r.cells[bestCol] })).filter((x) => x.c && (x.c.eyes === "closed" || x.c.eyes === "partial"));
  if (!shut.length) {
    const blinks = frames.filter((_, i) => rows.some((r) => r.cells[i]?.eyes === "closed")).map((m) => names[m.photo_id]);
    return blinks.length ? `Someone blinks on ${blinks.join(", ")}. ${pick}, the AI pick, has every readable eye open.` : "";
  }
  const who = shut.map((x) => `person ${x.i + 1}`).join(" and ");
  const mainOk = rows.some((r, i) => r.main && r.cells[bestCol]?.eyes === "open" && !shut.some((x) => x.i === i));
  const allOpen = frames.findIndex((_, i) => i !== bestCol && rows.every((r) => !r.cells[i] || r.cells[i]?.eyes === "open"));
  const why = mainOk ? "the main subject's eyes are open and it scored highest overall" : "it scored highest overall";
  const alt = allOpen >= 0 ? ` ${names[frames[allOpen].photo_id]} has every eye open but scored lower, so it is worth a look.` : " No frame has everyone's eyes open.";
  return `${pick} is the AI pick even though ${who} ${shut.length === 1 ? "has" : "have"} an eye shut: ${why}.${alt}`;
}

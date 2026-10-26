"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { faceScale, normalizeScores, plainReason, type Scores } from "@/lib/cull";
import { heatRGBA, sharpMap, type SharpMap } from "@/lib/sharpmap";
import { parseReason } from "@/lib/reasons";
import { PICK_WORD, type Pick } from "@/lib/picks";
import type { Member, Verdict } from "@/lib/types";
import { SignedImg } from "../Frame";
import { VerdictGlyph } from "../VerdictMark";
import { EyeGlyph } from "./FaceStrip";

/** Verdict colours that pass AA on a near-black surface. */
export const DARK_VERDICT: Record<Verdict, string> = { keep: "#4cc38a", review: "#f0a43a", reject: "#ff8080" };

export interface ZoomState {
  /** 1 = fit. */
  z: number;
  /** Zoom centre, 0..1 of the image. Shared by every pane, so compare views pan together. */
  u: number;
  v: number;
}
export const FIT: ZoomState = { z: 1, u: 0.5, v: 0.5 };
export const ZOOM_STEPS = [1, 2, 3, 4];

/** 1, 2 or 4 frames side by side with synced zoom/pan, an optional 100% loupe and the AI overlay. */
export function Viewer({
  panes,
  focusId,
  onFocus,
  zoom,
  onZoom,
  overlay,
  loupe,
  names,
  sizes,
  onSize,
  picks,
  stamp,
  dark = true,
  maxH,
}: {
  panes: Member[];
  focusId: string;
  onFocus: (id: string) => void;
  zoom: ZoomState;
  onZoom: (z: ZoomState) => void;
  overlay: boolean;
  loupe: boolean;
  names: Record<string, string>;
  sizes: Record<string, [number, number]>;
  onSize: (id: string, w: number, h: number) => void;
  picks?: Record<string, Pick>;
  /** Last pick made, to animate its stamp on the pane: { id, pick, n } (n changes per press). */
  stamp?: { id: string; pick: Pick; n: number };
  dark?: boolean;
  /** Height cap for the single-pane inline view (CSS length). */
  maxH?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [W, H] = useBox(box);
  // Two panes go side by side or stacked, whichever shows each frame larger in this box.
  const gap = panes.length > 1 ? 8 : 0;
  const ar = 3 / 2;
  const fit = (c: number, r: number) => Math.min((W - gap * (c - 1)) / c, ((H - gap * (r - 1)) / r - 26) * ar);
  const [cols, rows] = panes.length >= 3 ? [2, 2] : panes.length === 2 ? (fit(2, 1) >= fit(1, 2) ? [2, 1] : [1, 2]) : [1, 1];
  const cellW = (W - gap * (cols - 1)) / cols;
  const cellH = (H - gap * (rows - 1)) / rows;
  return (
    <div
      ref={box}
      className="grid h-full w-full"
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0,1fr))`, gridTemplateRows: `repeat(${rows}, minmax(0,1fr))`, gap, maxHeight: maxH }}
    >
      {panes.map((m) => (
        <Pane
          key={m.photo_id}
          m={m}
          on={panes.length > 1 && m.photo_id === focusId}
          multi={panes.length > 1}
          w={cellW}
          h={cellH}
          onFocus={() => onFocus(m.photo_id)}
          zoom={zoom}
          onZoom={onZoom}
          overlay={overlay}
          loupe={loupe}
          name={names[m.photo_id] ?? "Frame"}
          size={sizes[m.photo_id]}
          onSize={(w, h) => onSize(m.photo_id, w, h)}
          pick={picks?.[m.photo_id]}
          stamp={stamp && stamp.id === m.photo_id ? stamp : undefined}
          dark={dark}
        />
      ))}
    </div>
  );
}

function Pane({
  m,
  on,
  multi,
  w,
  h,
  onFocus,
  zoom,
  onZoom,
  overlay,
  loupe,
  name,
  size,
  onSize,
  pick,
  stamp,
  dark,
}: {
  m: Member;
  on: boolean;
  multi: boolean;
  w: number;
  h: number;
  onFocus: () => void;
  zoom: ZoomState;
  onZoom: (z: ZoomState) => void;
  overlay: boolean;
  loupe: boolean;
  name: string;
  size?: [number, number];
  onSize: (w: number, h: number) => void;
  pick?: Pick;
  stamp?: { pick: Pick; n: number };
  dark: boolean;
}) {
  const ar = size ? size[0] / size[1] : 3 / 2;
  const pad = multi ? 26 : 0;
  const bw = Math.max(0, Math.min(w, (h - pad) * ar));
  const bh = bw / ar;
  const scores = normalizeScores(m.scores as Record<string, unknown> | undefined, m.breakdown);
  const [ptr, setPtr] = useState<{ u: number; v: number; x: number; y: number } | null>(null);
  const zoomed = zoom.z > 1;

  const at = (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return { u: clamp01((e.clientX - r.left) / r.width), v: clamp01((e.clientY - r.top) / r.height), x: e.clientX - r.left, y: e.clientY - r.top };
  };
  return (
    <div className={`relative flex min-h-0 min-w-0 flex-col items-center justify-center ${multi ? "rounded-[8px] p-0" : ""}`}>
      <div
        className={`relative overflow-hidden ${multi ? "rounded-[6px]" : "rounded-[4px]"} ${zoomed ? "cursor-zoom-out" : loupe ? "cursor-none" : "cursor-zoom-in"} ${
          on ? "ring-2 ring-[#6ea8ff] ring-offset-2 ring-offset-black" : ""
        }`}
        style={{ width: bw, height: bh }}
        onMouseMove={(e) => {
          const p = at(e);
          setPtr(p);
          if (zoomed) onZoom({ ...zoom, u: p.u, v: p.v });
        }}
        onMouseLeave={() => setPtr(null)}
        onClick={(e) => {
          if (multi && !on) {
            onFocus();
            return;
          }
          const p = at(e);
          onZoom(zoomed ? { ...zoom, z: 1 } : { z: 2.5, u: p.u, v: p.v });
        }}
        onWheel={(e) => {
          if (!e.ctrlKey && !e.metaKey && !zoomed) return;
          const p = at(e as unknown as React.MouseEvent<HTMLDivElement>);
          const z = Math.max(1, Math.min(6, zoom.z * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
          onZoom({ z: z < 1.05 ? 1 : z, u: p.u, v: p.v });
        }}
      >
        <div
          className="absolute inset-0 transition-transform duration-150 ease-out motion-reduce:transition-none"
          style={{ transform: zoomed ? `scale(${zoom.z})` : "none", transformOrigin: `${zoom.u * 100}% ${zoom.v * 100}%` }}
        >
          <SignedImg src={m.proxy_url} alt={`${name}${multi ? "" : ", large preview"}`} fit="contain" eager onSize={onSize} onDark compact={multi} />
          {overlay && size && <HeatLayer id={m.photo_id} src={m.proxy_url} />}
          {overlay && size && <FaceBoxes scores={scores} size={size} />}
        </div>
        {loupe && !zoomed && ptr && size && <Loupe src={m.proxy_url} size={size} box={[bw, bh]} ptr={ptr} />}
        {overlay && <ReasonTag m={m} scores={scores} compact={multi} />}
        {pick && multi && <PickBadge pick={pick} />}
        {stamp && <Stamp key={stamp.n} pick={stamp.pick} />}
      </div>
      {multi && (
        <p className={`t-data mt-1.5 flex h-5 items-center gap-2 text-[0.75rem] ${on ? "font-semibold text-white" : dark ? "text-white/80" : ""}`}>
          {name}
          {m.is_best && <span className="font-semibold text-[#f6c343]">AI pick</span>}
          {m.verdict && (
            <span className="inline-flex items-center gap-1" style={{ color: DARK_VERDICT[m.verdict] }}>
              <VerdictGlyph verdict={m.verdict} className="h-3 w-3" />
              {m.verdict === "review" ? "Review" : m.verdict === "keep" ? "Keep" : "Reject"}
            </span>
          )}
        </p>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ loupe

/** A circle following the cursor that shows the proxy at 100% (2× when the frame is already shown at full size). */
function Loupe({ src, size, box, ptr }: { src: string; size: [number, number]; box: [number, number]; ptr: { u: number; v: number; x: number; y: number } }) {
  const R = 95;
  // 100% of the proxy's pixels; when the frame is already on screen at (near) full size, magnify 2×.
  const lw = box[0] >= size[0] * 0.8 ? box[0] * 2 : size[0];
  const lh = lw * (size[1] / size[0]);
  const pct = Math.round((lw / box[0]) * 100);
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute z-10 overflow-hidden rounded-full bg-black shadow-[0_0_0_2px_rgba(255,255,255,0.9),0_8px_28px_rgba(0,0,0,0.6)]"
      style={{ width: R * 2, height: R * 2, left: ptr.x - R, top: ptr.y - R }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt="" referrerPolicy="no-referrer" draggable={false} className="absolute max-w-none" style={{ width: lw, height: lh, left: R - ptr.u * lw, top: R - ptr.v * lh }} />
      <span className="t-data absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-black/70 px-1.5 text-[0.625rem] font-semibold text-white">{pct}%</span>
    </div>
  );
}

// ------------------------------------------------------------------ AI overlay

const mapCache = new Map<string, SharpMap | "failed">();

/** Focus-peaking heat map: where the frame has fine detail (Laplacian variance per block), computed here from the
 *  proxy pixels. Falls back to nothing when the image can't be read (no CORS), and the face boxes still show. */
function HeatLayer({ id, src }: { id: string; src: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [map, setMap] = useState<SharpMap | "failed" | undefined>(() => mapCache.get(id));
  useEffect(() => {
    if (mapCache.has(id)) {
      setMap(mapCache.get(id));
      return;
    }
    let live = true;
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.referrerPolicy = "no-referrer";
    img.decoding = "async";
    img.onload = () => {
      try {
        const c = document.createElement("canvas");
        c.width = img.naturalWidth;
        c.height = img.naturalHeight;
        const ctx = c.getContext("2d", { willReadFrequently: true });
        if (!ctx) throw new Error("no 2d");
        ctx.drawImage(img, 0, 0);
        const d = ctx.getImageData(0, 0, c.width, c.height);
        const m = sharpMap(d.data, c.width, c.height, Math.max(16, Math.round(c.width / 40)));
        mapCache.set(id, m);
        if (live) setMap(m);
      } catch {
        mapCache.set(id, "failed");
        if (live) setMap("failed");
      }
    };
    img.onerror = () => {
      if (live) setMap("failed");
    };
    img.src = src;
    return () => {
      live = false;
    };
  }, [id, src]);
  useEffect(() => {
    const c = ref.current;
    if (!c || !map || map === "failed") return;
    c.width = map.cols;
    c.height = map.rows;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const im = ctx.createImageData(map.cols, map.rows);
    map.cells.forEach((t, i) => im.data.set(heatRGBA(t), i * 4));
    ctx.putImageData(im, 0, 0);
  }, [map]);
  if (!map || map === "failed") return null;
  return <canvas ref={ref} aria-hidden="true" className="kf-fade pointer-events-none absolute inset-0 h-full w-full mix-blend-screen" style={{ imageRendering: "auto", filter: "blur(6px)", opacity: 0.4 }} />;
}

const EYE_COLOR = { open: "#4cc38a", partial: "#f0a43a", closed: "#ff6b6b", unknown: "#ffffff" } as const;
const EYE_WORD = { open: "Eyes open", partial: "One eye shut", closed: "Blink", unknown: "Eyes unreadable" } as const;

function FaceBoxes({ scores, size }: { scores: Scores; size: [number, number] }) {
  const k = faceScale(scores, size[0], size[1]);
  const faces = scores.faces.filter((f) => f.bbox_px);
  return (
    <>
      <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox={`0 0 ${size[0]} ${size[1]}`} aria-hidden="true">
        {faces.map((f) => {
          const st = eyeState(f.state);
          const [x, y, w, h] = (f.bbox_px as number[]).map((v) => v * k);
          return <rect key={f.face_id} x={x} y={y} width={w} height={h} rx={4} fill="none" stroke={EYE_COLOR[st]} strokeWidth={2.5} strokeDasharray={st === "closed" ? "6 4" : undefined} vectorEffect="non-scaling-stroke" style={{ filter: "drop-shadow(0 0 1.5px rgba(0,0,0,0.9))" }} />;
        })}
      </svg>
      {/* Labels in CSS px (readable at any zoom-to-fit size), pinned under each box. */}
      {faces.map((f, i) => {
        const st = eyeState(f.state);
        const [x, y, , h] = (f.bbox_px as number[]).map((v) => v * k);
        // Faces close together: drop every other label a row lower so they don't collide.
        const near = faces.slice(0, i).some((o) => Math.abs((o.bbox_px as number[])[0] * k - x) < size[0] * 0.2 && Math.abs((o.bbox_px as number[])[1] * k - y) < size[1] * 0.15);
        return (
          <span
            key={f.face_id}
            aria-hidden="true"
            className="pointer-events-none absolute inline-flex items-center gap-1 rounded-[4px] bg-black/80 px-1.5 py-[1px] text-[11px] leading-4 font-semibold whitespace-nowrap shadow"
            style={{ left: `${(x / size[0]) * 100}%`, top: `calc(${((y + h) / size[1]) * 100}% + ${near ? 23 : 3}px)`, color: EYE_COLOR[st] }}
          >
            <EyeGlyph eyes={st} className="h-3 w-3" />
            {f.main_subject && <span className="text-white">Main ·</span>}
            {EYE_WORD[st]}
          </span>
        );
      })}
    </>
  );
}

function eyeState(s?: string): keyof typeof EYE_COLOR {
  return s === "open" || s === "partial" || s === "closed" ? s : "unknown";
}

/** Why the agent flagged this frame, drawn on the photo: verdict, reason, and the sharpness read. */
function ReasonTag({ m, scores, compact }: { m: Member; scores: Scores; compact: boolean }) {
  const reason = plainReason(m.reason_text) || (m.reason_codes ?? []).map((c) => parseReason(c).def.label).filter(Boolean).join(" · ");
  const closed = scores.faces.filter((f) => f.state === "closed").length;
  const sharp = scores.sharp_label ? `${scores.sharp_label}${scores.blur_kind ? `, ${scores.blur_kind} blur` : ""}` : "";
  return (
    <div className="kf-fade pointer-events-none absolute top-2 left-2 flex max-w-[calc(100%-1rem)] flex-col items-start gap-1">
      <p
        className="max-w-full truncate rounded-full bg-black/80 py-1 pr-2.5 pl-2 text-[0.75rem] font-semibold text-white shadow-lg ring-1 ring-white/10 backdrop-blur"
        title={reason}
      >
        <span className="inline-flex items-center gap-1" style={{ color: m.verdict ? DARK_VERDICT[m.verdict] : "#fff" }}>
          {m.verdict && <VerdictGlyph verdict={m.verdict} className="h-3 w-3" />}
          {m.is_best ? "AI pick" : m.verdict === "keep" ? "Keep" : m.verdict === "reject" ? "Rejected" : m.verdict === "review" ? "For review" : "No verdict"}
        </span>
        {!compact && reason && <span className="font-normal"> · {reason.replace(/\.$/, "")}</span>}
      </p>
      {(sharp || closed > 0) && (
        <p className="inline-flex items-center gap-2 rounded-full bg-black/75 px-2 py-0.5 text-[0.6875rem] text-white/90 ring-1 ring-white/10">
          {sharp && (
            <span className="inline-flex items-center gap-1">
              <span aria-hidden="true" className="h-1.5 w-6 rounded-full" style={{ background: "linear-gradient(90deg, rgb(40 90 255), rgb(0 220 230), rgb(255 236 60))" }} />
              Sharpness: {sharp}
            </span>
          )}
          {closed > 0 && (
            <span className="inline-flex items-center gap-1" style={{ color: EYE_COLOR.closed }}>
              <EyeGlyph eyes="closed" />
              {closed} blinking
            </span>
          )}
        </p>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ picks

const PICK_COLOR: Record<Pick, string> = { keep: "#2fa36b", review: "#d98a1c", reject: "#e5484d" };

function PickBadge({ pick }: { pick: Pick }) {
  return (
    <span
      className="kf-pop pointer-events-none absolute top-2 right-2 inline-flex h-6 items-center gap-1 rounded-full pr-2 pl-1.5 text-[0.6875rem] font-semibold text-white shadow-[0_0_0_1.5px_rgba(255,255,255,0.9)]"
      style={{ background: PICK_COLOR[pick] }}
    >
      <VerdictGlyph verdict={pick} className="h-3 w-3" />
      You: {PICK_WORD[pick]}
    </span>
  );
}

/** Big verdict stamp that pops and fades when you press K / X / U. */
function Stamp({ pick }: { pick: Pick }) {
  return (
    <span aria-hidden="true" className="kf-stamp pointer-events-none absolute inset-0 grid place-items-center">
      <span className="inline-flex items-center gap-2 rounded-full px-5 py-2.5 text-[1.25rem] font-semibold text-white shadow-2xl" style={{ background: PICK_COLOR[pick] }}>
        <VerdictGlyph verdict={pick} className="h-6 w-6" />
        {PICK_WORD[pick]}
      </span>
    </span>
  );
}

export const VIEWER_CSS = `@keyframes kf-stamp{0%{opacity:0;transform:scale(.7)}18%{opacity:1;transform:scale(1.04)}30%{transform:scale(1)}75%{opacity:1}100%{opacity:0;transform:scale(1)}}.kf-stamp>span{animation:kf-stamp .7s cubic-bezier(.2,.8,.2,1) both}@media (prefers-reduced-motion:reduce){.kf-stamp>span{animation:kf-stamp-r .7s linear both}@keyframes kf-stamp-r{0%,80%{opacity:1}100%{opacity:0}}}`;

// ------------------------------------------------------------------ utils

function clamp01(v: number) {
  return Math.max(0, Math.min(1, v));
}

function useBox(ref: React.RefObject<HTMLElement | null>): [number, number] {
  const [s, setS] = useState<[number, number]>([0, 0]);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setS([el.clientWidth, el.clientHeight]));
    ro.observe(el);
    setS([el.clientWidth, el.clientHeight]);
    return () => ro.disconnect();
  }, [ref]);
  return s;
}

"use client";

import { useState } from "react";
import { PhotoUnavailable, useImageFailure } from "@/components/States";
import type { Verdict } from "@/lib/types";
import s from "./approvals.module.css";
import { METRICS, noFaces, type FrameVM } from "./logic";

export type Zoom = { x: number; y: number } | null;

// Light text on a tinted chip: ≥ 7:1 on the near-black stage.
const DARK_CHIP: Record<Verdict, string> = {
  keep: "bg-[#173d2a] text-[#a7e3bf]",
  review: "bg-[#3d2c12] text-[#f5cf8e]",
  reject: "bg-[#3f1c1c] text-[#f3b0aa]",
};

const VERDICT_STYLE: Record<Verdict, { label: string; dot: string; text: string }> = {
  keep: { label: "Keep", dot: "bg-[var(--ap-keep)]", text: "text-[var(--ap-keep)]" },
  review: { label: "Review", dot: "bg-[var(--ap-review)]", text: "text-[var(--ap-review)]" },
  reject: { label: "Reject", dot: "bg-[var(--ap-reject)]", text: "text-[var(--ap-reject)]" },
};

export function VerdictPill({ v, muted = false, onDark = false }: { v: Verdict; muted?: boolean; onDark?: boolean }) {
  const st = VERDICT_STYLE[v];
  if (onDark) {
    return (
      <span className={`inline-flex items-center gap-1.5 rounded-[5px] px-1.5 py-0.5 text-[0.75rem] font-semibold ${DARK_CHIP[v]}`}>
        <span aria-hidden="true" className={`inline-block h-1.5 w-1.5 rounded-full ${st.dot}`} />
        {st.label}
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center gap-1.5 text-[0.78rem] font-semibold ${muted ? "text-[var(--ap-muted)]" : st.text}`}>
      <span aria-hidden="true" className={`inline-block h-2 w-2 rounded-full ${st.dot}`} />
      {st.label}
    </span>
  );
}

/** One large candidate on the comparison stage. Clicking the photo opens it at full stage size; hovering any
 *  candidate zooms every candidate to the same spot (synced loupe), so eyes and focus can be compared at the same
 *  place in each frame. "Keep" (or its digit) makes the frame the keeper. */
export function Candidate({
  frame,
  hotkey,
  verdict,
  agentPick,
  yourPick,
  isKeeper,
  pickLabel,
  zoom,
  zoomOn,
  onZoom,
  onPick,
  onOpen,
  onAspect,
  aspect,
  readOnly,
  leaders,
  width,
  showMetrics = true,
  name,
  scoreLabel = "Score",
  fineScores = false,
  enlarged = false,
}: {
  /** "Agent rank score" or "Worker score": which number this is. */
  scoreLabel?: string;
  /** Two decimals instead of one (top two under a point apart). */
  fineScores?: boolean;
  frame: FrameVM;
  hotkey?: number;
  verdict: Verdict;
  agentPick: boolean;
  yourPick: boolean;
  /** This frame is the keeper the answer would save (no "Keep" button on it). */
  isKeeper: boolean;
  pickLabel: string;
  zoom: Zoom;
  zoomOn: boolean;
  onZoom: (z: Zoom) => void;
  onPick?: () => void;
  /** Click on the photo: open it at full stage size (or, when already enlarged, nothing). */
  onOpen?: () => void;
  /** Reports the photo's true width / height once it loads. */
  onAspect?: (a: number) => void;
  /** Width / height of the photo box; the photo is contained, never cropped. */
  aspect: number;
  readOnly: boolean;
  leaders: Record<string, string | undefined>;
  width?: number;
  showMetrics?: boolean;
  name: string;
  enlarged?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const why = useImageFailure(frame.proxy_url, failed);
  const z = zoomOn && zoom;
  const ring = yourPick
    ? "ring-[3px] ring-[var(--ap-keep)]"
    : agentPick
      ? "ring-[3px] ring-[var(--ap-accent)]"
      : "ring-1 ring-white/10 hover:ring-white/40";
  const metrics = showMetrics && frame.breakdown ? frame.breakdown : undefined;

  return (
    <figure className="flex min-w-0 flex-col" style={width ? { width } : undefined}>
      {/* One row above the photo: name, pick badge, verdict, score, Keep. */}
      <div className="mb-1.5 flex h-6 min-w-0 flex-nowrap items-center gap-x-2 px-0.5 text-[0.8rem]">
        <span className="truncate font-semibold whitespace-nowrap text-white">{name}</span>
        {(agentPick || yourPick) && (
          <span
            className={`inline-flex shrink-0 items-center gap-1 rounded-[5px] px-1.5 py-px text-[0.72rem] font-semibold whitespace-nowrap text-white ${
              yourPick ? "bg-[var(--ap-keep)]" : "bg-[var(--ap-accent)]"
            }`}
          >
            <svg aria-hidden="true" viewBox="0 0 12 12" className="h-3 w-3">
              <path d="M2.5 6.2 5 8.6l4.6-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {pickLabel}
          </span>
        )}
        <span className="shrink-0">
          <VerdictPill v={verdict} onDark />
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-2">
          {typeof frame.score === "number" && (
            <span className="text-[0.78rem] whitespace-nowrap text-white/75 tabular-nums" title={scoreLabel === "Agent rank score" ? "The score the agent ranked this burst by, out of 100" : "The worker's stored quality score for this frame, out of 100"}>
              <span className={width && width < 420 ? "sr-only" : ""}>{scoreLabel} </span>
              <span className={`font-semibold ${leaders.score === frame.photo_id ? "text-white" : "text-white/85"}`}>{(frame.score * 100).toFixed(fineScores ? 2 : 1)}</span>
            </span>
          )}
          {!readOnly && !isKeeper && onPick && (
            <button
              type="button"
              onClick={onPick}
              aria-label={`Keep ${name} instead`}
              title={hotkey !== undefined ? `Keep this frame (${hotkey})` : "Keep this frame"}
              className="inline-flex h-6 items-center gap-1.5 rounded-[5px] bg-white/10 px-2 text-[0.74rem] font-semibold text-white ring-1 ring-white/25 ring-inset hover:bg-white/20 focus-visible:outline-2 focus-visible:outline-white"
            >
              Keep
              {hotkey !== undefined && (
                <span aria-hidden="true" className="text-white/70 tabular-nums">
                  {hotkey}
                </span>
              )}
            </button>
          )}
        </span>
      </div>
      <button
        type="button"
        onClick={enlarged ? undefined : onOpen}
        aria-label={`${enlarged ? "" : "Enlarge "}${name}${agentPick ? ", the agent's pick" : ""}${yourPick && !agentPick ? ", your pick" : ""}`}
        onMouseMove={(e) => {
          if (!zoomOn) return;
          const r = e.currentTarget.getBoundingClientRect();
          onZoom({ x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height });
        }}
        onMouseLeave={() => onZoom(null)}
        style={{ aspectRatio: String(aspect) }}
        className={`group relative block w-full overflow-hidden rounded-[8px] bg-black transition-shadow focus-visible:outline-2 focus-visible:outline-offset-3 focus-visible:outline-white ${ring} ${
          zoomOn ? "cursor-crosshair" : enlarged ? "cursor-default" : "cursor-zoom-in"
        }`}
      >
        {frame.proxy_url && !failed ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={frame.proxy_url}
            alt={`${name} of the burst`}
            referrerPolicy="no-referrer"
            decoding="async"
            draggable={false}
            onError={() => setFailed(true)}
            onLoad={(e) => {
              const im = e.currentTarget;
              if (im.naturalWidth && im.naturalHeight) onAspect?.(im.naturalWidth / im.naturalHeight);
            }}
            className={`${s.zoomImg} block h-full w-full object-contain`}
            style={z ? { transform: "scale(2.6)", transformOrigin: `${z.x * 100}% ${z.y * 100}%` } : undefined}
          />
        ) : failed ? (
          <PhotoUnavailable why={why} onDark compact />
        ) : (
          <span className="flex h-full items-center justify-center p-4 text-center text-sm text-white/70">No image</span>
        )}
        {!enlarged && (
          <span className="pointer-events-none absolute right-2 bottom-2 rounded-[5px] bg-black/70 px-1.5 py-0.5 text-[0.72rem] font-semibold text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 motion-reduce:transition-none">
            Click to enlarge
          </span>
        )}
      </button>
      {/* Score bars: one compact row, so they never steal the photo's height. */}
      {metrics && (
        <figcaption className="mt-1.5 h-[18px] px-0.5">
          {noFaces(metrics) ? (
            <p className="text-[0.74rem] text-white/75">Faces: n/a, no faces</p>
          ) : (
            // Label, value, then the bar; the bars drop out when the frame is too narrow for them to read.
            <dl className="grid grid-cols-3 gap-x-5">
              {METRICS.map((m) => {
                const v = metrics[m.key];
                if (typeof v !== "number") return <div key={m.key} />;
                const lead = leaders[m.key] === frame.photo_id;
                const bars = !width || width >= 520;
                return (
                  <div key={m.key} className={`grid min-w-0 items-center gap-1.5 text-[0.74rem] ${bars ? "grid-cols-[auto_2rem_minmax(1rem,1fr)]" : "grid-cols-[auto_2rem]"}`}>
                    <dt className={`truncate whitespace-nowrap ${lead ? "font-semibold text-white" : "text-white/80"}`}>{m.label}</dt>
                    <dd className={`text-right tabular-nums ${lead ? "font-semibold text-white" : "text-white/70"}`}>
                      {lead && <span aria-hidden="true" className="mr-0.5 text-[0.6rem]">▲</span>}
                      {Math.round(v * 100)}
                      {lead && <span className="sr-only"> (highest)</span>}
                    </dd>
                    {bars && (
                    <dd className="h-1.5 w-full overflow-hidden rounded-full bg-white/15" aria-hidden="true">
                      <span className={`block h-full rounded-full ${lead ? "bg-white" : "bg-white/50"}`} style={{ width: `${Math.round(Math.max(0, Math.min(1, v)) * 100)}%` }} />
                    </dd>
                    )}
                  </div>
                );
              })}
            </dl>
          )}
        </figcaption>
      )}
    </figure>
  );
}

/** Height a candidate needs besides its photo: the row above, plus the score row below when shown. */
export const CAPTION_H = { bare: 30, metrics: 54 };

/** Every frame of the burst, small, with the verdict each will get. Frames on the stage are ringed; click any
 *  frame to keep it instead (it joins the stage). */
export function Filmstrip({
  frames,
  verdictOf,
  onStage,
  onPick,
  readOnly,
  lockedReason = "Read-only judge view",
  nameOf,
}: {
  lockedReason?: string;
  frames: FrameVM[];
  verdictOf: (id: string) => Verdict;
  onStage: Set<string>;
  onPick: (id: string) => void;
  readOnly: boolean;
  nameOf: (n: number) => string;
}) {
  // A thin strip (≤ 64px): number and verdict dot sit on the thumbnail, not under it.
  return (
    <div className="min-w-0 overflow-x-auto">
      <ul className="mx-auto flex w-max gap-1.5 p-1" aria-label="Whole burst">
        {frames.map((f) => {
          const v = verdictOf(f.photo_id);
          const staged = onStage.has(f.photo_id);
          return (
            <li key={f.photo_id} className="shrink-0">
              <button
                type="button"
                onClick={() => onPick(f.photo_id)}
                // Read-only (judge, or an answered request): a click shows the frame large instead of picking it.
                title={readOnly ? `Show ${nameOf(f.n)} (${lockedReason.toLowerCase()})` : `Keep ${nameOf(f.n)} instead`}
                aria-label={`${nameOf(f.n)}, ${v}${staged ? ", compared above" : ""}`}
                className={`relative block h-12 w-[72px] overflow-hidden rounded-[6px] bg-black transition-opacity focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white ${
                  staged ? "ring-2 ring-white/80" : "opacity-60 hover:opacity-100"
                }`}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={f.proxy_url} alt="" referrerPolicy="no-referrer" loading="lazy" className="h-full w-full object-cover" />
                <span className="absolute bottom-0.5 left-0.5 flex items-center gap-1 rounded-[4px] bg-black/75 px-1 text-[0.66rem] leading-4 font-semibold text-white tabular-nums">
                  <span aria-hidden="true" className={`inline-block h-1.5 w-1.5 rounded-full ${DOT[v]}`} />
                  {f.n}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const DOT: Record<Verdict, string> = { keep: "bg-[var(--ap-keep)]", review: "bg-[var(--ap-review)]", reject: "bg-[var(--ap-reject)]" };

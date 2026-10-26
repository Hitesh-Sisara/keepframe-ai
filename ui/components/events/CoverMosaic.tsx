"use client";

import { useState } from "react";
import { PhotoUnavailable, useImageFailure } from "@/components/States";
import { useApi } from "@/lib/api";
import type { GroupsPage, Member } from "@/lib/types";

/** Up to four cover frames for an event: the best frame of each of its first groups (real thumbnails from
 *  GET /events/{id}/groups). Falls back to other members when an event has fewer groups. */
export function useCoverFrames(eventId: string, refreshKey: string): { frames: Member[]; loading: boolean; failed: boolean } {
  // refreshKey changes when a run on this event changes, so the cover (and the URL expiry) follows the run.
  const page = useApi<GroupsPage>(`/events/${encodeURIComponent(eventId)}/groups`, { limit: 12, _r: refreshKey || undefined });
  // Spread the picks across the event so neighbouring bursts of one moment don't fill the whole mosaic.
  const all = page.data?.items ?? [];
  const step = Math.max(1, Math.floor(all.length / 4));
  const groups = all.filter((_, i) => i % step === 0);
  const best = groups.map((g) => g.members.find((m) => m.is_best) ?? g.members[0]).filter(Boolean) as Member[];
  const extra = groups.flatMap((g) => g.members).filter((m) => !best.includes(m));
  return { frames: [...best, ...extra].slice(0, 4), loading: !page.data && !page.error, failed: Boolean(page.error) };
}

export function CoverMosaic({ frames, loading, title, fill = false }: { frames: Member[]; loading: boolean; title: string; fill?: boolean }) {
  // fill: the card's text column sets the height and the mosaic covers it, so tall thumbnails never stretch the
  // card and leave dead space beside the stats.
  const box = fill ? "aspect-[16/9] md:absolute md:inset-0 md:aspect-auto" : "aspect-[16/9]";
  if (loading) {
    return (
      <div aria-hidden="true" className={`grid ${box} grid-cols-[2fr_1fr] grid-rows-3 gap-0.5 overflow-hidden`}>
        <div className="row-span-3 animate-pulse bg-[var(--kf-surface-2,#eef1f4)] motion-reduce:animate-none" />
        {[0, 1, 2].map((i) => (
          <div key={i} className="animate-pulse bg-[var(--kf-surface-2,#eef1f4)] motion-reduce:animate-none" />
        ))}
      </div>
    );
  }
  if (!frames.length) {
    return (
      <div className={`grid ${box} place-items-center bg-[var(--kf-surface-2,#f5f7f9)] text-[0.8rem] text-[var(--kf-muted,#5a646d)]`}>
        <span className="flex flex-col items-center gap-2">
          <svg viewBox="0 0 24 24" className="h-6 w-6 opacity-60" aria-hidden="true">
            <rect x="3" y="5" width="18" height="14" rx="2" fill="none" stroke="currentColor" strokeWidth="1.5" />
            <circle cx="9" cy="10" r="1.6" fill="currentColor" />
            <path d="M4 17l5-4 3 2.5 4-3.5 4 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
          </svg>
          Covers appear after the first run
        </span>
      </div>
    );
  }
  const [hero, ...rest] = frames;
  return (
    <div
      role="img"
      aria-label={`Cover photos from ${title}`}
      className={`grid ${box} min-h-0 gap-0.5 overflow-hidden bg-[var(--kf-border,#dfe4e8)] ${rest.length ? "grid-cols-[2fr_1fr]" : "grid-cols-1"}`}
      style={{ gridTemplateRows: rest.length ? `repeat(${rest.length}, minmax(0, 1fr))` : undefined }}
    >
      <Thumb src={hero.proxy_url} className={rest.length ? "" : ""} style={{ gridRow: rest.length ? `span ${rest.length}` : undefined }} />
      {rest.map((m) => (
        <Thumb key={m.photo_id} src={m.proxy_url} />
      ))}
    </div>
  );
}

function Thumb({ src, className = "", style }: { src: string; className?: string; style?: React.CSSProperties }) {
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const why = useImageFailure(src, state === "error");
  return (
    <div className={`relative min-h-0 overflow-hidden bg-[var(--kf-surface-2,#eef1f4)] ${className}`} style={style}>
      {state !== "error" && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onLoad={() => setState("ok")}
          onError={() => setState("error")}
          className={`h-full w-full object-cover object-[50%_30%] transition-[opacity,transform] duration-500 group-hover:scale-[1.03] motion-reduce:transition-none motion-reduce:group-hover:scale-100 ${state === "ok" ? "opacity-100" : "opacity-0"}`}
        />
      )}
      {state === "error" && why === "expired" && <PhotoUnavailable why={why} />}
    </div>
  );
}

"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { Verdict } from "@/lib/types";
import { VerdictMark } from "./VerdictMark";
import { EXPIRED_TEXT, PhotoUnavailable, useImageFailure } from "./States";

/** Pages that can re-issue signed image links provide this; a frame whose link fails asks for fresh links
 *  once (all frames share the one request) instead of showing an error. */
export const ImageRefreshContext = createContext<(() => Promise<void>) | null>(null);

type State = "loading" | "ok" | "refreshing" | "failed";

/** A proxy image with the verdict marked over it. Images come from short-lived signed URLs; `referrerPolicy`
 *  keeps the signed URL out of third-party referrers. */
export function Frame({
  src,
  alt,
  verdict,
  aspect = "3 / 2",
  markSize,
  fit = "cover",
  className = "",
  imgClassName = "",
  ring = true,
  dimReject = false,
  onSize,
  style,
  best,
  priority,
  compact = true,
  onDark,
  children,
}: {
  onDark?: boolean;
  /** Small tile or inside a link/button: no "See the demo" link on an expired photo. */
  compact?: boolean;
  src?: string;
  /** Alt convention (lib/a11y frameAlt): "Frame 3 of 6, keep, best pick" for the frame a card is about; ""
   *  for thumbnails whose link or button already names the frame (no double reading). */
  alt: string;
  /** The page's main image (its LCP): load eagerly with high fetch priority. */
  priority?: boolean;
  verdict?: Verdict;
  /** The AI's pick: one "AI pick" marker instead of a check plus a crown. */
  best?: boolean;
  aspect?: string;
  markSize?: "xs" | "sm" | "md" | "lg";
  fit?: "cover" | "contain";
  className?: string;
  imgClassName?: string;
  ring?: boolean;
  /** Mute rejected frames (cull grids), the way Lightroom dims rejects. */
  dimReject?: boolean;
  onSize?: (w: number, h: number) => void;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}) {
  const dim = dimReject && verdict === "reject";
  return (
    <div className={`relative overflow-hidden rounded-[8px] bg-[var(--kf-surface-2)] ${className}`} style={{ ...(aspect ? { aspectRatio: aspect } : {}), ...style }}>
      <SignedImg src={src} alt={alt} fit={fit} className={imgClassName} dim={dim} onSize={onSize} eager={priority} priority={priority} compact={compact} onDark={onDark} />
      <VerdictMark verdict={verdict} size={markSize} ring={ring} best={best} />
      {children}
    </div>
  );
}

/** <img> that survives signed-link expiry: on error it asks the page for fresh links, then retries. */
export function SignedImg({
  src,
  alt,
  fit = "cover",
  className = "",
  eager,
  priority,
  dim,
  onSize,
  compact = true,
  onDark = false,
}: {
  compact?: boolean;
  onDark?: boolean;
  src?: string;
  alt: string;
  fit?: "cover" | "contain";
  className?: string;
  eager?: boolean;
  /** fetchpriority="high" for the LCP image. */
  priority?: boolean;
  dim?: boolean;
  onSize?: (w: number, h: number) => void;
}) {
  const refresh = useContext(ImageRefreshContext);
  const [state, setState] = useState<State>("loading");
  const [nonce, setNonce] = useState(0);
  const triedFor = useRef<string | undefined>(undefined);
  const imgRef = useRef<HTMLImageElement>(null);

  // A fresh signature for an image already on screen swaps in place (the browser keeps the old pixels until the
  // new ones decode), so the periodic link refresh never flashes. Only a frame that was not showing re-skeletons.
  useEffect(() => {
    setState((s) => (s === "ok" ? "ok" : "loading"));
  }, [src]);

  // Cached images can finish before React attaches onLoad.
  useEffect(() => {
    const el = imgRef.current;
    if (el?.complete && el.naturalWidth > 0) {
      setState("ok");
      onSize?.(el.naturalWidth, el.naturalHeight);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, nonce]);

  // On error: one URL check (States.useImageFailure). 404 = the photo is gone (7-day retention): final at once.
  // Anything else gets one retry (fresh links first when the page can issue them), then the placeholder.
  const [errored, setErrored] = useState(false);
  const why = useImageFailure(src, errored);
  useEffect(() => setErrored(false), [src]);
  useEffect(() => {
    if (!why) return;
    if (why === "expired" || triedFor.current === src) return setState("failed");
    triedFor.current = src;
    setErrored(false);
    if (refresh) {
      setState("refreshing");
      void refresh().then(() => setNonce((n) => n + 1));
    } else setNonce((n) => n + 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [why]);
  function onError() {
    setState((s) => (s === "ok" ? "loading" : s));
    setErrored(true);
  }

  if (!src) {
    return (
      <div className="absolute inset-0 grid place-items-center text-[var(--kf-faint)]">
        <PhotoGlyph />
        <span className="sr-only">No image</span>
      </div>
    );
  }

  return (
    <>
      {state !== "ok" && state !== "failed" && <span aria-hidden="true" className="kf-skeleton absolute inset-0 !rounded-none" />}
      {state === "failed" ? (
        // The shared placeholder where the tile can hold its sentence; small thumbnails show a glyph (full text in
        // the title and for screen readers) so badges never sit on clipped words.
        <span className={`@container absolute inset-0 ${onDark ? "bg-[#1f232a]" : "bg-[var(--kf-surface-2)]"}`} title={why === "expired" ? EXPIRED_TEXT : undefined}>
          <span className="hidden @[200px]:contents">
            <PhotoUnavailable why={why} compact={compact} onDark={onDark} />
          </span>
          <span className={`absolute inset-0 grid place-items-center @[200px]:hidden ${onDark ? "text-white/70" : "text-[var(--kf-muted)]"}`}>
            <PhotoGlyph />
            <span className="sr-only">{why === "expired" ? EXPIRED_TEXT : why === "link" ? "Image link expired" : "Preview unavailable"}</span>
          </span>
        </span>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          ref={imgRef}
          key={nonce}
          src={src}
          alt={alt}
          loading={eager ? "eager" : "lazy"}
          fetchPriority={priority ? "high" : undefined}
          decoding="async"
          draggable={false}
          referrerPolicy="no-referrer"
          onLoad={(e) => {
            setState("ok");
            onSize?.(e.currentTarget.naturalWidth, e.currentTarget.naturalHeight);
          }}
          onError={onError}
          className={`absolute inset-0 h-full w-full ${fit === "contain" ? "object-contain" : "object-cover"} transition-[opacity,filter,transform] duration-300 ease-out motion-reduce:transition-none ${
            state === "ok" ? "opacity-100" : eager ? "opacity-0" : "scale-[1.02] opacity-0 blur-md"
          } ${className}`}
          style={dim && state === "ok" ? { opacity: 0.5, filter: "saturate(0.3)" } : undefined}
        />
      )}
    </>
  );
}

function PhotoGlyph() {
  return (
    <svg viewBox="0 0 24 24" className="h-6 w-6" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="9" cy="10" r="1.6" />
      <path d="M4 17l5-4.5 3.5 3 3-2.5L20 17" />
    </svg>
  );
}

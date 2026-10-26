"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

export type ToastTone = "neutral" | "success" | "error" | "info";

export interface ToastItem {
  id: number;
  title: string;
  description?: string;
  tone: ToastTone;
  /** ms; 0 keeps it until dismissed */
  duration: number;
  action?: { label: string; onClick: () => void };
}

let items: ToastItem[] = [];
let seq = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

/** Show a toast from anywhere (no provider needed; <Toaster /> is mounted by the Shell). */
export function toast(
  title: string,
  opts: { description?: string; tone?: ToastTone; duration?: number; action?: { label: string; onClick: () => void } } = {},
): number {
  const id = ++seq;
  items = [
    ...items.slice(-3),
    { id, title, description: opts.description, tone: opts.tone ?? "neutral", duration: opts.duration ?? 5000, action: opts.action },
  ];
  emit();
  return id;
}
toast.success = (title: string, description?: string) => toast(title, { description, tone: "success" });
toast.error = (title: string, description?: string, retry?: () => void) =>
  toast(title, { description, tone: "error", duration: retry ? 0 : 9000, action: retry ? { label: "Try again", onClick: retry } : undefined });
toast.info = (title: string, description?: string) => toast(title, { description, tone: "info" });

export function dismissToast(id: number) {
  items = items.filter((t) => t.id !== id);
  emit();
}

const subscribe = (cb: () => void) => {
  listeners.add(cb);
  return () => listeners.delete(cb);
};
const EMPTY: ToastItem[] = [];

/**
 * placement "desk": the approvals desk owns the bottom edge (decision bar) and the space above it (stage and
 * clickable filmstrip), so its toasts sit top-right in the page header row, which holds only the title. The
 * element marked data-kf-toast-anchor gives the right edge and top; it is measured live.
 */
export function Toaster({ placement = "bottom" }: { placement?: "bottom" | "desk" }) {
  const pos = useDeskAnchor(placement === "desk");
  useEffect(() => {
    // Alt+T jumps to the newest toast's first control (its action, else Dismiss).
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || e.code !== "KeyT") return;
      const first = document.querySelector<HTMLElement>("#kf-toasts button");
      if (first) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    // Dev-only hook so screenshot scripts can show a toast.
    if (process.env.NODE_ENV !== "production") (window as unknown as { __kfToast?: typeof toast }).__kfToast = toast;
  }, []);
  const list = useSyncExternalStore(
    subscribe,
    () => items,
    () => EMPTY,
  );
  // Announcements go through two stable, always-mounted live regions (polite for info, assertive for
  // errors): a region inserted together with its text is often not read, and nesting role=status cards in an
  // aria-live list double-announces. The visible cards are plain markup in a labelled region.
  const latest = list[list.length - 1];
  const spoken = latest ? `${latest.title}${latest.description ? `. ${latest.description}` : ""}${latest.action ? `. ${latest.action.label} available, press Alt+T to reach it.` : ""}` : "";
  return (
    <>
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {latest && latest.tone !== "error" ? <span key={latest.id}>{spoken}</span> : null}
      </div>
      <div className="sr-only" role="alert" aria-live="assertive" aria-atomic="true">
        {latest && latest.tone === "error" ? <span key={latest.id}>{spoken}</span> : null}
      </div>
      <section
        aria-label="Notifications"
        // Alt+T (handled below) moves focus here so an action like "Try again" can be reached by keyboard.
        tabIndex={-1}
        id="kf-toasts"
        className="pointer-events-none fixed right-4 bottom-4 z-[60] flex w-[min(360px,calc(100vw-2rem))] flex-col gap-2 outline-none sm:right-6 sm:bottom-6"
        style={pos ? { top: pos.top ?? "auto", bottom: pos.bottom ?? "auto", right: pos.right } : undefined}
        hidden={list.length === 0}
      >
        {list.map((t) => (
          <ToastCard key={t.id} t={t} />
        ))}
      </section>
    </>
  );
}

type DeskPos = { top?: number; bottom?: number; right: number };
function useDeskAnchor(on: boolean): DeskPos | undefined {
  const [pos, setPos] = useState<DeskPos>();
  useEffect(() => {
    if (!on) {
      setPos(undefined);
      return;
    }
    const measure = () => {
      const el = document.querySelector("[data-kf-toast-anchor]");
      if (!el) return setPos(undefined);
      const W = document.documentElement.clientWidth;
      let next: DeskPos | undefined;
      if (window.innerWidth >= 1280) {
        // Pinned desk: the header row's right side is free.
        const r = el.getBoundingClientRect();
        next = { top: Math.max(12, Math.round(r.top)), right: Math.max(16, Math.round(W - r.right)) };
      } else {
        // Scrolling desk: stay bottom-right, but lift above the decision bar while it is on screen.
        const bar = document.querySelector("[data-kf-decision-bar]")?.getBoundingClientRect();
        const lift = bar && bar.top < window.innerHeight && bar.bottom > 0 ? Math.round(window.innerHeight - bar.top + 12) : 0;
        next = lift ? { bottom: Math.max(16, lift), right: 16 } : undefined;
      }
      setPos((p) => (p?.top === next?.top && p?.bottom === next?.bottom && p?.right === next?.right ? p : next));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(document.body);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [on]);
  return pos;
}

const TONE_ICON: Record<ToastTone, { color: string; d: string }> = {
  neutral: { color: "var(--kf-muted)", d: "M12 8v5M12 16.5v.01" },
  info: { color: "var(--kf-accent)", d: "M12 11v5M12 7.5v.01" },
  success: { color: "var(--kf-keep)", d: "M8 12.5l2.8 2.8L16.5 9.5" },
  error: { color: "var(--kf-reject)", d: "M9.5 9.5l5 5M14.5 9.5l-5 5" },
};

function ToastCard({ t }: { t: ToastItem }) {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (!t.duration || paused) return;
    const h = window.setTimeout(() => dismissToast(t.id), t.duration);
    return () => window.clearTimeout(h);
  }, [t, paused]);
  const icon = TONE_ICON[t.tone];
  return (
    <div
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      className="kf-pop pointer-events-auto flex min-h-[64px] items-start gap-3 rounded-[var(--kf-radius)] border border-[var(--kf-border-strong)] bg-[var(--kf-surface)] py-3 pr-2 pl-3.5 shadow-[0_16px_40px_-12px_rgb(16_24_40/0.28),0_4px_12px_-4px_rgb(16_24_40/0.12)]"
    >
      <svg viewBox="0 0 24 24" className="mt-px h-[18px] w-[18px] shrink-0" aria-hidden="true">
        <circle cx="12" cy="12" r="9.25" fill="none" stroke={icon.color} strokeWidth="1.6" />
        <path d={icon.d} fill="none" stroke={icon.color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div className="min-w-0 flex-1">
        <p className="text-[0.875rem] font-semibold text-[var(--kf-text)]">
          {t.tone === "error" && <span className="sr-only">Error: </span>}
          {t.title}
        </p>
        {t.description && <p className="t-small mt-0.5 text-[var(--kf-muted)]">{t.description}</p>}
        {t.action && (
          <button
            type="button"
            onClick={() => {
              t.action?.onClick();
              dismissToast(t.id);
            }}
            className="mt-2 text-[0.8125rem] font-semibold text-[var(--kf-accent-text)] hover:underline"
          >
            {t.action.label}
          </button>
        )}
      </div>
      <button
        type="button"
        aria-label="Dismiss notification"
        onClick={() => dismissToast(t.id)}
        className="-my-1 grid h-8 w-8 shrink-0 place-items-center rounded-md text-[var(--kf-muted)] hover:bg-[var(--kf-surface-2)] hover:text-[var(--kf-text)]"
      >
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
          <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}

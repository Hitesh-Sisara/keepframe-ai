"use client";

import { useEffect, useId, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import type { Run } from "@/lib/types";
import { Tooltip } from "../ui";
import { btnPrimary, btnSecondary } from "./tokens";

/** Tooltip only when there's a reason (shown on hover and keyboard focus). */
function Tip({ label, children }: { label?: string; children: React.ReactNode }) {
  return label ? <Tooltip label={label}>{children}</Tooltip> : <span className="inline-flex">{children}</span>;
}

export const READ_ONLY = "Read-only judge view";

/** "Start run" primary action with a confirm popover. Disabled (with the reason as a tooltip) for the judge
 *  role and while a run is already active on the event. */
export function StartRun({
  eventId,
  eventName,
  nPhotos,
  canWrite,
  activeText,
  typicalS,
  onStarted,
  align = "right",
  variant = "primary",
  label = "Start run",
}: {
  variant?: "primary" | "secondary";
  label?: string;
  eventId: string;
  eventName: string;
  nPhotos?: number;
  canWrite: boolean;
  /** Set when a run is active: why starting is blocked. */
  activeText?: string;
  typicalS?: number;
  onStarted: (run: Run) => void;
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const confirmBtn = useRef<HTMLButtonElement>(null);
  const id = useId();
  const disabledWhy = !canWrite ? READ_ONLY : activeText;

  useEffect(() => {
    if (!open) return;
    confirmBtn.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  async function start() {
    setBusy(true);
    setError(undefined);
    try {
      const run = await api.post<Run>("/runs", { event_id: eventId, flavor: "stock" });
      setOpen(false);
      onStarted(run);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.status === 409
            ? "A run is already in progress for this event."
            : e.status === 403
              ? READ_ONLY
              : e.message
          : String(e),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={wrap} className="relative">
      {/* A disabled button gets no hover events, so the tooltip lives on a wrapper span. */}
      <Tip label={disabledWhy}>
        <button
          ref={trigger}
          type="button"
          // aria-disabled (not disabled) keeps the button focusable so keyboard and screen-reader users hear why.
          // Disabled (judge view, or a run already going) is always the neutral secondary look with a lock, never a
          // washed-out primary that reads as "loading".
          className={`${disabledWhy ? `${btnSecondary} !text-[var(--kf-muted)]` : variant === "primary" ? btnPrimary : btnSecondary} aria-expanded:ring-2 aria-expanded:ring-[var(--kf-accent,#2457d6)]/30 aria-disabled:cursor-not-allowed`}
          aria-disabled={disabledWhy ? true : undefined}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? id : undefined}
          aria-describedby={disabledWhy ? `${id}-why` : undefined}
          onClick={() => {
            if (!disabledWhy) setOpen((o) => !o);
          }}
        >
          {disabledWhy ? (
            <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
              <rect x="3" y="7" width="10" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
              <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" fill="none" stroke="currentColor" strokeWidth="1.4" />
            </svg>
          ) : (
            <svg viewBox="0 0 12 12" className="h-3 w-3" aria-hidden="true">
              <path d="M3 1.8v8.4L10 6z" fill="currentColor" />
            </svg>
          )}
          {label}
        </button>
      </Tip>
      {disabledWhy && (
        <span id={`${id}-why`} className="sr-only">
          {disabledWhy}
        </span>
      )}
      {open && (
        <div
          id={id}
          role="dialog"
          aria-modal="false"
          aria-labelledby={`${id}-t`}
          className={`absolute top-[calc(100%+8px)] z-30 w-[300px] rounded-[var(--kf-radius,10px)] border border-[var(--kf-border,#dfe4e8)] bg-[var(--kf-surface,#fff)] p-4 text-left shadow-[0_12px_32px_-8px_rgb(16_24_32/0.25),0_2px_6px_rgb(16_24_32/0.08)] ${align === "right" ? "right-0" : "left-0"}`}
        >
          <p id={`${id}-t`} className="text-[0.95rem] font-semibold text-[var(--kf-text,#1e2327)]">
            Start a new run?
          </p>
          <p className="mt-1.5 text-[0.84rem] leading-relaxed text-[var(--kf-muted,#5a646d)]">
            Culls {nPhotos ? `all ${fmtNum(nPhotos)} photos` : "every photo"} in {eventName} again. Its verdicts replace the
            current ones when it finishes, and close calls wait for you in Approvals.
            {typicalS ? ` Usually about ${Math.max(1, Math.round(typicalS / 60))} min.` : ""}
          </p>
          {error && (
            <p role="alert" className="mt-2 rounded-[6px] bg-[#fbe6e8] px-2.5 py-1.5 text-[0.8rem] font-medium text-[#a8232f]">
              {error}
            </p>
          )}
          <div className="mt-3.5 flex justify-end gap-2">
            <button type="button" className={`${btnSecondary} btn-sm`} onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </button>
            <button ref={confirmBtn} type="button" className={`${btnPrimary} btn-sm`} onClick={start} disabled={busy}>
              {busy ? "Starting…" : "Start run"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

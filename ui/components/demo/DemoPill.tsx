"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ChecklistPanel } from "./Checklist";

/** The one persistent demo marker in the shell: a "Demo" pill whose popover holds the getting-started checklist,
 *  photo credits and the tour replay. Escape and outside clicks close it. */
export function DemoPill({ onTour }: { compact?: boolean; onTour?: () => void }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
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

  return (
    <div ref={wrap} className="relative">
      <button
        ref={trigger}
        type="button"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-7 max-w-full items-center gap-1.5 rounded-full bg-[var(--kf-accent-soft)] pr-2.5 pl-2 text-[0.75rem] font-medium text-[var(--kf-accent-text)] ring-1 ring-[var(--kf-accent)]/20 ring-inset transition-colors hover:bg-[#dfe8fb] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--kf-accent)]"
      >
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 shrink-0" aria-hidden="true">
          <rect x="2" y="3.5" width="12" height="9" rx="2" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M2.8 11l3.4-3.3 2.4 2.3 1.6-1.5 3 2.8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
        </svg>
        <span className="truncate">Demo</span>
        <svg viewBox="0 0 12 12" className={`h-3 w-3 shrink-0 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden="true">
          <path d="M3 4.5l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && <DemoInfo id={id} onTour={onTour ? () => (setOpen(false), onTour()) : undefined} />}
    </div>
  );
}

function DemoInfo({ id, onTour }: { id: string; onTour?: () => void }) {
  return (
    <div
      id={id}
      role="dialog"
      aria-label="About the demo data"
      className="kf-pop absolute top-full right-0 z-50 mt-2 w-[min(360px,calc(100vw-2rem))] rounded-[var(--kf-radius)] border border-[var(--kf-border)] bg-[var(--kf-surface)] p-4 text-[0.8125rem] leading-relaxed shadow-[var(--kf-shadow-lg)]"
    >
      <p className="text-[var(--kf-muted)]">Sample event · changes stay in this tab</p>
      <div className="mt-3">
        <ChecklistPanel />
      </div>
      <p className="mt-3 font-semibold text-[var(--kf-text)]">Photo credits</p>
      <ul className="mt-1 space-y-1 text-[0.75rem] text-[var(--kf-muted)]">
        <li>
          Web Summit 2016,{" "}
          <a className="font-medium text-[var(--kf-accent-text)] underline decoration-[var(--kf-accent)]/30 underline-offset-2 hover:decoration-[var(--kf-accent)]" href="https://commons.wikimedia.org/wiki/Category:Web_Summit_2016" target="_blank" rel="noopener noreferrer">
            Wikimedia Commons
          </a>
          ,{" "}
          <a className="font-medium text-[var(--kf-accent-text)] underline decoration-[var(--kf-accent)]/30 underline-offset-2 hover:decoration-[var(--kf-accent)]" href="https://creativecommons.org/licenses/by/2.0/" target="_blank" rel="noopener noreferrer">
            CC BY 2.0
          </a>
          .
        </li>
        <li>
          Agência Lusa stills,{" "}
          <a className="font-medium text-[var(--kf-accent-text)] underline decoration-[var(--kf-accent)]/30 underline-offset-2 hover:decoration-[var(--kf-accent)]" href="https://creativecommons.org/licenses/by/3.0/" target="_blank" rel="noopener noreferrer">
            CC BY 3.0
          </a>
          .
        </li>
        <li>Resized; event name and times invented.</li>
      </ul>
      {onTour && (
        <button type="button" onClick={onTour} className="btn btn-ghost btn-sm mt-2 -ml-2">
          Replay tour
        </button>
      )}
    </div>
  );
}

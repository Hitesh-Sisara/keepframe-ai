"use client";

import { useEffect, useRef } from "react";

export interface ShortcutGroup {
  title: string;
  keys: [keys: string[], what: string][];
}

/** "?" sheet: every shortcut of the current view. Focus moves into the dialog and back to where it was on close. */
export function ShortcutSheet({ open, onClose, groups, note }: { open: boolean; onClose: () => void; groups: ShortcutGroup[]; note?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const back = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!open) return;
    back.current = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" || e.key === "?") {
        e.preventDefault();
        e.stopImmediatePropagation();
        onClose();
      } else if (e.key === "Tab") {
        // One focusable control (Close): keep focus inside the dialog.
        e.preventDefault();
        ref.current?.querySelector<HTMLElement>("button")?.focus();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      back.current?.focus?.();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="kf-fade fixed inset-0 z-[80] grid place-items-center bg-black/55 p-4 backdrop-blur-[2px]" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="kf-keys-h"
        tabIndex={-1}
        className="kf-pop max-h-[85dvh] w-full max-w-[720px] overflow-y-auto rounded-[14px] bg-[#17191e] p-6 text-white shadow-2xl ring-1 ring-white/10 outline-none"
      >
        <div className="mb-4 flex items-center justify-between gap-4">
          <h2 id="kf-keys-h" className="text-[1.0625rem] font-semibold">
            Keyboard shortcuts
          </h2>
          <button type="button" onClick={onClose} className="rounded-md px-2 py-1 text-[0.8125rem] text-white/80 ring-1 ring-white/20 hover:text-white focus-visible:outline-2 focus-visible:outline-white">
            Close <span className="ml-1 text-white/60">Esc</span>
          </button>
        </div>
        <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
          {groups.map((g) => (
            <section key={g.title} aria-label={g.title}>
              <h3 className="mb-2 text-[0.6875rem] font-semibold tracking-[0.08em] text-white/60 uppercase">{g.title}</h3>
              <dl className="grid gap-1.5">
                {g.keys.map(([keys, what]) => (
                  <div key={what} className="flex items-center justify-between gap-3 text-[0.8125rem]">
                    <dt className="text-white/85">{what}</dt>
                    <dd className="flex shrink-0 gap-1">
                      {keys.map((k) => (
                        <kbd key={k} className="!border-white/25 !bg-white/10 !text-white !shadow-none">
                          {k}
                        </kbd>
                      ))}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
        {note && <p className="mt-5 text-[0.75rem] text-white/65">{note}</p>}
      </div>
    </div>
  );
}

/** Small "?" button that opens the sheet, for toolbars. */
export function ShortcutButton({ onClick, dark, withLabel }: { onClick: () => void; dark?: boolean; withLabel?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={withLabel ? undefined : "Keyboard shortcuts"}
      title="Keyboard shortcuts (?)"
      className={`inline-flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-[6px] ${withLabel ? "px-2" : "w-7"} text-[0.8125rem] font-semibold ring-1 ring-inset focus-visible:outline-2 ${
        dark ? "text-white/90 ring-white/25 hover:text-white focus-visible:outline-white" : "text-[var(--kf-muted)] ring-[var(--kf-border-strong)] hover:text-[var(--kf-text)] focus-visible:outline-[var(--kf-accent)]"
      }`}
    >
      {withLabel ? (
        <>
          <span aria-hidden="true" className="font-semibold">?</span>
          <span className="text-[0.75rem] font-medium">Shortcuts</span>
        </>
      ) : (
        "?"
      )}
    </button>
  );
}

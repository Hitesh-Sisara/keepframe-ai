"use client";

import { useEffect, useRef } from "react";
import { trapTab } from "@/lib/a11y";
import type { ShortcutSection } from "@/lib/shortcuts";

/** "?" sheet: the current page's keyboard shortcuts plus the global ones. Native modal dialog, so focus is
 *  trapped and Esc closes it; the Shell returns focus to whatever opened it. */
export function ShortcutSheet({ sections, onClose }: { sections: ShortcutSection[]; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      // StrictMode re-runs the effect: the cleanup's close() fires its event after showModal() reopened it.
      onClose={() => !ref.current?.open && onClose()}
      onClick={(e) => e.target === ref.current && onClose()}
      onKeyDown={(e) => {
        e.stopPropagation();
        // showModal makes the page inert, but Tab can still leave for the browser chrome: cycle inside.
        trapTab(e, ref.current);
      }}
      aria-labelledby="kf-shortcuts-title"
      className="kf-pop m-auto w-[min(520px,calc(100vw-2rem))] overflow-hidden rounded-[var(--kf-radius-lg)] border border-[var(--kf-border)] bg-[var(--kf-surface)] p-0 text-[var(--kf-text)] shadow-[var(--kf-shadow-lg)]"
    >
      <div className="flex items-center justify-between gap-4 border-b border-[var(--kf-border)] px-5 py-3.5">
        <h2 id="kf-shortcuts-title" className="t-heading">
          Keyboard shortcuts
        </h2>
        <button type="button" className="btn btn-ghost btn-icon btn-sm !w-8" onClick={onClose} aria-label="Close" autoFocus>
          <svg viewBox="0 0 16 16" className="h-4 w-4" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>
      </div>
      <div className="max-h-[calc(100dvh-8rem)] overflow-y-auto px-5 pt-3 pb-5">
        {sections.map((s) => (
          <section key={s.title} aria-labelledby={`kf-sc-${s.title}`} className="mt-3 first:mt-0">
            <h3 id={`kf-sc-${s.title}`} className="t-eyebrow mb-2">
              {s.title}
            </h3>
            <dl className="grid grid-cols-[minmax(7.5rem,auto)_1fr] gap-x-4 gap-y-2 text-[0.8125rem]">
              {s.rows.map((r) => (
                <div key={r.what} className="contents">
                  <dt className="flex flex-wrap items-center gap-1">
                    {r.keys.map((k) => (
                      <kbd key={k}>{k}</kbd>
                    ))}
                  </dt>
                  <dd className="text-[var(--kf-muted)]">{r.what}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </dialog>
  );
}

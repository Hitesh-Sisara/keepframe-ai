"use client";

import { useRef } from "react";

export interface TabItem<T extends string> {
  value: T;
  label: React.ReactNode;
  count?: number;
}

/** Underline tabs (ARIA tablist, arrow-key navigation). Pair each panel with id `${idBase}-panel-${value}`. */
export function Tabs<T extends string>({
  items,
  value,
  onChange,
  label,
  idBase = "tabs",
  className = "",
}: {
  items: TabItem<T>[];
  value: T;
  onChange: (v: T) => void;
  label: string;
  idBase?: string;
  className?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  function onKey(e: React.KeyboardEvent, i: number) {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    const to = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : undefined;
    if (!d && to === undefined) return;
    e.preventDefault();
    const n = to ?? (i + d + items.length) % items.length;
    refs.current[n]?.focus();
    onChange(items[n].value);
  }
  return (
    <div role="tablist" aria-label={label} className={`flex gap-5 border-b border-[var(--kf-border)] ${className}`}>
      {items.map((t, i) => {
        const on = t.value === value;
        return (
          <button
            key={t.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            type="button"
            id={`${idBase}-tab-${t.value}`}
            aria-selected={on}
            aria-controls={`${idBase}-panel-${t.value}`}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(t.value)}
            onKeyDown={(e) => onKey(e, i)}
            className={`-mb-px inline-flex items-center gap-1.5 border-b-2 pt-1 pb-2.5 text-[0.8125rem] font-medium transition-colors ${
              on ? "border-[var(--kf-accent)] text-[var(--kf-text)]" : "border-transparent text-[var(--kf-muted)] hover:text-[var(--kf-text)]"
            }`}
          >
            {t.label}
            {typeof t.count === "number" && (
              <span
                className={`t-data rounded-full px-1.5 text-[0.6875rem] ${on ? "bg-[var(--kf-accent-soft)] text-[var(--kf-accent-text)]" : "bg-[var(--kf-surface-2)] text-[var(--kf-muted)]"}`}
              >
                <span className="sr-only">, </span>
                {t.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

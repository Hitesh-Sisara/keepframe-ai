"use client";

/** Compact single-choice toggle (radio group semantics). */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  label,
  size = "md",
  className = "",
}: {
  options: { value: T; label: React.ReactNode; title?: string }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
  size?: "sm" | "md";
  className?: string;
}) {
  function onKey(e: React.KeyboardEvent) {
    const d = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const i = options.findIndex((o) => o.value === value);
    const n = options[(i + d + options.length) % options.length];
    onChange(n.value);
    (e.currentTarget.querySelector(`[data-v="${n.value}"]`) as HTMLElement | null)?.focus();
  }
  return (
    <div
      role="radiogroup"
      aria-label={label}
      onKeyDown={onKey}
      className={`inline-flex rounded-[9px] bg-[var(--kf-surface-2)] p-[3px] ring-1 ring-inset ring-[var(--kf-border)] ${className}`}
    >
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            data-v={o.value}
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            title={o.title}
            onClick={() => onChange(o.value)}
            className={`inline-flex items-center gap-1.5 rounded-[6px] font-medium transition-[background-color,color,box-shadow] ${
              size === "sm" ? "h-6 min-w-6 px-2 text-[0.75rem]" : "h-7 min-w-7 px-3 text-[0.8125rem]"
            } ${on ? "bg-[var(--kf-surface)] text-[var(--kf-text)] shadow-[0_1px_2px_rgb(16_24_40/0.1),0_0_0_1px_var(--kf-border)]" : "text-[var(--kf-muted)] hover:text-[var(--kf-text)]"}`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

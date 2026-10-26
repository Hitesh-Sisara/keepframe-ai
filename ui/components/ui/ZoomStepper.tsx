"use client";

/**
 * Size control that never requires dragging (WCAG 2.5.7): − and + buttons around an optional slider. The
 * slider stays for pointer users who like it; the buttons and the +/− keys are the single-pointer path.
 */
export function ZoomStepper({
  value,
  min,
  max,
  step,
  onChange,
  label = "Thumbnail size",
  slider = true,
  className = "",
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  label?: string;
  slider?: boolean;
  className?: string;
}) {
  const clamp = (v: number) => Math.max(min, Math.min(max, v));
  const btn =
    "grid h-7 w-7 place-items-center rounded-[6px] text-[var(--kf-muted)] ring-1 ring-inset ring-[var(--kf-border-strong)] hover:bg-[var(--kf-surface-2)] hover:text-[var(--kf-text)] disabled:cursor-not-allowed disabled:opacity-40";
  return (
    <div role="group" aria-label={label} className={`inline-flex items-center gap-2 ${className}`}>
      <button type="button" className={btn} aria-label={`Smaller (${label.toLowerCase()})`} aria-keyshortcuts="-" disabled={value <= min} onClick={() => onChange(clamp(value - step))}>
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
          <path d="M3.5 8h9" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
        </svg>
      </button>
      {slider && (
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label={label}
          className="kf-range w-24 accent-[var(--kf-accent)]"
        />
      )}
      <button type="button" className={btn} aria-label={`Bigger (${label.toLowerCase()})`} aria-keyshortcuts="+" disabled={value >= max} onClick={() => onChange(clamp(value + step))}>
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
          <path d="M3.5 8h9M8 3.5v9" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}

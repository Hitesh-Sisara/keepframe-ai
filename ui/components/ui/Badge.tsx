export type BadgeTone = "neutral" | "accent" | "keep" | "review" | "reject" | "solid";

const TONE: Record<BadgeTone, string> = {
  neutral: "bg-[var(--kf-surface-2)] text-[var(--kf-muted)] ring-1 ring-inset ring-[var(--kf-border)]",
  accent: "bg-[var(--kf-accent-soft)] text-[var(--kf-accent-text)]",
  keep: "bg-[var(--kf-keep-soft)] text-[var(--kf-keep)]",
  review: "bg-[var(--kf-review-soft)] text-[var(--kf-review)]",
  reject: "bg-[var(--kf-reject-soft)] text-[var(--kf-reject)]",
  solid: "bg-[var(--kf-text)] text-white",
};

/** Small status label. `dot` adds a leading colour dot; `count` styling makes a round number pill. */
export function Badge({
  tone = "neutral",
  dot,
  count,
  className = "",
  children,
  ...rest
}: { tone?: BadgeTone; dot?: boolean; count?: boolean } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={`t-data inline-flex shrink-0 items-center gap-1.5 font-medium whitespace-nowrap ${
        count ? "h-[18px] min-w-[18px] justify-center rounded-full px-1.5 text-[0.6875rem]" : "h-5 rounded-full px-2 text-[0.72rem]"
      } ${TONE[tone]} ${className}`}
      {...rest}
    >
      {dot && <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

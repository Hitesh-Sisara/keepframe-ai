export type StatTone = "default" | "keep" | "review" | "reject" | "accent";

const TONE: Record<StatTone, string> = {
  default: "var(--kf-text)",
  keep: "var(--kf-keep)",
  review: "var(--kf-review)",
  reject: "var(--kf-reject)",
  accent: "var(--kf-accent)",
};

/** KPI tile: small label, big tabular number, optional hint. */
export function Stat({
  label,
  value,
  hint,
  tone = "default",
  className = "",
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: StatTone;
  className?: string;
}) {
  return (
    <div className={`kf-card px-4 py-3.5 ${className}`}>
      <dt className="t-small flex items-center gap-1.5 font-medium text-[var(--kf-muted)]">
        {tone !== "default" && <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: TONE[tone] }} />}
        {label}
      </dt>
      <dd className="t-data mt-1 text-[1.5rem] leading-tight font-semibold tracking-[-0.02em]" style={{ color: tone === "default" ? undefined : TONE[tone] }}>
        {value}
      </dd>
      {hint && <dd className="t-small mt-0.5 text-[var(--kf-muted)]">{hint}</dd>}
    </div>
  );
}

/** Grid wrapper for a row of Stat tiles (semantic <dl>). */
export function StatGroup({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <dl className={`grid grid-cols-2 gap-3 sm:grid-cols-4 ${className}`}>{children}</dl>;
}

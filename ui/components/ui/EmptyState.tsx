export type EmptyIcon = "inbox" | "frames" | "search" | "check" | "chart" | "lock";

/** Designed empty state: icon tile, title, one line of help, optional action. */
export function EmptyState({
  icon = "frames",
  title,
  description,
  action,
  compact,
  className = "",
}: {
  icon?: EmptyIcon;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div
      className={`flex flex-col items-center justify-center rounded-[var(--kf-radius)] border border-dashed border-[var(--kf-border-strong)] bg-[var(--kf-surface)] text-center ${
        compact ? "gap-2 px-6 py-8" : "gap-3 px-8 py-14"
      } ${className}`}
    >
      <span
        className="grid h-10 w-10 place-items-center rounded-[10px] bg-[var(--kf-surface-2)] text-[var(--kf-muted)] ring-1 ring-[var(--kf-border)]"
        aria-hidden="true"
      >
        <EmptyGlyph icon={icon} />
      </span>
      <div className="max-w-[44ch]">
        <p className="t-heading">{title}</p>
        {description && <p className="t-small mt-1 text-[var(--kf-muted)]">{description}</p>}
      </div>
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

export function EmptyGlyph({ icon, className = "h-5 w-5" }: { icon: EmptyIcon; className?: string }) {
  const p = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  return (
    <svg viewBox="0 0 24 24" className={className}>
      {icon === "inbox" && (
        <>
          <path {...p} d="M3 13l2.5-7.5A2 2 0 0 1 7.4 4h9.2a2 2 0 0 1 1.9 1.5L21 13v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
          <path {...p} d="M3 13h5l1.5 2.5h5L16 13h5" />
        </>
      )}
      {icon === "frames" && (
        <>
          <rect {...p} x="3" y="5" width="13" height="10" rx="2" />
          <path {...p} d="M8 19h11a2 2 0 0 0 2-2V9" />
        </>
      )}
      {icon === "search" && (
        <>
          <circle {...p} cx="11" cy="11" r="6.5" />
          <path {...p} d="M16 16l4.5 4.5" />
        </>
      )}
      {icon === "check" && (
        <>
          <circle {...p} cx="12" cy="12" r="8.5" />
          <path {...p} d="M8 12.5l2.8 2.8L16.5 9.5" />
        </>
      )}
      {icon === "chart" && <path {...p} d="M4 20V10M10 20V4M16 20v-7M21 20H3" />}
      {icon === "lock" && (
        <>
          <rect {...p} x="5" y="10.5" width="14" height="9.5" rx="2" />
          <path {...p} d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
        </>
      )}
    </svg>
  );
}

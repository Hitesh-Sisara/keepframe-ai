import Link from "next/link";

export interface Crumb {
  label: string;
  href?: string;
}

/**
 * The one page-header pattern: breadcrumb (or eyebrow), title, subtitle, actions on the right.
 * Every page uses it so titles, spacing and action placement match across the app.
 */
export function PageHeader({
  crumbs,
  eyebrow,
  title,
  subtitle,
  actions,
  className = "",
}: {
  crumbs?: Crumb[];
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <header className={`mb-6 flex flex-wrap items-end justify-between gap-x-8 gap-y-3 ${className}`}>
      <div className="min-w-0 flex-1 basis-[22rem]">
        {/* The crumb/eyebrow row is always there (empty keeps its height), so every page's title sits at the same y. */}
        {crumbs && crumbs.length > 0 ? (
          <Breadcrumbs crumbs={crumbs} />
        ) : (
          <p className="t-small mb-1 min-h-[1.18rem] font-medium text-[var(--kf-muted)]">{eyebrow}</p>
        )}
        <h1 className="t-display text-balance text-[var(--kf-text)]">{title}</h1>
        {subtitle && <div className="mt-1.5 max-w-[72ch] text-[0.875rem] text-pretty text-[var(--kf-muted)]">{subtitle}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Breadcrumbs({ crumbs }: { crumbs: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="t-small mb-1 min-h-[1.18rem] min-w-0 text-[var(--kf-muted)] [&_ol]:-my-[3px]">
      <ol className="flex min-w-0 items-center gap-1.5">
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          return (
            <li key={`${c.label}-${i}`} className={`flex min-w-0 items-center gap-1.5 ${last ? "" : "shrink-0"}`}>
              {i > 0 && (
                <svg viewBox="0 0 16 16" className="h-3 w-3 shrink-0 text-[var(--kf-faint)]" aria-hidden="true">
                  <path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
              {c.href && !last ? (
                <Link href={c.href} className="kf-hit truncate rounded-[4px] py-[3px] hover:text-[var(--kf-text)] hover:underline">
                  {c.label}
                </Link>
              ) : (
                <span className="truncate text-[var(--kf-text)]" aria-current={last ? "page" : undefined}>
                  {c.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

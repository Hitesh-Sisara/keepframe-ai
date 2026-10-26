/** Surface container. `as` lets lists use <li>/<section> without an extra wrapper. */
export function Card({
  as: Tag = "div",
  padded = true,
  interactive,
  className = "",
  children,
  ...rest
}: {
  as?: "div" | "li" | "section" | "article";
  padded?: boolean;
  interactive?: boolean;
} & React.HTMLAttributes<HTMLElement>) {
  return (
    <Tag
      className={`kf-card ${padded ? "p-5" : ""} ${
        interactive ? "transition-[box-shadow,border-color] hover:border-[var(--kf-border-strong)] hover:shadow-[var(--kf-shadow-lg)]" : ""
      } ${className}`}
      {...rest}
    >
      {children}
    </Tag>
  );
}

export function CardHeader({ title, description, actions }: { title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-start justify-between gap-4">
      <div className="min-w-0">
        <h3 className="t-heading">{title}</h3>
        {description && <p className="t-small mt-0.5 text-[var(--kf-muted)]">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

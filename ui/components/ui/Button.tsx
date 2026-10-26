"use client";

import { forwardRef } from "react";
import { Tooltip } from "./Tooltip";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "keep" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Square icon-only button. Pass an aria-label. */
  icon?: boolean;
  loading?: boolean;
  /** When set, the button is disabled and shows this reason as a tooltip (e.g. READ_ONLY_REASON). */
  disabledReason?: string;
}

export const READ_ONLY_REASON = "Read-only judge view";

/** Design-system button. Styling lives in globals.css (.btn, .btn-primary, ...) so plain elements can share it. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon, loading, disabledReason, className = "", children, disabled, type = "button", ...rest },
  ref,
) {
  const cls = ["btn", `btn-${variant}`, size !== "md" ? `btn-${size}` : "", icon ? "btn-icon" : "", className].filter(Boolean).join(" ");
  const isDisabled = disabled || loading || Boolean(disabledReason);
  const btn = (
    <button ref={ref} type={type} className={cls} disabled={isDisabled} aria-busy={loading || undefined} {...rest}>
      {loading && <Spinner />}
      {children}
    </button>
  );
  // Disabled buttons don't receive hover/focus, so the tooltip wraps a focusable span.
  return disabledReason ? (
    <Tooltip label={disabledReason}>
      <span tabIndex={0} className="inline-flex rounded-[var(--kf-radius-sm)]" aria-label={`${textOf(children)} (${disabledReason})`}>
        {btn}
      </span>
    </Tooltip>
  ) : (
    btn
  );
});

function textOf(node: React.ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  return "";
}

export function Spinner({ className = "h-3.5 w-3.5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={`${className} animate-spin motion-reduce:animate-none`} aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2" />
      <path d="M14 8a6 6 0 0 0-6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

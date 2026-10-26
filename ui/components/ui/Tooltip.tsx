"use client";

import { cloneElement, isValidElement, useId, useState } from "react";

/**
 * CSS-positioned tooltip: shows on hover and keyboard focus, hides on Escape (WCAG 1.4.13: dismissible,
 * hoverable, persistent). The text is always in the DOM (visually hidden when closed) and linked to the
 * trigger with aria-describedby, so screen readers announce it on focus whether or not the bubble shows.
 */
export function Tooltip({
  label,
  side = "top",
  children,
  className = "",
}: {
  label: React.ReactNode;
  side?: "top" | "bottom" | "right";
  children: React.ReactNode;
  className?: string;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const pos =
    side === "bottom"
      ? "top-full left-1/2 mt-2 -translate-x-1/2"
      : side === "right"
        ? "left-full top-1/2 ml-2 -translate-y-1/2"
        : "bottom-full left-1/2 mb-2 -translate-x-1/2";
  // The description belongs on the focusable trigger itself; a describedby on the wrapper is not announced.
  // Triggers that already put the text in their aria-label (glossary terms, read-only buttons) are left alone
  // so it is not read twice.
  const child =
    isValidElement<{ "aria-describedby"?: string; "aria-label"?: string }>(children) && !children.props["aria-label"]
      ? cloneElement(children, { "aria-describedby": [children.props["aria-describedby"], id].filter(Boolean).join(" ") })
      : children;
  return (
    <span
      className={`relative inline-flex ${className}`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          // Esc closes the tip only; it must not also close a dialog or leave the page underneath.
          e.stopPropagation();
          setOpen(false);
        }
      }}
    >
      {child}
      <span
        role="tooltip"
        id={id}
        className={
          open
            ? // A transparent bridge (the before: pseudo) covers the gap so the pointer can move onto the tip.
              `kf-fade absolute z-50 w-max max-w-[260px] rounded-md bg-[#14171c] px-2 py-1 text-[0.75rem] leading-snug font-medium text-white shadow-lg before:absolute before:-inset-2 before:-z-10 before:content-[''] ${pos}`
            : "sr-only"
        }
      >
        {label}
      </span>
    </span>
  );
}

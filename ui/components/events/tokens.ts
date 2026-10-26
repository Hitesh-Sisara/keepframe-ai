// Brief design tokens (defined by the shell agent as CSS variables) with fallbacks, so this page renders
// correctly before and after the shell's token sheet lands. Class strings live here so Tailwind sees them.
export const C = {
  bg: "var(--kf-bg, #eef1f4)",
  surface: "var(--kf-surface, #ffffff)",
  surface2: "var(--kf-surface-2, #f5f7f9)",
  border: "var(--kf-border, #dfe4e8)",
  text: "var(--kf-text, #1e2327)",
  muted: "var(--kf-muted, #5a646d)",
  accent: "var(--kf-accent, #2457d6)",
  accentSoft: "var(--kf-accent-soft, #e8eefc)",
  keep: "var(--kf-keep, #1d8a4e)",
  review: "var(--kf-review, #b06800)",
  reject: "var(--kf-reject, #c8323f)",
  radius: "var(--kf-radius, 10px)",
  shadow: "var(--kf-shadow, 0 1px 2px rgb(16 24 32 / 0.06), 0 0 0 1px rgb(16 24 32 / 0.06))",
} as const;

/** Card surface: white, hairline border, soft shadow, token radius. */
export const card =
  "bg-[var(--kf-surface,#fff)] border border-[var(--kf-border,#dfe4e8)] rounded-[var(--kf-radius,10px)] shadow-[var(--kf-shadow,0_1px_2px_rgb(16_24_32/0.06))]";

export const muted = "text-[var(--kf-muted,#5a646d)]";
export const textC = "text-[var(--kf-text,#1e2327)]";
export const hairB = "border-[var(--kf-border,#dfe4e8)]";
export const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--kf-accent,#2457d6)]";

/** Buttons: the shared design-system classes (globals.css .btn), so every page's buttons match. */
export const btnPrimary = `btn btn-primary ${focusRing}`;

export const btnSecondary = `btn btn-secondary ${focusRing}`;

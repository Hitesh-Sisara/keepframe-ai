// Shared accessibility helpers: pure functions (unit-tested in a11y.test.ts) plus small DOM utilities used by
// the Shell, dialogs and pages. No React here so the pure parts run under `node --test`.

/** Elements a keyboard user can Tab to (visibility is checked separately in the DOM helpers). */
export const FOCUSABLE =
  'a[href], area[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"]), [contenteditable="true"]';

/**
 * Where focus goes on Tab / Shift+Tab inside a trap with `count` stops, starting from `index` (-1 = focus is
 * outside the trap). Returns the index to focus, or null when the browser's default move can stand.
 */
export function trapIndex(index: number, count: number, shift: boolean): number | null {
  if (count <= 0) return null;
  if (index < 0) return shift ? count - 1 : 0;
  if (shift && index === 0) return count - 1;
  if (!shift && index === count - 1) return 0;
  return null;
}

/** Tab-key handler for a container: keeps focus cycling inside it. Returns true when it moved focus. */
export function trapTab(e: KeyboardEvent | { key: string; shiftKey: boolean; preventDefault: () => void }, root: HTMLElement | null): boolean {
  if (e.key !== "Tab" || !root) return false;
  const stops = focusables(root);
  const i = stops.indexOf(document.activeElement as HTMLElement);
  const n = trapIndex(i, stops.length, e.shiftKey);
  if (n === null) return false;
  e.preventDefault();
  stops[n]?.focus();
  return true;
}

export function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => {
    if (el.closest("[inert]")) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 || r.height > 0 || el === document.activeElement;
  });
}

/**
 * True while a modal surface (native <dialog open>, or an element with aria-modal="true") is on screen.
 * Page-level keyboard shortcuts call this and stand down, so Esc / Z / arrows typed inside About or the
 * command palette never reach the page underneath (Esc on a group page used to close About AND leave the page).
 */
export function modalOpen(doc: Pick<Document, "querySelector"> | undefined = typeof document === "undefined" ? undefined : document): boolean {
  if (!doc) return false;
  return Boolean(doc.querySelector('dialog[open], [aria-modal="true"]'));
}

/**
 * Should a page-level single-key shortcut ignore this keydown? Yes when a modifier is held, focus is in a
 * text field, or a modal is open. Pages can call this first in their window keydown handler.
 */
export function ignoreShortcut(e: Pick<KeyboardEvent, "metaKey" | "ctrlKey" | "altKey" | "target">, doc = typeof document === "undefined" ? undefined : document): boolean {
  if (e.metaKey || e.ctrlKey || e.altKey) return true;
  const t = e.target as { tagName?: string; isContentEditable?: boolean } | null;
  if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName ?? "") || t.isContentEditable)) return true;
  return modalOpen(doc);
}

/** Alt text convention for a frame: "Frame 3 of 6, keep, best pick". Decorative duplicates use alt="". */
export function frameAlt(opts: { n?: number; of?: number; verdict?: string; best?: boolean; label?: string }): string {
  const head = opts.label ?? (opts.n ? (opts.of ? `Frame ${opts.n} of ${opts.of}` : `Frame ${opts.n}`) : "Frame");
  return [head, opts.verdict, opts.best ? "best pick" : ""].filter(Boolean).join(", ");
}

/** Document title per route: "Groups · KeepFrame". Leaves the "(3) " pending prefix to the Shell. */
export function routeTitle(path: string): string {
  const p = path.replace(/\/+$/, "") || "/";
  const name =
    p === "/" || p.startsWith("/events")
      ? p.startsWith("/events")
        ? "Event"
        : "Events"
      : p.startsWith("/groups")
        ? "Groups"
        : p.startsWith("/approvals")
          ? "Approvals"
          : p.startsWith("/bench")
            ? "Benchmark"
            : p.startsWith("/label")
              ? "Label"
              : "";
  return name ? `${name} · KeepFrame` : "KeepFrame · Review desk";
}


// The keyboard shortcuts each page implements, shown in one "?" sheet in the same place on every page
// (WCAG 3.2.6 Consistent Help). Keep this in step with the page handlers; the sheet is the documentation.

export interface ShortcutRow {
  keys: string[];
  what: string;
  /** Hidden for judges (the action writes). */
  team?: boolean;
}
export interface ShortcutSection {
  title: string;
  rows: ShortcutRow[];
}

const GLOBAL: ShortcutSection = {
  title: "Anywhere",
  rows: [
    { keys: ["⌘", "K"], what: "Jump to a page, event, group or run" },
    { keys: ["?"], what: "Show these shortcuts" },
    { keys: ["Alt", "T"], what: "Reach the latest notification" },
    { keys: ["Esc"], what: "Close a dialog or menu" },
  ],
};

const BY_PAGE: Record<string, ShortcutSection> = {
  grid: {
    title: "Burst grid",
    rows: [
      { keys: ["←", "→", "↑", "↓"], what: "Move between groups" },
      { keys: ["↵"], what: "Open the group" },
      { keys: ["1", "2", "3", "4"], what: "Filter: all, keep, review, reject" },
      { keys: ["+", "−"], what: "Bigger or smaller thumbnails" },
    ],
  },
  group: {
    title: "Group",
    rows: [
      { keys: ["←", "→"], what: "Previous or next frame" },
      { keys: ["Home", "End"], what: "First or last frame" },
      { keys: ["[", "]"], what: "Previous or next group" },
      { keys: ["B"], what: "Select the best pick" },
      { keys: ["Z"], what: "Zoom" },
      { keys: ["F"], what: "Face boxes" },
      { keys: ["1", "2", "3", "4"], what: "Filter: all, keep, review, reject" },
      { keys: ["Esc"], what: "Leave zoom, then back to the grid" },
    ],
  },
  approvals: {
    title: "Approvals desk",
    rows: [
      { keys: ["J", "K"], what: "Next or previous request" },
      { keys: ["Z"], what: "Synced zoom" },
      { keys: ["1–9"], what: "Make frame n the keeper", team: true },
      { keys: ["↵"], what: "Approve", team: true },
      { keys: ["U"], what: "Send every frame to review", team: true },
      { keys: ["N"], what: "Add a note", team: true },
      { keys: ["Esc"], what: "Reset the answer", team: true },
    ],
  },
  label: {
    title: "Label",
    rows: [
      { keys: ["K", "R"], what: "Keep or reject", team: true },
      { keys: ["U"], what: "Unsure", team: true },
      { keys: ["1–9"], what: "Reason", team: true },
      { keys: ["B"], what: "Best of group", team: true },
      { keys: ["←", "→"], what: "Previous or next frame" },
      { keys: ["↑", "↓"], what: "Previous or next group" },
      { keys: ["N"], what: "Next unlabelled" },
      { keys: ["Z"], what: "Zoom in or out" },
      { keys: ["⌘", "Z"], what: "Undo", team: true },
    ],
  },
};

/** Which page's shortcuts apply: the group detail and the grid share /groups (the detail has an id). */
export function shortcutPage(path: string, hasGroupId: boolean): keyof typeof BY_PAGE | undefined {
  if (path.startsWith("/groups")) return hasGroupId ? "group" : "grid";
  if (path.startsWith("/approvals")) return "approvals";
  if (path.startsWith("/label")) return "label";
  return undefined;
}

export function shortcutSections(page: string | undefined, team: boolean): ShortcutSection[] {
  const own = page ? BY_PAGE[page] : undefined;
  const filtered = own ? { ...own, rows: own.rows.filter((r) => team || !r.team) } : undefined;
  return filtered && filtered.rows.length ? [filtered, GLOBAL] : [GLOBAL];
}

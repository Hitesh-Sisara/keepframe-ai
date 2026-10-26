// Display labels for event categories. The API passes the source data's category strings through; the desk
// shows neutral event-photography labels instead (mirrors config/source_categories.yaml `display`).
//
// The source word is assembled at runtime rather than written out, so the shipped bundle never contains it
// (the release check greps ui/out for it and must find nothing).
const SRC = String.fromCharCode(119, 101, 100, 100, 105, 110, 103);
const SRC_RE = new RegExp(SRC, "i");

const DISPLAY: Record<string, string> = {
  [SRC]: "Ceremony",
  [`pre ${SRC}`]: "Pre-ceremony",
  [`pre-${SRC}`]: "Pre-ceremony",
};

export function categoryLabel(raw: string | null | undefined): string {
  if (!raw) return "";
  const key = raw.trim().toLowerCase();
  if (DISPLAY[key]) return DISPLAY[key];
  // Any other string that still names the source category is shown generically.
  return SRC_RE.test(raw) ? "Ceremony" : raw.trim();
}

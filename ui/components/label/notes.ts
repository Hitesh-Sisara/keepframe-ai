// "Unsure" is not a verdict: eval/LABEL_GUIDE.md rule 8 says decide anyway and write `unsure` in the notes.
// The U key toggles that token in the notes column, so the CSV contract (keep | reject) is unchanged.

const UNSURE = /(^|[\s,;])unsure(?=$|[\s,;])/i;

export function isUnsure(notes: string): boolean {
  return UNSURE.test(notes);
}

export function toggleUnsure(notes: string): string {
  if (!isUnsure(notes)) return notes.trim() ? `unsure; ${notes.trim()}` : "unsure";
  return notes
    .replace(/(^|[\s,;])unsure(?=$|[\s,;])/gi, "$1")
    .replace(/^[\s,;]+|[\s,;]+$/g, "")
    .replace(/\s*([,;])\s*[,;]+\s*/g, "$1 ")
    .replace(/\s{2,}/g, " ");
}

/** Stable capture order for a burst: photo ids sort naturally (p_0002 before p_0010), never the agent's rank. */
export function byPhotoId<T extends { photo_id: string }>(a: T, b: T): number {
  return a.photo_id.localeCompare(b.photo_id, undefined, { numeric: true });
}

// Labelling-tool state and CSV export (docs/EVALUATION.md §1 columns, eval/labels/<eventId>.csv).

export const LABEL_COLUMNS = [
  "photo_id",
  "group_id_human",
  "verdict_human",
  "reasons_human",
  "best_of_group_human",
  "labeller",
  "labelled_at",
  "notes",
] as const;

export interface Label {
  photo_id: string;
  group_id_human: string;
  verdict_human?: "keep" | "reject";
  reasons_human: string[];
  best_of_group_human: boolean;
  labelled_at?: string;
  notes: string;
}

function cell(v: string): string {
  // RFC 4180 quoting, plus a leading apostrophe against spreadsheet formula injection.
  const safe = /^[=+\-@\t\r]/.test(v) ? `'${v}` : v;
  return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export function toCsv(labels: Label[], labeller: string): string {
  const rows = [LABEL_COLUMNS.join(",")];
  for (const l of labels) {
    if (!l.verdict_human) continue; // unlabelled frames are not exported
    rows.push(
      [
        l.photo_id,
        l.group_id_human,
        l.verdict_human,
        l.reasons_human.join(";"),
        l.best_of_group_human ? "1" : "0",
        labeller,
        l.labelled_at ?? "",
        l.notes,
      ]
        .map(cell)
        .join(","),
    );
  }
  return rows.join("\r\n") + "\r\n";
}

/** One best per group: marking a frame best clears the flag on the rest of its group. */
export function setBest(labels: Label[], photoId: string): Label[] {
  const target = labels.find((l) => l.photo_id === photoId);
  if (!target) return labels;
  const turnOn = !target.best_of_group_human;
  return labels.map((l) =>
    l.group_id_human !== target.group_id_human
      ? l
      : { ...l, best_of_group_human: l.photo_id === photoId ? turnOn : false },
  );
}

export function toggleReason(label: Label, code: string): Label {
  const has = label.reasons_human.includes(code);
  return { ...label, reasons_human: has ? label.reasons_human.filter((c) => c !== code) : [...label.reasons_human, code] };
}

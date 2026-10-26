// Fixed reason-code vocabulary (mirrors config/reason_codes.yaml).
// Each code keeps one colour everywhere in the UI. Codes group into families so the 14 codes stay
// legible: the family sets the hue, the label always carries the meaning (never colour alone).

export type ReasonFamily = "blur" | "eyes" | "exposure" | "duplicate" | "subject" | "keep" | "review";

export interface ReasonDef {
  code: string;
  label: string;
  family: ReasonFamily;
  /** 1–9 shortcut in the labelling tool (reasons_human column), when assigned. */
  key?: number;
  description: string;
}

export const REASONS: readonly ReasonDef[] = [
  { code: "BLUR_MOTION", label: "Motion blur", family: "blur", key: 1, description: "Subject or camera moved during exposure" },
  { code: "BLUR_FOCUS", label: "Missed focus", family: "blur", key: 2, description: "Focus plane missed the subject" },
  { code: "EYES_CLOSED", label: "Eyes closed", family: "eyes", key: 3, description: "Main subject's eyes closed" },
  { code: "EXPOSURE_UNDER", label: "Underexposed", family: "exposure", key: 4, description: "Shadows clipped / too dark" },
  { code: "EXPOSURE_OVER", label: "Overexposed", family: "exposure", key: 5, description: "Highlights clipped / too bright" },
  { code: "LOW_CONTRAST", label: "Low contrast", family: "exposure", key: 6, description: "Flat tonal range" },
  { code: "DUPLICATE_OF", label: "Duplicate", family: "duplicate", key: 7, description: "Near-duplicate of another frame" },
  { code: "BURST_NOT_BEST", label: "Not best in group", family: "duplicate", key: 8, description: "A better frame exists in this group" },
  { code: "NO_SUBJECT", label: "No subject", family: "subject", key: 9, description: "No clear subject in frame" },
  { code: "KEEP_BEST_OF_GROUP", label: "Best of group", family: "keep", description: "Chosen as the best frame of its group" },
  { code: "KEEP_SINGLETON", label: "Singleton keep", family: "keep", description: "Only frame of its moment and passes checks" },
  { code: "REVIEW_LOW_CONFIDENCE", label: "Low confidence", family: "review", description: "Agent unsure; a person should look" },
  { code: "REVIEW_POLICY_GROUP_EYES", label: "Group shot, one blink", family: "review", description: "One non-main face closed in a group photo" },
  { code: "REVIEW_ARTISTIC_INTENT", label: "Possible artistic blur", family: "review", description: "Motion blur that may be intentional" },
] as const;

// Family colours: ink on a light tint. Text stays dark (≥ 7:1 on its tint) so the hue identifies
// and the words carry the meaning.
export const FAMILY_STYLE: Record<ReasonFamily, { ink: string; tint: string; rule: string }> = {
  blur: { ink: "#3b2a6b", tint: "#ece8f7", rule: "#6b55b8" },
  eyes: { ink: "#5b1f43", tint: "#f7e6ef", rule: "#b4467f" },
  exposure: { ink: "#4d3a00", tint: "#f7efd6", rule: "#a87b00" },
  duplicate: { ink: "#173e5c", tint: "#e3eef7", rule: "#2f74ad" },
  subject: { ink: "#3a3f44", tint: "#eceeef", rule: "#7a838b" },
  keep: { ink: "#0f4426", tint: "#e2f2e8", rule: "#2b8a52" },
  review: { ink: "#5a3300", tint: "#fbe9d4", rule: "#b8660b" },
};

const BY_CODE = new Map(REASONS.map((r) => [r.code, r]));
const BY_KEY = new Map(REASONS.filter((r) => r.key).map((r) => [r.key as number, r]));

/** DUPLICATE_OF:<photo_id> → { def: DUPLICATE_OF, arg: photo_id } */
export function parseReason(raw: string): { def: ReasonDef; arg?: string } {
  const [base, arg] = raw.split(":", 2);
  const def = BY_CODE.get(base) ?? {
    code: base,
    label: base.toLowerCase().replace(/_/g, " "),
    family: "subject" as const,
    description: "Unrecognised reason code",
  };
  return { def, arg };
}

export function reasonByKey(key: number): ReasonDef | undefined {
  return BY_KEY.get(key);
}

export function isKnownReason(code: string): boolean {
  return BY_CODE.has(code.split(":", 1)[0]);
}

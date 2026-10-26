import type { Verdict } from "@/lib/types";

// Verdict marks drawn over a frame: a solid corner badge whose glyph carries the verdict (so it reads without
// colour) plus a thin inset ring in the verdict colour. Nothing covers the subject's face.
//   keep = check, review = pause bars (waiting on a person), reject = cross. Solid shapes so they read at 10px.

export const VERDICT_COLOR: Record<Verdict, string> = {
  keep: "var(--kf-keep)",
  review: "var(--kf-review)",
  reject: "var(--kf-reject)",
};

/** Badge fill on top of a photo. Rejects stay calm through dimming the photo and no ring, not a different colour. */
const BADGE_FILL: Record<Verdict, string> = VERDICT_COLOR;

export const VERDICT_WORD: Record<Verdict, string> = { keep: "Keep", reject: "Reject", review: "Review" };

export function VerdictGlyph({ verdict, className = "h-3 w-3" }: { verdict: Verdict; className?: string }) {
  const p = { fill: "none", stroke: "currentColor", strokeWidth: 2.4, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true">
      {verdict === "keep" && <path {...p} d="M3.5 8.4l3 3 6-6.6" />}
      {verdict === "reject" && <path {...p} d="M4.5 4.5l7 7M11.5 4.5l-7 7" />}
      {/* Review = "?": the frame waits on a person. Stroke weight matches the check and cross so it reads at 10px. */}
      {verdict === "review" && (
        <>
          <path {...p} strokeWidth={2.2} d="M5.6 5.9a2.45 2.45 0 1 1 3.55 2.2c-.75.4-1.15.85-1.15 1.75" />
          <circle cx="8" cy="12.6" r="1.25" fill="currentColor" />
        </>
      )}
    </svg>
  );
}

/** Corner badge + inset ring. `size` scales the badge; "lg" also prints the word. */
export function VerdictMark({ verdict, size = "md", ring = true, best = false }: { verdict?: Verdict; size?: "xs" | "sm" | "md" | "lg"; ring?: boolean; best?: boolean }) {
  if (!verdict) return null;
  if (best && verdict === "keep") return <PickMark size={size} ring={ring} />;
  const color = VERDICT_COLOR[verdict];
  // Only keep and review get the inset ring: the frames a photographer acts on.
  const showRing = ring && verdict !== "reject";
  const box = size === "lg" ? "h-7 gap-1.5 pl-1.5 pr-2.5 text-[0.78rem]" : size === "sm" ? "h-[22px] w-[22px] justify-center" : size === "xs" ? "h-4 w-4 justify-center" : "h-6 w-6 justify-center";
  const glyph = size === "lg" ? "h-3.5 w-3.5" : size === "sm" ? "h-3 w-3" : size === "xs" ? "h-2.5 w-2.5" : "h-3.5 w-3.5";
  const at = size === "xs" ? "top-1 left-1" : "top-1.5 left-1.5";
  return (
    <>
      {showRing && (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 rounded-[inherit]"
          style={{ boxShadow: `inset 0 0 0 2px ${color}` }}
        />
      )}
      <span
        aria-hidden="true"
        className={`pointer-events-none absolute ${at} inline-flex items-center rounded-full font-semibold text-white shadow-[0_0_0_1.5px_rgb(255_255_255/0.9),0_1px_3px_rgb(0_0_0/0.35)] ${box}`}
        style={{ background: BADGE_FILL[verdict] }}
      >
        <VerdictGlyph verdict={verdict} className={glyph} />
        {size === "lg" && VERDICT_WORD[verdict]}
      </span>
    </>
  );
}

/** The AI's pick: one marker (a keep check inside a gold-edged pill reading "AI pick"), replacing check + crown. */
function PickMark({ size, ring }: { size: "xs" | "sm" | "md" | "lg"; ring: boolean }) {
  const sm = size === "sm" || size === "xs";
  return (
    <>
      {ring && <span aria-hidden="true" className="pointer-events-none absolute inset-0 rounded-[inherit]" style={{ boxShadow: "inset 0 0 0 2px var(--kf-keep)" }} />}
      <span
        aria-hidden="true"
        className={`pointer-events-none absolute top-1.5 left-1.5 inline-flex items-center rounded-full font-semibold text-white shadow-[0_0_0_1.5px_rgb(255_255_255/0.9),0_1px_3px_rgb(0_0_0/0.35)] ${
          size === "xs" ? "h-4 w-4 justify-center" : sm ? "h-[22px] w-[22px] justify-center" : "h-6 gap-1 pr-2 pl-1.5 text-[0.6875rem] tracking-[0.01em]"
        }`}
        style={{ background: "var(--kf-keep)" }}
      >
        <VerdictGlyph verdict="keep" className={size === "xs" ? "h-2.5 w-2.5" : sm ? "h-3 w-3" : "h-3.5 w-3.5"} />
        {!sm && "AI pick"}
      </span>
    </>
  );
}

/** Best-pick crown, top-right. */
export function CrownBadge({ size = "md", label }: { size?: "sm" | "md" | "lg"; label?: string }) {
  const lg = size === "lg";
  return (
    <span
      aria-hidden="true"
      className={`pointer-events-none absolute top-1.5 right-1.5 inline-flex items-center gap-1 rounded-full bg-[#f6c343] font-semibold text-[#3b2a00] shadow-[0_0_0_1.5px_rgb(255_255_255/0.9),0_1px_3px_rgb(0_0_0/0.35)] ${
        lg ? "h-7 pr-2.5 pl-2 text-[0.78rem]" : size === "sm" ? "h-[22px] w-[22px] justify-center" : "h-6 w-6 justify-center"
      }`}
    >
      <CrownGlyph className={lg ? "h-3.5 w-3.5" : "h-3 w-3"} />
      {lg && (label ?? "Best pick")}
    </span>
  );
}

export function CrownGlyph({ className = "h-3 w-3" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true">
      <path d="M2 5.2l3.2 2.6L8 3l2.8 4.8L14 5.2l-1.3 7H3.3z" fill="currentColor" />
    </svg>
  );
}

export function VerdictTally({ counts, compact = false }: { counts?: Partial<Record<Verdict, number>>; compact?: boolean }) {
  const c = { keep: 0, reject: 0, review: 0, ...(counts ?? {}) };
  return (
    <dl className={`t-data flex items-center ${compact ? "gap-3 text-[0.8125rem]" : "gap-4 text-[0.875rem]"}`}>
      {(["keep", "review", "reject"] as const).map((v) => (
        <div key={v} className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="order-0 inline-flex h-4 w-4 items-center justify-center rounded-full text-white"
            style={{ background: VERDICT_COLOR[v] }}
          >
            <VerdictGlyph verdict={v} className="h-2.5 w-2.5" />
          </span>
          {/* dt before dd (a term then its value) for assistive tech; the number still reads first visually. */}
          <dt className="order-2 text-[var(--kf-muted)]">{v}</dt>
          <dd className="order-1 font-semibold">{c[v]}</dd>
        </div>
      ))}
    </dl>
  );
}

import { fmtNum } from "@/lib/format";
import { tally } from "@/lib/runs";
import type { VerdictCounts } from "@/lib/types";

const SEG = [
  { k: "keep", word: "Keep", color: "var(--kf-keep, #1d8a4e)" },
  { k: "review", word: "Review", color: "var(--kf-review, #b06800)" },
  { k: "reject", word: "Reject", color: "var(--kf-reject, #c8323f)" },
] as const;

/** Slim stacked keep/review/reject bar with a numeric legend. With no verdicts yet it shows an empty track
 *  and `emptyText` instead of a misleading "0 / 0 / 0". */
export function VerdictBar({
  counts,
  emptyText = "No verdicts yet",
  legend = "full",
  className = "",
}: {
  counts?: VerdictCounts;
  emptyText?: string;
  legend?: "full" | "numbers" | "none";
  className?: string;
}) {
  const t = tally(counts);
  const label = t.total
    ? `${t.keep} keep, ${t.review} review, ${t.reject} reject`
    : emptyText;
  return (
    <div className={`min-w-0 ${className}`}>
      <div
        role="img"
        aria-label={label}
        className="flex h-1.5 w-full gap-px overflow-hidden rounded-full bg-[var(--kf-surface-2,#eef1f4)]"
      >
        {t.total > 0 &&
          SEG.map((s) =>
            t[s.k] > 0 ? <span key={s.k} style={{ width: `${(t[s.k] / t.total) * 100}%`, background: s.color }} /> : null,
          )}
      </div>
      {legend !== "none" && (
        <div aria-hidden="true" className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.75rem] text-[var(--kf-muted,#5a646d)]">
          {t.total === 0 ? (
            <span>{emptyText}</span>
          ) : (
            SEG.filter((s) => legend === "full" || t[s.k] > 0).map((s) => (
              <span key={s.k} className="inline-flex items-center gap-1.5 whitespace-nowrap">
                <span className="h-2 w-2 rounded-[2px]" style={{ background: s.color }} />
                <span className="tabular-nums font-semibold text-[var(--kf-text,#1e2327)]">{fmtNum(t[s.k])}</span>
                {legend === "full" && <span>{s.word.toLowerCase()}</span>}
              </span>
            ))
          )}
        </div>
      )}
    </div>
  );
}

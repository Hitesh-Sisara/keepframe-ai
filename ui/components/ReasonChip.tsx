import { FAMILY_STYLE, parseReason } from "@/lib/reasons";

export function ReasonChip({ code, count, title }: { code: string; count?: number; title?: string }) {
  const { def, arg } = parseReason(code);
  const s = FAMILY_STYLE[def.family];
  return (
    <span
      className="inline-flex h-6 items-center gap-1.5 rounded-full pr-2.5 pl-2 text-[0.75rem] font-medium whitespace-nowrap"
      style={{ background: s.tint, color: s.ink }}
      title={title ?? `${def.code}: ${def.description}`}
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full" style={{ background: s.rule }} />
      <span>{def.label}</span>
      {arg && <span className="t-data opacity-75">{arg}</span>}
      {count !== undefined && <span className="t-data font-semibold opacity-80">{count}</span>}
    </span>
  );
}

export function ReasonChips({ codes, counts }: { codes?: string[]; counts?: Record<string, number> }) {
  const entries: [string, number | undefined][] = counts
    ? Object.entries(counts)
    : (codes ?? []).map((c) => [c, undefined]);
  if (!entries.length) return null;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {entries.map(([c, n]) => (
        <li key={c}>
          <ReasonChip code={c} count={n} />
        </li>
      ))}
    </ul>
  );
}

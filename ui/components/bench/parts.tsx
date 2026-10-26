"use client";

import { fmtMs, fmtSigned, pctChange, processorName, stageLabel } from "@/components/bench/derive";
import { Glossed } from "@/components/Glossed";
import { fmtNum, fmtPct1 } from "@/lib/format";
import { baselineOf, compilerOf, type HostSeries, stageValue } from "@/lib/hosts";

/* ---------- Stat tile (dataviz figure contract: label · value · delta · foot) ---------- */

export function StatTile({
  label,
  value,
  unit,
  delta,
  foot,
  accent,
}: {
  label: string;
  value: string;
  unit?: string;
  delta?: { text: string; good: boolean; neutral?: boolean };
  foot?: string;
  accent?: boolean;
}) {
  return (
    <div className={`kf-card relative flex flex-col overflow-hidden px-5 pt-4 pb-4 ${accent ? "ring-1 ring-(--p-accent)/25" : ""}`}>
      {accent && <span aria-hidden="true" className="absolute inset-x-0 top-0 h-[3px] bg-(--p-accent)" />}
      <p className="text-[0.875rem] font-medium text-(--p-muted)">{label}</p>
      <p className="mt-2 flex items-baseline gap-2">
        <span className="text-[2.5rem] leading-none font-semibold tracking-[-0.03em]">{value}</span>
        {unit && <span className="text-[0.9375rem] text-(--p-muted)">{unit}</span>}
      </p>
      {delta && (
        <p
          className={`mt-3 inline-flex w-fit items-center gap-1 rounded-full px-2 py-0.5 text-[0.8125rem] font-semibold ${
            delta.neutral ? "bg-(--p-surface-2) text-(--p-muted) ring-1 ring-(--p-border) ring-inset" : delta.good ? "bg-(--p-keep-soft) text-(--p-keep)" : "bg-(--p-reject-soft) text-(--p-reject)"
          }`}
        >
          {!delta.neutral && <Arrow up={delta.text.startsWith("+")} />}
          {delta.text}
        </p>
      )}
      {foot && <p className="mt-auto pt-3 text-[0.8125rem] text-(--p-muted)">{foot}</p>}
    </div>
  );
}

function Arrow({ up }: { up: boolean }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 12 12" className={`h-3 w-3 ${up ? "" : "rotate-180"}`}>
      <path d="M6 2.5 L10 7.5 H2 Z" fill="currentColor" />
    </svg>
  );
}

/** KleidiCV on vs off. When the file has no KleidiCV host, say so plainly: never a placeholder number. */
export function KleidiTile({ result }: { result?: { off: HostSeries; on: HostSeries; controlled: boolean; compiler?: string } }) {
  if (!result) {
    return (
      <div className="kf-card flex flex-col px-5 pt-4 pb-4">
        <p className="text-[0.875rem] font-medium text-(--p-muted)">KleidiCV vs carotene</p>
        <p className="mt-2 text-[1.75rem] leading-[2.5rem] font-semibold tracking-[-0.02em]">Not measured</p>
        <p className="mt-auto pt-3 text-[0.8125rem] text-(--p-muted)">No KleidiCV host in this run</p>
      </div>
    );
  }
  const a = result.off.host.images_per_sec;
  const b = result.on.host.images_per_sec;
  const change = pctChange(b, a);
  return (
    <div className="kf-card flex flex-col px-5 pt-4 pb-4">
      <p className="text-[0.875rem] font-medium text-(--p-muted)">KleidiCV vs carotene</p>
      <p className="mt-2 flex items-baseline gap-2">
        <span className="text-[2.5rem] leading-none font-semibold tracking-[-0.03em]">
          {typeof a === "number" && typeof b === "number" && a > 0 ? `${(b / a).toFixed(3)}×` : "n/a"}
        </span>
        <span className="text-[0.9375rem] text-(--p-muted)">throughput</span>
      </p>
      {change !== undefined && (
        <p
          className={`mt-3 inline-flex w-fit items-center gap-1 rounded-full px-2 py-0.5 text-[0.8125rem] font-semibold ${
            Math.abs(change) < 0.5 ? "bg-(--p-surface-2) text-(--p-muted)" : change > 0 ? "bg-(--p-keep-soft) text-(--p-keep)" : "bg-(--p-reject-soft) text-(--p-reject)"
          }`}
        >
          {fmtSigned(change)} with KleidiCV
        </p>
      )}
      <p className="mt-auto pt-3 text-[0.8125rem] text-(--p-muted)">
        <span title={result.controlled ? `Same source and compiler${result.compiler ? ` (${result.compiler})` : ""}, only the HAL differs` : "PyPI wheels, different compilers: not a pure KleidiCV effect"}>
          {fmtNum(b, 1)} vs {fmtNum(a, 1)} images/s{result.controlled ? "" : " · wheels"}
        </span>
      </p>
    </div>
  );
}

/* ---------- Segmented control ---------- */

export function Segmented<T extends string>({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-[8px] bg-(--p-surface-2) p-[3px] ring-1 ring-(--p-border)">
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o.value)}
            className={`kf-focus rounded-[6px] px-3 py-1 text-[0.875rem] font-medium transition-colors ${
              on ? "bg-(--p-surface) text-(--p-text) shadow-sm ring-1 ring-(--p-border)" : "text-(--p-muted) hover:text-(--p-text)"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/* ---------- Instance / config spec table ---------- */

function halText(s: HostSeries): string {
  if (s.hal === "kleidicv") return "KleidiCV + carotene";
  if (s.hal === "carotene") return "carotene";
  return s.host.opencv_hal ?? "Not reported";
}

export function SpecTable({ series }: { series: HostSeries[] }) {
  // Throughput, cost, processor and image count are in the headline tiles and column heads, so not repeated here.
  const allRows: { label: string; cell: (s: HostSeries) => React.ReactNode; num?: boolean; measured?: boolean }[] = [
    { label: "Instance", cell: (s) => s.host.instance_type ?? "n/a" },
    { label: "OpenCV", cell: (s) => s.host.opencv_version ?? "n/a", num: true },
    { label: "Build", cell: (s) => (s.build === "src" ? `Source build${compilerOf(s.host) ? `, ${compilerOf(s.host)}` : ""}` : "PyPI wheel") },
    { label: "HAL", cell: halText },
    { label: "Workers", cell: (s) => fmtNum(s.host.workers, 0), num: true },
    { label: "CPU (mean)", cell: (s) => `${fmtPct1(s.host.cpu_util_mean)}%`, num: true, measured: true },
    { label: "Peak memory", cell: (s) => (typeof s.host.peak_rss_mb === "number" ? `${fmtNum(s.host.peak_rss_mb, 0)} MB` : "n/a"), num: true, measured: true },
  ];
  const rows = allRows.filter((r) => !series.every((s) => ["Not reported", "n/a"].includes(String(r.cell(s)))));
  // Rows where every host has the same value are shown once, under "Same on every host".
  const text = (n: React.ReactNode) => (typeof n === "string" || typeof n === "number" ? String(n) : null);
  const shared = series.length > 1 ? rows.filter((r) => { if (r.measured) return false; const v = series.map((s) => text(r.cell(s))); return v[0] !== null && v.every((x) => x === v[0]); }) : [];
  const differing = rows.filter((r) => !shared.includes(r));
  return (
    <section aria-labelledby="spec-h" className="kf-card self-start overflow-hidden">
      <h2 id="spec-h" className="px-6 pt-5 pb-3 text-[1.25rem] font-semibold tracking-[-0.01em]">
        Setup
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-[0.875rem] sm:text-[0.9375rem]">
          <thead>
            <tr className="border-y border-(--p-border) bg-(--p-surface-2)">
              <th scope="col" className="w-[38%] px-3 py-2.5 text-left sm:px-6">
                <span className="sr-only">Property</span>
              </th>
              {series.map((s) => (
                <th key={s.key} scope="col" className="px-3 py-2.5 sm:px-6 text-left font-semibold">
                  <span className="flex items-center gap-2">
                    <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-[3px]" style={{ background: s.color }} />
                    {processorName(s.host.instance_type) ?? s.key}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {differing.map((r) => (
              <tr key={r.label} className="border-b border-(--p-border) last:border-0">
                <th scope="row" className="px-3 py-2.5 sm:px-6 text-left font-normal text-(--p-muted)">
                  {r.label}
                </th>
                {series.map((s) => (
                  <td key={s.key} className={`px-3 py-2.5 sm:px-6 ${r.num ? "kf-num" : ""}`}>
                    {r.cell(s)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          {shared.length > 0 && (
            <tbody>
              <tr className="border-y border-(--p-border) bg-(--p-surface-2)">
                <th scope="colgroup" colSpan={series.length + 1} className="px-3 py-2 text-left sm:px-6 text-[0.8125rem] font-medium text-(--p-muted)">
                  All hosts
                </th>
              </tr>
              {shared.map((r) => (
                <tr key={r.label} className="border-b border-(--p-border) last:border-0">
                  <th scope="row" className="px-3 py-2.5 sm:px-6 text-left font-normal text-(--p-muted)">
                    {r.label}
                  </th>
                  <td colSpan={series.length} className={`px-3 py-2.5 sm:px-6 ${r.num ? "kf-num" : ""}`}>
                    {r.cell(series[0])}
                  </td>
                </tr>
              ))}
            </tbody>
          )}
        </table>
      </div>
    </section>
  );
}

/* ---------- Methods ---------- */

export function MethodsNote({ nImages }: { nImages?: number }) {
  return (
    <details className="kf-card group self-start px-6 py-4">
      <summary className="kf-focus cursor-pointer text-[0.9375rem] font-semibold marker:text-(--p-muted)">Method</summary>
      <p className="mt-2 text-[0.875rem] leading-relaxed text-(--p-muted)">
        Same pipeline and {nImages ? `${fmtNum(nImages, 0)}-image ` : ""}set on every host, one OpenCV thread per worker; stage times per image (median, p95).
        Throughput = images ÷ wall-clock; cost = on-demand price × wall-clock.
      </p>
    </details>
  );
}

/* ---------- Table twin of the chart ---------- */

export function BenchTable({ series, stages }: { series: HostSeries[]; stages: string[] }) {
  const base = baselineOf(series);
  const others = series.filter((s) => s !== base);
  const short = (s: HostSeries) => s.host.instance_type?.split(".")[0] ?? s.key;
  return (
    <div className="overflow-x-auto">
      <table className="kf-num w-full min-w-[640px] text-right text-[0.9375rem]">
        <caption className="sr-only">Per-stage time in milliseconds per image for each host</caption>
        <thead>
          <tr className="border-b border-(--p-border-strong)">
            <th scope="col" className="kf-eyebrow py-2.5 pr-4 text-left">Stage</th>
            {series.map((s) => (
              <th key={`${s.key}-50`} scope="col" className="kf-eyebrow px-4 py-2.5 text-right">
                {short(s)} p50
              </th>
            ))}
            {series.map((s) => (
              <th key={`${s.key}-95`} scope="col" className="kf-eyebrow px-4 py-2.5 text-right">
                {short(s)} p95
              </th>
            ))}
            {base &&
              others.map((s) => (
                <th key={`${s.key}-x`} scope="col" className="kf-eyebrow py-2.5 pl-4 text-right">
                  {short(s)} ÷ {short(base)}
                </th>
              ))}
          </tr>
        </thead>
        <tbody>
          {stages.map((st) => {
            const b = base ? stageValue(base, st, "p50_ms") : undefined;
            return (
              <tr key={st} className="border-b border-(--p-border) last:border-0">
                <th scope="row" className="py-2.5 pr-4 text-left font-medium">
                  <Glossed text={stageLabel(st)} />
                </th>
                {series.map((s) => {
                  const v = stageValue(s, st, "p50_ms");
                  return <td key={`${s.key}-50`} className="px-4 py-2.5">{v === undefined ? "n/a" : fmtMs(v)}</td>;
                })}
                {series.map((s) => {
                  const v = stageValue(s, st, "p95_ms");
                  return <td key={`${s.key}-95`} className="px-4 py-2.5 text-(--p-muted)">{v === undefined ? "n/a" : fmtMs(v)}</td>;
                })}
                {base &&
                  others.map((s) => {
                    const v = stageValue(s, st, "p50_ms");
                    return (
                      <td key={`${s.key}-x`} className="py-2.5 pl-4 font-semibold">
                        {b && v ? `${(v / b).toFixed(2)}×` : "n/a"}
                      </td>
                    );
                  })}
              </tr>
            );
          })}
        </tbody>
      </table>
      {base && others.length > 0 && (
        <p className="mt-3 text-[0.8125rem] text-(--p-muted)">
          ms per image. Ratio = median ÷ {short(base)}; above 1× means {short(base)} is faster.
        </p>
      )}
    </div>
  );
}

/* ---------- Loading skeleton ---------- */

export function BenchSkeleton() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading benchmark results</span>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="kf-card px-5 py-4">
            <div className="kf-skel h-4 w-32" />
            <div className="kf-skel mt-3 h-10 w-28" />
            <div className="kf-skel mt-4 h-3 w-40" />
          </div>
        ))}
      </div>
      <div className="kf-card mt-6 px-8 py-6">
        <div className="kf-skel h-5 w-48" />
        <div className="mt-6 space-y-4">
          {[80, 40, 10, 20, 30, 10, 95, 60].map((w, i) => (
            <div key={i} className="flex items-center gap-6">
              <div className="kf-skel h-4 w-44" />
              <div className="kf-skel h-6" style={{ width: `${w * 0.6}%` }} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

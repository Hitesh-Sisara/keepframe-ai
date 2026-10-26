"use client";

import { useId, useState } from "react";
import { Glossed } from "@/components/Glossed";
import { fmtMs, shortHost, stageLabel } from "@/components/bench/derive";
import { baselineOf, type HostSeries, stageValue } from "@/lib/hosts";

// Grouped horizontal bars: one row per pipeline stage, one bar per host, one linear ms axis shared by all rows.
// Built in HTML (not a scaled SVG) so text keeps its pixel size at every width. Dataviz mark spec: bars ≤ 24 px
// (here 14 px), 4 px rounded data-end and square at the baseline, a 2 px surface gap between a row's bars,
// hairline solid gridlines, each bar's value at its tip in text ink with a surface halo (never the series
// colour), a legend always present (rendered by the page), a per-row hover/focus tooltip, and a table twin.

const BAR = 14;

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 9, 10]) if (m * p >= v * 1.03) return m * p;
  return 10 * p;
}

function tickValues(max: number): number[] {
  const p = 10 ** Math.floor(Math.log10(max));
  const step = [p / 5, p / 4, p / 2, p, p * 2].find((s) => max / s <= 8 && Number.isInteger(+(max / s).toFixed(6))) ?? max / 4;
  const out: number[] = [];
  for (let t = 0; t <= max + 1e-9; t += step) out.push(+t.toFixed(6));
  return out;
}

export function StageChart({
  stages,
  series,
  metric,
  caption,
}: {
  stages: string[];
  series: HostSeries[];
  metric: "p50_ms" | "p95_ms";
  caption: string;
}) {
  const id = useId();
  const [hover, setHover] = useState<string | null>(null);
  const base = baselineOf(series);
  const others = series.filter((s) => s !== base);
  const all = stages.flatMap((st) => series.map((s) => stageValue(s, st, metric) ?? 0));
  const max = niceMax(Math.max(...all, 0));
  const ticks = tickValues(max);
  // The bar track ends 4.5rem short of the plot so the tip label always fits; ticks share that same width.
  const pct = (v: number) => `${(v / max) * 100}%`;
  const vs = base && others.length === 1 ? others[0] : undefined;
  const grid = vs ? "kf-stagegrid kf-stagegrid-3" : "kf-stagegrid";

  return (
    <figure aria-labelledby={`${id}-cap`} className="min-w-0">
      <figcaption id={`${id}-cap`} className="sr-only">
        {caption}
      </figcaption>

      <div className={`${grid} pb-2`}>
        <div className="kf-eyebrow [grid-area:name]">Stage</div>
        <div className="kf-eyebrow kf-hide-narrow [grid-area:bars]">ms / image</div>
        {vs && (
          <div className="kf-eyebrow text-right whitespace-nowrap [grid-area:ratio]" title={`${shortHost(base!, series)} vs ${shortHost(vs, series)}: ${shortHost(vs, series)} time ÷ ${shortHost(base!, series)} time`}>
            Speedup
          </div>
        )}
      </div>

      <ol className="border-t border-(--p-border-strong)">
        {stages.map((stage) => {
          // Ratio from the measured (unrounded) times; the bar labels are rounded for reading.
          const b = base ? stageValue(base, stage, metric) : undefined;
          const v = vs ? stageValue(vs, stage, metric) : undefined;
          const r = b && v ? v / b : undefined; // other ÷ baseline time; > 1 means the baseline host is faster
          const active = hover === stage;
          return (
            <li
              key={stage}
              tabIndex={0}
              aria-label={`${stageLabel(stage)}: ${series
                .map((s) => {
                  const x = stageValue(s, stage, metric);
                  return `${s.label} ${x === undefined ? "not measured" : `${fmtMs(x)} ms`}`;
                })
                .join("; ")}${r ? `. ${ratioWords(r, base!, vs!, series)}` : ""}`}
              onPointerEnter={() => setHover(stage)}
              onPointerLeave={() => setHover((h) => (h === stage ? null : h))}
              onFocus={() => setHover(stage)}
              onBlur={() => setHover(null)}
              className={`kf-focus relative ${grid} items-center border-b border-(--p-border) py-2.5 outline-none transition-colors ${
                active ? "bg-(--p-surface-2) [--p-halo:var(--p-surface-2)]" : ""
              }`}
            >
              <div className="pl-1 text-[0.9375rem] font-medium [grid-area:name]"><Glossed text={stageLabel(stage)} /></div>
              <div className="relative [grid-area:bars]">
                <div aria-hidden="true" className="pointer-events-none absolute inset-y-[-10px] right-[4.5rem] left-0">
                  {ticks.map((t) => (
                    <span key={t} className="absolute inset-y-0 w-px bg-(--p-border)" style={{ left: pct(t) }} />
                  ))}
                </div>
                <div className="relative mr-[4.5rem] flex flex-col gap-[2px]">
                  {series.map((s) => {
                    const x = stageValue(s, stage, metric);
                    return (
                      <div key={s.key} className="relative" style={{ height: BAR }}>
                        <span
                          aria-hidden="true"
                          className="absolute inset-y-0 left-0 rounded-r-[4px] transition-opacity"
                          style={{ width: x === undefined ? 0 : `max(3px, ${pct(x)})`, background: s.color, opacity: hover && !active ? 0.35 : 1 }}
                        />
                        <span
                          aria-hidden="true"
                          className="kf-num absolute top-1/2 z-10 ml-1.5 -translate-y-1/2 rounded-[3px] bg-(--p-halo,var(--p-surface)) px-1 py-[1px] text-[0.8125rem] leading-none font-medium whitespace-nowrap text-(--p-text)"
                          style={{ left: x === undefined ? 0 : `max(3px, ${pct(x)})` }}
                        >
                          {x === undefined ? "n/a" : fmtMs(x)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
              {vs && (
                <div className="kf-num pr-1 text-right text-[0.9375rem] [grid-area:ratio]">
                  {r === undefined ? <span className="text-(--p-muted)">n/a</span> : <RatioText r={r} />}
                </div>
              )}
              {active && <RowTooltip stage={stage} series={series} metric={metric} />}
            </li>
          );
        })}
      </ol>

      <div aria-hidden="true" className={grid}>
        <div className="relative mr-[4.5rem] h-7 [grid-area:bars]">
          {ticks.map((t, i) => (
            <span
              key={t}
              className={`kf-num absolute top-2 text-[0.8125rem] text-(--p-muted) ${i === 0 ? "" : "-translate-x-1/2"}`}
              style={{ left: pct(t) }}
            >
              {+t.toFixed(2)}
            </span>
          ))}
        </div>
      </div>
    </figure>
  );
}

/** Half-up to 2 decimals, immune to float artefacts (66.6 / 59.2 = 1.1249999… must read 1.13). */
function round2(x: number): string {
  return (Math.round(x * 100 + 1e-7) / 100).toFixed(2);
}

function ratioWords(r: number, base: HostSeries, vs: HostSeries, series: HostSeries[]): string {
  if (Math.abs(r - 1) < 0.005) return "Same time on both";
  return r > 1
    ? `${shortHost(base, series)} is ${round2(r)} times faster`
    : `${shortHost(base, series)} is ${round2(1 / r)} times slower`;
}

/** Neutral "1.15×" (the column says speedup); only a regression is coloured, and it carries the word, not just the colour. */
function RatioText({ r }: { r: number }) {
  if (Math.abs(r - 1) < 0.005) return <span className="text-(--p-muted)">same</span>;
  return r > 1 ? (
    <span>
      <span className="font-semibold">{round2(r)}×</span>
    </span>
  ) : (
    <span className="text-(--p-review)">
      <span className="font-semibold">{round2(1 / r)}×</span> slower
    </span>
  );
}

function RowTooltip({ stage, series, metric }: { stage: string; series: HostSeries[]; metric: "p50_ms" | "p95_ms" }) {
  const base = baselineOf(series);
  const baseV = base ? stageValue(base, stage, metric) : undefined;
  return (
    <div
      aria-hidden="true"
      className="kf-card pointer-events-none absolute top-[calc(100%+4px)] right-[8.5rem] z-20 w-[300px] px-3.5 py-2.5 text-[0.875rem] shadow-lg"
    >
      <p className="mb-1.5 font-semibold">{stageLabel(stage)}</p>
      <p className="mb-1 flex gap-2 text-[0.75rem] text-(--p-muted)">
        <span className="flex-1" />
        <span className="w-[7.5rem] text-right">p50 / p95 ms</span>
        <span className="w-12 text-right">ratio</span>
      </p>
      {series.map((s) => {
        const v = stageValue(s, stage, metric);
        const p50 = stageValue(s, stage, "p50_ms");
        const p95 = stageValue(s, stage, "p95_ms");
        return (
          <p key={s.key} className="flex items-center gap-2 py-0.5">
            <span aria-hidden="true" className="inline-block h-2.5 w-2.5 shrink-0 rounded-[3px]" style={{ background: s.color }} />
            <span className="min-w-0 flex-1 truncate">{shortHost(s, series)}</span>
            <span className="kf-num w-[7.5rem] text-right">
              <strong className="font-semibold">{p50 === undefined ? "n/a" : fmtMs(p50)}</strong>
              <span className="text-(--p-muted)"> / {p95 === undefined ? "n/a" : fmtMs(p95)}</span>
            </span>
            <span className="kf-num w-12 text-right text-(--p-muted)">{s !== base && baseV && v ? `${(v / baseV).toFixed(2)}×` : ""}</span>
          </p>
        );
      })}
    </div>
  );
}

export function Legend({ series }: { series: HostSeries[] }) {
  return (
    <ul className="flex flex-wrap gap-x-6 gap-y-2 text-[0.9375rem]" aria-label="Legend">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-2">
          <span aria-hidden="true" className="inline-block h-3 w-3 rounded-[3px]" style={{ background: s.color }} />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

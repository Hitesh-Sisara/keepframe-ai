"use client";

import "@/components/bench/tokens.css";
import { useMemo, useState } from "react";
import { BenchSkeleton, KleidiTile, MethodsNote, SpecTable, StatTile, Segmented, BenchTable } from "@/components/bench/parts";
import { CostCalculator } from "@/components/bench/Story";
import { fmtMeasured, fmtSigned, generationPair, isTotalStage, kleidiResult, pctChange, processorName, shortHost, withDisplayNames } from "@/components/bench/derive";
import { Legend, StageChart } from "@/components/StageChart";
import { ErrorNote } from "@/components/States";
import { PageHeader } from "@/components/ui";
import { useApi } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { hostSeries, sourcePair, stageNames, stageValue } from "@/lib/hosts";
import type { BenchDoc } from "@/lib/types";

export default function BenchPage() {
  const bench = useApi<BenchDoc>("/bench");
  const [view, setView] = useState<"chart" | "table">("chart");
  const [metric, setMetric] = useState<"p50_ms" | "p95_ms">("p50_ms");
  const doc = bench.data;
  const series = useMemo(() => withDisplayNames(hostSeries(doc)), [doc]);
  const allStages = useMemo(() => stageNames(series), [series]);
  const stages = allStages.filter((s) => !isTotalStage(s));
  const total = allStages.find(isTotalStage);
  const gen = useMemo(() => generationPair(series), [series]);
  const kleidi = useMemo(() => kleidiResult(series, sourcePair(series)), [series]);

  // Deltas use the values as displayed in the spec table (2 decimals), so a reader can redo the arithmetic.
  const shown = (v: number | undefined, d: number) => (typeof v === "number" ? +v.toFixed(d) : undefined);
  const ipsGain = gen ? pctChange(shown(gen.newer.host.images_per_sec, 2), shown(gen.older.host.images_per_sec, 2)) : undefined;
  const costChange = gen
    ? pctChange(shown(perMillionNum(gen.newer.host.cost_per_1k_usd), 2), shown(perMillionNum(gen.older.host.cost_per_1k_usd), 2))
    : undefined;
  const nImages = doc?.n_images ?? series[0]?.host.n_images;

  return (
    <div className="kf-page text-(--p-text)">
      <PageHeader
        title={
          gen
            ? `OpenCV 5 on ${processorName(gen.newer.host.instance_type)} vs ${processorName(gen.older.host.instance_type)}`
            : "OpenCV 5 on AWS Graviton"
        }
        subtitle={nImages ? `${fmtNum(nImages, 0)} images` : undefined}
        actions={
          doc?.generated_at ? (
            <p className="kf-num text-[0.8125rem] text-(--p-muted)">
              Measured <time dateTime={doc.generated_at}>{fmtMeasured(doc.generated_at)}</time>
            </p>
          ) : undefined
        }
      />

      <ErrorNote error={bench.error} retry={bench.reload} />
      {!doc && !bench.error && <BenchSkeleton />}

      {doc && series.length === 0 && (
        <div className="kf-card px-6 py-10 text-center">
          <p className="font-semibold">No results yet</p>
          <p className="mt-1 text-(--p-muted)">Run a benchmark first.</p>
        </div>
      )}

      {doc && series.length > 0 && (
        <>
          {doc.synthetic && (
            <p role="note" className="mb-6 rounded-(--p-radius) border border-(--p-review)/30 bg-(--p-review-soft) px-4 py-3 text-(--p-review)">
              <strong className="font-semibold">Placeholder numbers</strong> (synthetic fixture).
            </p>
          )}

          <section aria-label="Headline results" className={`grid gap-4 md:grid-cols-2 ${kleidi ? "xl:grid-cols-4" : "xl:grid-cols-3"}`}>
            {gen ? (
              <>
                <StatTile
                  label={processorName(gen.newer.host.instance_type) ?? "Newer"}
                  value={fmtNum(gen.newer.host.images_per_sec, 1)}
                  unit="images/s"
                  delta={ipsGain !== undefined ? { text: fmtSigned(ipsGain), good: ipsGain >= 0 } : undefined}
                />
                <StatTile
                  label={processorName(gen.older.host.instance_type) ?? "Older"}
                  value={fmtNum(gen.older.host.images_per_sec, 1)}
                  unit="images/s"
                  delta={{ text: "Baseline", good: true, neutral: true }}
                />
                <StatTile
                  label={`Cost, ${processorName(gen.newer.host.instance_type)}`}
                  value={perMillion(gen.newer.host.cost_per_1k_usd)}
                  unit="/ 1M images"
                  delta={
                    costChange !== undefined
                      ? { text: fmtSigned(costChange), good: costChange <= 0 }
                      : undefined
                  }
                />
              </>
            ) : (
              series.slice(0, 3).map((s) => (
                <StatTile
                  key={s.key}
                  label={s.label}
                  value={fmtNum(s.host.images_per_sec, 1)}
                  unit="images/s"
                  foot={`${perMillion(s.host.cost_per_1k_usd)} / 1M images`}
                />
              ))
            )}
            {kleidi && <KleidiTile result={kleidi} />}
          </section>

          <section aria-labelledby="stages-h" className="kf-card mt-6 px-4 pt-5 pb-4 sm:px-6 lg:px-8">
            <div className="mb-5 flex flex-wrap items-center justify-between gap-4">
              <div>
                <h2 id="stages-h" className="text-[1.25rem] font-semibold tracking-[-0.01em]">
                  Stage times
                </h2>
                {total && (
                  <p className="kf-num mt-0.5 text-[0.875rem] text-(--p-muted)">
                    Total {series.map((s) => `${fmtNum(stageValue(s, total, metric), 1)} ms ${shortHost(s, series)}`).join(" · ")}
                  </p>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
                <Segmented
                  label="Statistic"
                  value={metric}
                  onChange={setMetric}
                  options={[
                    { value: "p50_ms", label: "Median" },
                    { value: "p95_ms", label: "p95" },
                  ]}
                />
                <Segmented
                  label="View"
                  value={view}
                  onChange={setView}
                  options={[
                    { value: "chart", label: "Chart" },
                    { value: "table", label: "Table" },
                  ]}
                />
              </div>
            </div>
            <div className="mb-5">
              <Legend series={series} />
            </div>
            {view === "chart" ? (
              <StageChart
                stages={stages}
                series={series}
                metric={metric}
                caption={`${metric === "p50_ms" ? "Median" : "95th percentile"} time per image for each pipeline stage, one bar per host`}
              />
            ) : (
              <BenchTable series={series} stages={allStages} />
            )}
          </section>

          <div className="mt-6">
            <CostCalculator series={series} />
          </div>

          <div className="mt-6 grid grid-cols-1 gap-6 [&>*]:min-w-0">
            <SpecTable series={series} />
            <MethodsNote nImages={nImages} />
          </div>
        </>
      )}
    </div>
  );
}

/** Cost per 1,000 images × 1,000, so the difference shows in the first digits instead of the fifth decimal. */
function perMillionNum(per1k?: number): number | undefined {
  return typeof per1k === "number" ? per1k * 1000 : undefined;
}

function perMillion(per1k?: number): string {
  return typeof per1k === "number" ? `$${(per1k * 1000).toFixed(2)}` : "n/a";
}

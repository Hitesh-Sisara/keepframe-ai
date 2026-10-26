"use client";

import { useId, useState } from "react";
import { approxDuration, eventCost, eventSeconds, fmtUsd, processorName } from "@/components/bench/derive";
import { fmtNum } from "@/lib/format";
import type { HostSeries } from "@/lib/hosts";

const EXAMPLE = 2000;

const STEPS = [100, 250, 500, 1000, 2000, 3000, 5000, 7500, 10000, 15000, 20000, 30000, 50000];

/** Photos per event → compute time and cost, at the measured rate of each host. */
export function CostCalculator({ series }: { series: HostSeries[] }) {
  const id = useId();
  const [i, setI] = useState(STEPS.indexOf(EXAMPLE));
  const photos = STEPS[i];
  const hosts = series.filter((s) => typeof s.host.images_per_sec === "number" && typeof s.host.cost_per_1k_usd === "number");
  if (!hosts.length) return null;
  const maxCost = Math.max(...hosts.map((h) => eventCost(photos, h.host.cost_per_1k_usd) ?? 0));

  return (
    <section aria-labelledby={`${id}-h`} className="kf-card px-6 py-6 lg:px-8">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={`${id}-h`} className="text-[1.25rem] font-semibold tracking-[-0.01em]">
          Cost per event
        </h2>
      </div>

      <div className="mt-5 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] lg:items-center">
        <div>
          <label htmlFor={`${id}-r`} className="flex items-baseline justify-between text-[0.875rem] font-medium">
            Photos per event
            <output htmlFor={`${id}-r`} className="kf-num text-[1.75rem] leading-none font-semibold tracking-[-0.02em]">
              {fmtNum(photos, 0)}
            </output>
          </label>
          <input
            id={`${id}-r`}
            type="range"
            min={0}
            max={STEPS.length - 1}
            step={1}
            value={i}
            onChange={(e) => setI(Number(e.target.value))}
            aria-valuetext={`${photos.toLocaleString("en-US")} photos`}
            className="kf-range mt-4 w-full"
          />
          <div className="mt-1.5 flex justify-between text-[0.75rem] text-(--p-muted)" aria-hidden="true">
            <span>100</span>
            <span>50,000</span>
          </div>
        </div>

        <ul className="grid gap-3">
          {hosts.map((h) => {
            const c = eventCost(photos, h.host.cost_per_1k_usd) ?? 0;
            const t = eventSeconds(photos, h.host.images_per_sec) ?? 0;
            return (
              <li key={h.key} className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_auto] items-center gap-3">
                <span className="flex min-w-0 items-center gap-2 text-[0.875rem]">
                  <span aria-hidden="true" className="h-2.5 w-2.5 shrink-0 rounded-[3px]" style={{ background: h.color }} />
                  <span className="truncate">{processorName(h.host.instance_type) ?? h.label}</span>
                </span>
                <span aria-hidden="true" className="h-2 overflow-hidden rounded-full bg-(--p-surface-2)">
                  <span className="block h-full rounded-full transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${maxCost ? (c / maxCost) * 100 : 0}%`, background: h.color }} />
                </span>
                <span className="kf-num text-right text-[0.875rem]">
                  <span className="font-semibold">{fmtUsd(c)}</span>
                  <span className="text-(--p-muted)"> · {approxDuration(t)}</span>
                </span>
              </li>
            );
          })}
        </ul>
      </div>
      <p className="mt-4 text-[0.8125rem] text-(--p-muted)">Compute only, one instance</p>
    </section>
  );
}

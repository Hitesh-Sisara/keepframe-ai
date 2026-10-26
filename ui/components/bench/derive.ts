// Display-only derivations for the benchmark page. Every number shown comes from GET /bench; these helpers
// only pick which hosts to compare, compute ratios between measured values, and name things for people.
import type { HostSeries } from "../../lib/hosts.ts";

/** AWS Graviton generation from the instance family ("c8g.xlarge" → 4, "c7gn.large" → 3). */
export function gravitonGen(instanceType?: string): number | undefined {
  const m = /^[a-z]+(\d+)([a-z]*)\./.exec(instanceType ?? "");
  if (!m || !m[2].includes("g")) return undefined;
  const gen = Number(m[1]) - 4; // 6g → Graviton2, 7g → Graviton3, 8g → Graviton4
  return gen >= 2 ? gen : undefined;
}

export function processorName(instanceType?: string): string | undefined {
  const g = gravitonGen(instanceType);
  return g ? `Graviton${g}` : undefined;
}

export function ratio(num?: number, den?: number): number | undefined {
  return typeof num === "number" && typeof den === "number" && num > 0 && den > 0 ? num / den : undefined;
}

/** Newest-vs-previous Graviton comparison: same build and HAL on both sides, preferring the baseline kind. */
export function generationPair(series: HostSeries[]): { newer: HostSeries; older: HostSeries } | undefined {
  const withGen = series.map((s) => ({ s, g: gravitonGen(s.host.instance_type) })).filter((x) => x.g !== undefined);
  const gens = [...new Set(withGen.map((x) => x.g as number))].sort((a, b) => b - a);
  if (gens.length < 2) return undefined;
  const [hi, lo] = gens;
  const newer = withGen.filter((x) => x.g === hi).map((x) => x.s);
  const older = withGen.filter((x) => x.g === lo).map((x) => x.s);
  const kind = (s: HostSeries) => `${s.build}:${s.hal}`;
  const legacy = (s: HostSeries) => s.key.replace(/^c\dg-/, "");
  // Same kind of build first (the baseline kind wins ties), then same legacy label, then the first of each.
  for (const n of [...newer].sort((a, b) => Number(b.baseline) - Number(a.baseline))) {
    const o = older.find((x) => kind(x) === kind(n) && (n.hal !== "unknown" || legacy(x) === legacy(n)));
    if (o) return { newer: n, older: o };
  }
  return { newer: newer[0], older: older[0] };
}

/** KleidiCV on vs off. Controlled = same-compiler source builds; wheel = PyPI wheels (compilers differ). */
export function kleidiResult(
  series: HostSeries[],
  srcPair?: { off: HostSeries; on: HostSeries; compiler?: string },
): { off: HostSeries; on: HostSeries; controlled: boolean; compiler?: string } | undefined {
  if (srcPair) return { ...srcPair, controlled: true };
  const off = series.find((s) => s.build === "wheel" && s.hal === "carotene");
  const on = series.find(
    (s) => s.build === "wheel" && s.hal === "kleidicv" && (!off || s.host.instance_type === off.host.instance_type),
  );
  return off && on ? { off, on, controlled: false } : undefined;
}

const STAGE_NAMES: Record<string, string> = {
  decode_webp: "Decode WebP",
  resize_1024_area: "Resize to 1024 px",
  cvtcolor_gray: "Convert to grayscale",
  clahe: "CLAHE contrast",
  laplacian_sobel: "Sharpness (Laplacian, Sobel)",
  phash_blockmean: "Perceptual hash",
  yunet_1024: "Face detection (YuNet)",
  brisque: "BRISQUE quality score",
  opencv_total: "All OpenCV stages",
};

export function stageLabel(name: string): string {
  if (STAGE_NAMES[name]) return STAGE_NAMES[name];
  const s = name.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** The per-image total row is a sum of the others: it belongs in a summary, not on the same axis. */
export function isTotalStage(name: string): boolean {
  return /(^|_)total$/.test(name);
}

/** "+12.2%" style change from `from` to `to`. */
export function pctChange(to?: number, from?: number): number | undefined {
  return typeof to === "number" && typeof from === "number" && from > 0 ? (to / from - 1) * 100 : undefined;
}

export function fmtSigned(v: number, digits = 1): string {
  const s = Math.abs(v).toFixed(digits);
  return `${v >= 0 ? "+" : "−"}${s}%`;
}

export function fmtMs(v: number): string {
  return v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
}

/** Legend/series names people recognise: "Graviton4 · c8g.xlarge" when the processor alone tells hosts apart,
 *  else the build description from lib/hosts (e.g. "PyPI wheel, KleidiCV + carotene, c8g.xlarge"). */
export function withDisplayNames(series: HostSeries[]): HostSeries[] {
  const procs = series.map((s) => processorName(s.host.instance_type));
  const unique = procs.every((p) => p) && new Set(procs).size === procs.length;
  if (!unique) return series;
  return series.map((s, i) => ({ ...s, label: `${procs[i]} · ${s.host.instance_type}` }));
}

/** Short name for a host in tight places (column heads, tooltips): the processor, else the instance family. */
export function shortHost(s: HostSeries, series: HostSeries[]): string {
  const p = processorName(s.host.instance_type);
  const unique = p && series.filter((x) => processorName(x.host.instance_type) === p).length === 1;
  return unique ? p : (s.host.instance_type?.split(".")[0] ?? s.key);
}

/** "5 Oct 2026, 18:09 IST": date with year and the viewer's time zone. */
export function fmtMeasured(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" });
}

/** Headline throughput host: the newest Graviton generation's baseline-kind host, else the fastest host. */
export function headlineHost(series: HostSeries[]): HostSeries | undefined {
  const gen = generationPair(series);
  if (gen) return gen.newer;
  return [...series].filter((s) => typeof s.host.images_per_sec === "number").sort((a, b) => (b.host.images_per_sec ?? 0) - (a.host.images_per_sec ?? 0))[0];
}

/** Seconds of compute for `photos` at `ips` images/s (one instance, as benchmarked). */
export function eventSeconds(photos: number, ips?: number): number | undefined {
  return typeof ips === "number" && ips > 0 && photos >= 0 ? photos / ips : undefined;
}

/** Compute cost in USD for `photos` at the measured cost per 1,000 images. */
export function eventCost(photos: number, costPer1k?: number): number | undefined {
  return typeof costPer1k === "number" && costPer1k >= 0 && photos >= 0 ? (photos / 1000) * costPer1k : undefined;
}

/** "~70 s", "~2 min", "~1.4 h" for a story line (rounded, never more precise than the data). */
export function approxDuration(s: number): string {
  if (s < 90) return `~${Math.max(1, Math.round(s / (s >= 20 ? 5 : 1)) * (s >= 20 ? 5 : 1))} s`;
  if (s < 5400) return `~${Math.round(s / 60)} min`;
  return `~${(s / 3600).toFixed(1)} h`;
}

/** "$0.0021", "$0.21", "$2.11": enough significant digits for tiny per-event costs. */
export function fmtUsd(v: number): string {
  if (v === 0) return "$0";
  if (v < 0.01) return `$${v.toPrecision(2)}`;
  if (v < 100) return `$${v.toFixed(2)}`;
  return `$${Math.round(v).toLocaleString("en-US")}`;
}

import type { BenchDoc, BenchHost } from "./types";

// What a host IS comes from its measured build, not its name: KleidiCV iff cv2 reported a KleidiCV HAL
// (`opencv_hal`), and a source build iff the label starts with `src-` (the benchmark naming for builds made
// from source with one compiler). Colour follows that kind, so a re-run under a new label keeps its colour:
// slot 1 PyPI wheel + carotene (the baseline), slot 2 PyPI wheel + KleidiCV, slot 3 source build + carotene,
// slot 4 source build + KleidiCV. Older files without opencv_hal keep their label-fixed colours.
const KIND_COLOR: Record<string, string> = {
  "wheel:carotene": "var(--color-series-1)",
  "wheel:kleidicv": "var(--color-series-2)",
  "src:carotene": "var(--color-series-3)",
  "src:kleidicv": "var(--color-series-4)",
};
const LEGACY_COLOR: Record<string, string> = {
  stock: "var(--color-series-1)",
  "c7g-stock": "var(--color-series-3)",
};
const SPARE = ["var(--color-series-4)", "var(--color-series-3)", "var(--color-series-2)"];

/** Baseline in preference order: the carotene PyPI wheel (2026-10-08 bench), else the old `stock` label. */
const BASELINES = ["stock-carotene", "stock"];
/** Compiler measured for the benchmark src-gcc14-* builds (bench/results/2026-10-08/results.md), used only
 *  when the document itself does not carry `cxx_compiler`. */
const MEASURED_COMPILER: Record<string, string> = { gcc14: "GCC 14.2.1" };

export type Build = "wheel" | "src";
export type Hal = "carotene" | "kleidicv" | "unknown";

export interface HostSeries {
  key: string;
  label: string;
  color: string;
  /** The host every "vs baseline" ratio is measured against. */
  baseline: boolean;
  build: Build;
  hal: Hal;
  host: BenchHost;
}

export function halOf(h: Pick<BenchHost, "opencv_hal">): Hal {
  const v = (h.opencv_hal ?? "").toLowerCase();
  if (!v) return "unknown";
  return v.includes("kleidicv") ? "kleidicv" : v.includes("carotene") ? "carotene" : "unknown";
}

export function buildOf(h: Pick<BenchHost, "label">): Build {
  return h.label.startsWith("src-") ? "src" : "wheel";
}

/** "GCC 14.2.1" from `cxx_compiler` ("… (ver 14.2.1)"), else the measured value for a src-gccNN label. */
export function compilerOf(h: Pick<BenchHost, "label" | "cxx_compiler">): string | undefined {
  const m = /\(ver ([0-9.]+)\)/.exec(h.cxx_compiler ?? "");
  if (m) return `${/clang/i.test(h.cxx_compiler ?? "") ? "Clang" : "GCC"} ${m[1]}`;
  const tag = /^src-(gcc\d+)-/.exec(h.label)?.[1];
  return tag ? MEASURED_COMPILER[tag] : undefined;
}

const HAL_WORD: Record<Hal, string> = { carotene: "carotene HAL", kleidicv: "KleidiCV + carotene", unknown: "" };

export function hostName(h: Pick<BenchHost, "label" | "instance_type" | "opencv_hal" | "cxx_compiler">): string {
  const hal = halOf(h);
  let kind: string;
  if (buildOf(h) === "src") {
    const cc = compilerOf(h);
    kind = `Source build${cc ? ` (${cc})` : ""}, KleidiCV ${hal === "kleidicv" ? "on" : hal === "carotene" ? "off" : "?"}`;
  } else if (hal !== "unknown") {
    kind = `PyPI wheel, ${HAL_WORD[hal]}`;
  } else {
    kind = h.label.endsWith("stock") ? "Stock OpenCV 5" : h.label;
  }
  return h.instance_type ? `${kind}, ${h.instance_type}` : kind;
}

/** Hosts in a stable order: baseline, the other wheels (KleidiCV first), then source builds (off, on). */
export function hostSeries(doc: BenchDoc | undefined): HostSeries[] {
  const hosts = [...(doc?.hosts ?? [])].filter((h) => h.label);
  const base = BASELINES.find((b) => hosts.some((h) => h.label === b));
  const rank = (h: BenchHost) =>
    h.label === base ? 0 : (buildOf(h) === "src" ? 20 : 10) + (halOf(h) === "carotene" ? 0 : halOf(h) === "kleidicv" ? 1 : 2);
  hosts.sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
  const used = new Set<string>();
  const fixed = hosts.map((h) => {
    const c = LEGACY_COLOR[h.label] ?? (halOf(h) !== "unknown" ? KIND_COLOR[`${buildOf(h)}:${halOf(h)}`] : undefined);
    if (!c || used.has(c)) return undefined; // two hosts of one kind: the second takes a spare slot
    used.add(c);
    return c;
  });
  const spare = SPARE.filter((c) => !used.has(c));
  return hosts.map((h, i) => ({
    key: h.label,
    label: hostName(h),
    color: fixed[i] ?? spare.shift() ?? "var(--color-slate)",
    baseline: h.label === base,
    build: buildOf(h),
    hal: halOf(h),
    host: h,
  }));
}

export function baselineOf(series: HostSeries[]): HostSeries | undefined {
  return series.find((s) => s.baseline);
}

/** The controlled KleidiCV test: one carotene and one KleidiCV source build made with the same compiler. */
export function sourcePair(series: HostSeries[]): { off: HostSeries; on: HostSeries; compiler?: string } | undefined {
  const src = series.filter((s) => s.build === "src");
  for (const off of src.filter((s) => s.hal === "carotene")) {
    const cc = compilerOf(off.host);
    const on = src.find((s) => s.hal === "kleidicv" && compilerOf(s.host) === cc);
    if (on) return { off, on, compiler: cc };
  }
  return undefined;
}

/** Stage names in first-seen order across hosts. */
export function stageNames(series: HostSeries[]): string[] {
  const out: string[] = [];
  for (const s of series) for (const st of s.host.stages) if (!out.includes(st.name)) out.push(st.name);
  return out;
}

export function stageValue(s: HostSeries, stage: string, metric: "p50_ms" | "p95_ms"): number | undefined {
  const v = s.host.stages.find((r) => r.name === stage)?.[metric];
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

export function hasKleidiCv(series: HostSeries[]): boolean {
  return series.some((s) => s.hal === "kleidicv");
}

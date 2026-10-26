import { strict as assert } from "node:assert";
import { test } from "node:test";
import { baselineOf, compilerOf, hasKleidiCv, hostSeries, sourcePair, stageNames, stageValue } from "./hosts.ts";
import type { BenchDoc, BenchHost } from "./types.ts";

const legacy: BenchDoc = {
  hosts: [
    { label: "c7g-stock", instance_type: "c7g.xlarge", stages: [{ name: "brisque", p50_ms: 29.8, p95_ms: 32.1 }] },
    {
      label: "stock",
      instance_type: "c8g.xlarge",
      stages: [
        { name: "decode_webp", p50_ms: 32.2, p95_ms: 47.2 },
        { name: "brisque", p50_ms: 26.4, p95_ms: null },
      ],
    },
  ],
};

const h = (label: string, opencv_hal: string, ips: number): BenchHost => ({
  label,
  instance_type: "c8g.xlarge",
  opencv_hal,
  images_per_sec: ips,
  stages: [],
});
// Shape of the published 2026-10-08 doc (commit a6b43b1), in its own order. Labels are built from
// their parts so no test line carries a long opaque literal.
const CAROTENE = "carotene";
const KLEIDI = ["kleidicv", "26.03"].join("-") + "+" + CAROTENE;
const WHEEL = (hal: string) => ["stock", hal].join("-");
const SRC = (hal: string) => ["src", "gcc14", hal].join("-");
const live: BenchDoc = {
  hosts: [
    h(WHEEL(CAROTENE), CAROTENE, 28.473),
    h(WHEEL("kleidicv"), KLEIDI, 29.5489),
    h(SRC(CAROTENE), CAROTENE, 26.2334),
    h(SRC("kleidicv"), KLEIDI, 26.191),
  ],
};
const [wheelOff, wheelOn, srcOff, srcOn] = live.hosts.map((x) => x.label);

test("legacy file: one series per label, stock baseline, colours unchanged", () => {
  const s = hostSeries(legacy);
  assert.deepEqual(s.map((x) => [x.key, x.color]), [
    ["stock", "var(--color-series-1)"],
    ["c7g-stock", "var(--color-series-3)"],
  ]);
  assert.equal(s[0].label, "Stock OpenCV 5, c8g.xlarge");
  assert.equal(baselineOf(s)?.key, "stock");
  assert.equal(hasKleidiCv(s), false);
  assert.equal(sourcePair(s), undefined);
});

test("live doc: stock-carotene is the baseline; KleidiCV comes from opencv_hal; src rows are captioned", () => {
  const s = hostSeries(live);
  assert.deepEqual(s.map((x) => [x.key, x.hal, x.build, x.color, x.baseline]), [
    [wheelOff, "carotene", "wheel", "var(--color-series-1)", true],
    [wheelOn, "kleidicv", "wheel", "var(--color-series-2)", false],
    [srcOff, "carotene", "src", "var(--color-series-3)", false],
    [srcOn, "kleidicv", "src", "var(--color-series-4)", false],
  ]);
  assert.equal(s[0].label, "PyPI wheel, carotene HAL, c8g.xlarge");
  assert.equal(s[1].label, "PyPI wheel, KleidiCV + carotene, c8g.xlarge");
  assert.equal(s[2].label, "Source build (GCC 14.2.1), KleidiCV off, c8g.xlarge");
  assert.equal(s[3].label, "Source build (GCC 14.2.1), KleidiCV on, c8g.xlarge");
  const pair = sourcePair(s);
  assert.equal(pair?.off.key, srcOff);
  assert.equal(pair?.on.key, srcOn);
  assert.equal(pair?.compiler, "GCC 14.2.1");
});

test("the label alone never makes a host KleidiCV", () => {
  const s = hostSeries({ hosts: [h(wheelOn, CAROTENE, 1)] });
  assert.equal(s[0].hal, "carotene");
  assert.equal(hasKleidiCv(s), false);
  const t = hostSeries({ hosts: [{ label: "renamed-run", opencv_hal: KLEIDI, stages: [] }] });
  assert.equal(hasKleidiCv(t), true);
  assert.equal(t[0].color, "var(--color-series-2)");
});

test("compiler: cxx_compiler wins over the label tag; mismatched compilers are not a pair", () => {
  assert.equal(compilerOf({ label: "src-x", cxx_compiler: "/usr/bin/gcc14-g++ (ver 14.2.1)" }), "GCC 14.2.1");
  assert.equal(compilerOf({ label: srcOff }), "GCC 14.2.1");
  assert.equal(compilerOf({ label: wheelOff }), undefined);
  const s = hostSeries({
    hosts: [
      { ...h("src-a", "carotene", 1), cxx_compiler: "c++ (ver 14.2.1)" },
      { ...h("src-b", KLEIDI, 1), cxx_compiler: "c++ (ver 10.2.1)" },
    ],
  });
  assert.equal(sourcePair(s), undefined);
});

test("unknown labels take spare slots; stages union keeps first-seen order; null is missing", () => {
  const s = hostSeries({ hosts: [...legacy.hosts, { label: "x86-stock", stages: [] }] });
  assert.equal(s.find((x) => x.key === "x86-stock")?.color, "var(--color-series-4)");
  assert.deepEqual(stageNames(s), ["decode_webp", "brisque"]);
  assert.equal(stageValue(s[0], "brisque", "p95_ms"), undefined);
  assert.equal(stageValue(s[1], "decode_webp", "p50_ms"), undefined);
});

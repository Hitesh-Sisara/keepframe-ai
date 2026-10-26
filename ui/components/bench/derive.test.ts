import assert from "node:assert/strict";
import { test } from "node:test";
import { hostSeries } from "../../lib/hosts.ts";
import type { BenchDoc } from "../../lib/types.ts";
import { generationPair, gravitonGen, isTotalStage, kleidiResult, pctChange, stageLabel, withDisplayNames } from "./derive.ts";

const doc: BenchDoc = {
  hosts: [
    { label: "stock", instance_type: "c8g.xlarge", images_per_sec: 28.4, cost_per_1k_usd: 0.001055, stages: [] },
    { label: "c7g-stock", instance_type: "c7g.xlarge", images_per_sec: 25.3, cost_per_1k_usd: 0.001076, stages: [] },
  ],
};

test("Graviton generation comes from the instance family", () => {
  assert.equal(gravitonGen("c8g.xlarge"), 4);
  assert.equal(gravitonGen("c7gn.large"), 3);
  assert.equal(gravitonGen("m6g.large"), 2);
  assert.equal(gravitonGen("c7i.xlarge"), undefined);
  assert.equal(gravitonGen(undefined), undefined);
});

test("generation pair puts the newer Graviton first", () => {
  const pair = generationPair(hostSeries(doc));
  assert.equal(pair?.newer.host.instance_type, "c8g.xlarge");
  assert.equal(pair?.older.host.instance_type, "c7g.xlarge");
  assert.equal(generationPair(hostSeries({ hosts: [doc.hosts[0]] })), undefined);
});

test("no KleidiCV host means no KleidiCV result (never a made-up number)", () => {
  assert.equal(kleidiResult(hostSeries(doc)), undefined);
  const k = hostSeries({
    hosts: [
      { label: "stock-carotene", instance_type: "c8g.xlarge", opencv_hal: "carotene", images_per_sec: 28, stages: [] },
      { label: "stock-kleidicv", instance_type: "c8g.xlarge", opencv_hal: "kleidicv-26.03+carotene", images_per_sec: 29, stages: [] },
    ],
  });
  const r = kleidiResult(k);
  assert.equal(r?.controlled, false);
  assert.equal(r?.on.key, "stock-kleidicv");
});

test("display names use the processor when it tells hosts apart", () => {
  const s = withDisplayNames(hostSeries(doc));
  assert.deepEqual(s.map((x) => x.label), ["Graviton4 · c8g.xlarge", "Graviton3 · c7g.xlarge"]);
});

test("stage labels and totals", () => {
  assert.equal(stageLabel("yunet_1024"), "Face detection (YuNet)");
  assert.equal(stageLabel("new_stage"), "New stage");
  assert.equal(isTotalStage("opencv_total"), true);
  assert.equal(isTotalStage("clahe"), false);
  assert.equal(pctChange(110, 100)?.toFixed(1), "10.0");
  assert.equal(pctChange(1, 0), undefined);
});

test("event story numbers come straight from the measured rate and cost", async () => {
  const { eventSeconds, eventCost, approxDuration, fmtUsd } = await import("./derive.ts");
  const s = eventSeconds(2000, 28.4329)!;
  assert.ok(Math.abs(s - 70.34) < 0.01);
  assert.equal(approxDuration(s), "~70 s");
  assert.equal(approxDuration(12.4), "~12 s");
  assert.equal(approxDuration(600), "~10 min");
  assert.ok(Math.abs(eventCost(2000, 0.001055)! - 0.00211) < 1e-9);
  assert.equal(fmtUsd(0.00211), "$0.0021");
  assert.equal(fmtUsd(2.11), "$2.11");
  assert.equal(eventSeconds(100, 0), undefined);
  assert.equal(eventCost(100, undefined), undefined);
});

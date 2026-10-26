import assert from "node:assert/strict";
import { test } from "node:test";
import { LABEL_COLUMNS, setBest, toCsv, toggleReason, type Label } from "./labels.ts";
import { parseReason, reasonByKey, REASONS } from "./reasons.ts";

const base = (id: string, g: string): Label => ({
  photo_id: id,
  group_id_human: g,
  reasons_human: [],
  best_of_group_human: false,
  notes: "",
});

test("CSV has the eval columns in order and skips unlabelled frames", () => {
  const rows = [
    { ...base("p1", "g1"), verdict_human: "keep" as const, best_of_group_human: true, labelled_at: "2026-10-12T09:00:00Z" },
    { ...base("p2", "g1"), verdict_human: "reject" as const, reasons_human: ["BLUR_MOTION", "EYES_CLOSED"] },
    base("p3", "g1"),
  ];
  const lines = toCsv(rows, "ann1").trimEnd().split("\r\n");
  assert.equal(lines[0], LABEL_COLUMNS.join(","));
  assert.equal(lines[0], "photo_id,group_id_human,verdict_human,reasons_human,best_of_group_human,labeller,labelled_at,notes");
  assert.equal(lines[1], "p1,g1,keep,,1,ann1,2026-10-12T09:00:00Z,");
  assert.equal(lines[2], "p2,g1,reject,BLUR_MOTION;EYES_CLOSED,0,ann1,,");
  assert.equal(lines.length, 3);
});

test("CSV quotes commas/quotes and neutralises formulas", () => {
  const row = { ...base("p1", "g1"), verdict_human: "keep" as const, notes: '=HYPERLINK("x"), artistic' };
  const line = toCsv([row], "a").split("\r\n")[1];
  assert.equal(line, `p1,g1,keep,,0,a,,"'=HYPERLINK(""x""), artistic"`);
});

test("one best per group", () => {
  let rows = [base("p1", "g1"), base("p2", "g1"), base("p3", "g2")];
  rows = setBest(rows, "p1");
  rows = setBest(rows, "p2");
  rows = setBest(rows, "p3");
  assert.deepEqual(
    rows.map((r) => r.best_of_group_human),
    [false, true, true],
  );
  rows = setBest(rows, "p2");
  assert.equal(rows[1].best_of_group_human, false);
});

test("reason toggle and key map", () => {
  const l = toggleReason(toggleReason(base("p", "g"), "BLUR_FOCUS"), "EYES_CLOSED");
  assert.deepEqual(toggleReason(l, "BLUR_FOCUS").reasons_human, ["EYES_CLOSED"]);
  assert.equal(reasonByKey(1)?.code, "BLUR_MOTION");
  assert.equal(reasonByKey(9)?.code, "NO_SUBJECT");
  assert.equal(REASONS.length, 14);
  assert.deepEqual(parseReason("DUPLICATE_OF:p_88").arg, "p_88");
});

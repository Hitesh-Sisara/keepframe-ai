import { test } from "node:test";
import assert from "node:assert/strict";
import { eyesTo100, GLOSSARY, glossParts, groupDigits, policyTitle } from "./glossary.ts";

test("policy codes and measurement names are split out", () => {
  const p = glossParts("Margin 0.03 < 0.05 (Policy 5). lap_var and BRISQUE next.");
  assert.deepEqual(
    p.filter((x) => x.policy || x.term).map((x) => [x.text, x.policy ?? x.term]),
    [
      ["Policy 5", 5],
      ["lap_var", "lap_var"],
      ["BRISQUE", "brisque"],
    ],
  );
  assert.equal(p.map((x) => x.text).join(""), "Margin 0.03 < 0.05 (Policy 5). lap_var and BRISQUE next.");
});

test("a combined policy reference gets one tooltip per number", () => {
  const p = glossParts("Policy 1/3: compare within the group");
  assert.equal(p.map((x) => x.text).join(""), "Policy 1/3: compare within the group");
  assert.deepEqual(p.filter((x) => x.policy).map((x) => x.policy), [1, 3]);
});

test("short codes and prose names", () => {
  assert.deepEqual(glossParts("P2 then P5").filter((x) => x.policy).map((x) => x.policy), [2, 5]);
  assert.equal(glossParts("Laplacian variance 4229").find((x) => x.term)?.term, "lap_var");
  assert.equal(glossParts("Photo p_0069 is fine").some((x) => x.policy || x.term), false);
});

test("policy titles read in plain language", () => {
  assert.equal(policyTitle(5), "Policy 5: close call → ask a person");
});

test("long numbers in free text get grouped like the bars", () => {
  assert.equal(groupDigits("Laplacian variance 4229, re-scored"), "Laplacian variance 4,229, re-scored");
  assert.equal(groupDigits("in 2026, p_0069, 0.0113"), "in 2026, p_0069, 0.0113");
});

test("approval reason terms are in the glossary, with a plain word", async () => {
  const { GLOSSARY, plainTerms } = await import("./glossary.ts");
  const p = glossParts("main eyes closed (open_p 0.10/0.01), fiqa 0.47, clip_lo 0.29, mean_L 208, std_L 19.2, lap_var 1231");
  assert.deepEqual(p.filter((x) => x.term).map((x) => x.term), ["open_p", "fiqa", "clip_lo", "mean_L", "std_L", "lap_var"]);
  for (const t of p.filter((x) => x.term)) assert.ok(GLOSSARY[t.term as string]?.plain, t.term);
  assert.equal(plainTerms("motion blur (motion_ratio 6.1, lap_var 1231)"), "motion blur (motion streaks 6.1, sharpness 1231)");
});

test("eyesTo100 rescales left/right eye pairs and open_p ranges only", () => {
  assert.equal(eyesTo100("main eyes open (1.0/1.0)"), "main eyes open (100/100)");
  assert.equal(eyesTo100("(f1, 0.0001/0.008)"), "(f1, 0/1)");
  assert.equal(eyesTo100("open p 0.1545/0.2146"), "open p 15/21");
  assert.equal(eyesTo100("All under-exposed (0.57-0.62), lap_var 2285-2579, 1/3 faces"), "All under-exposed (0.57-0.62), lap_var 2285-2579, 1/3 faces");
  assert.match(GLOSSARY.open_p.body, /0 \(closed\) to 100 \(open\)/);
});

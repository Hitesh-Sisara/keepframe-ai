import { strict as assert } from "node:assert";
import { test } from "node:test";
import { captureOrder, earliestExpiry, frameNames, frameOrder, groupIndex, nameIdsInText, shortPhoto, faceCrop, faceScale, memberMatches, normalizeScores, plainReason, scoreBars, sortGroups, traceRows } from "./cull.ts";

test("plainReason strips the scripted prefix, verdict word and bracketed numbers", () => {
  assert.equal(
    plainReason("Auto-approved by a script (no human review). Keep: best frame of its group [sharpness 43904 (faces)]"),
    "Best frame of its group.",
  );
  assert.equal(plainReason("Reject: weaker frame of its burst; main subject's eyes closed [sharpness 1 (faces), margin 0.01 of 6]"), "Weaker frame of its burst; main subject's eyes closed.");
  assert.equal(plainReason("Decided by a person: Keeper looks right."), "");
  assert.equal(plainReason(undefined), "");
});

test("normalizeScores reads nested worker rows and flat API rows", () => {
  const nested = normalizeScores({
    sharpness: { lap_var: 10, label: "borderline", blur_kind: "motion", roi_used: "faces" },
    exposure: { clip_hi_pct: 0.0125, label: "ok", mean_L: 100 },
    eyes: { per_face: [{ face_id: "f1", state: "open", is_main: false }, { face_id: "f0", state: "closed", is_main: true }] },
    face_quality: { per_face: [{ face_id: "f0", fiqa: 0.5 }] },
    breakdown: { sharp_faces_norm: 0.4, exposure_ok: 1, eyes_open_mean: 0.2, fiqa_mean: 0.6 },
  });
  assert.equal(nested.lap_var, 10);
  assert.equal(nested.clip_hi_pct, 1.3);
  assert.equal(nested.faces[0].face_id, "f0", "main subject first when there are no boxes");
  assert.equal(nested.faces[0].fiqa, 0.5);
  const bars = scoreBars(nested);
  assert.deepEqual(bars.map((b) => b.tone), ["warn", "good", "bad", "good"]);
  assert.equal(scoreBars(normalizeScores({ sharpness: { label: "borderline" }, breakdown: { sharp_faces_norm: 0 } }))[0].tone, "bad", "worse of label and value");
  assert.match(bars[0].text, /Borderline \(motion blur\)/);
  assert.match(bars[2].text, /avg 20% · main closed/);
});

test("score bars say No faces instead of an empty bar", () => {
  const s = normalizeScores({ sharpness: { label: "sharp" }, breakdown: { sharp_faces_norm: 1, eyes_open_mean: 0.5 } });
  const eyes = scoreBars(s).find((b) => b.key === "eyes");
  assert.equal(eyes?.text, "No faces");
  assert.equal(eyes?.value, undefined);
});

test("faceScale derives proxy/processed scale from the faces ROI, else the smallest fitting size", () => {
  // p_0005: processed 1920 wide, proxy 1024; roi_rect is in proxy pixels.
  const s = normalizeScores({
    sharpness: { roi_used: "faces", roi_rect: [335, 44, 353, 346] },
    faces: [
      { face_id: "f0", bbox_px: [666, 138, 368, 538], main_subject: true },
      { face_id: "f1", bbox_px: [1175, 402, 103, 135] },
    ],
  });
  assert.equal(faceScale(s, 1024, 576), 1024 / 1920);
  assert.deepEqual(s.faces.map((f) => f.face_id), ["f0", "f1"], "left to right when boxes are known");
  const noRoi = normalizeScores({ faces: [{ face_id: "f0", bbox_px: [1800, 100, 100, 100] }] });
  assert.equal(faceScale(noRoi, 1024, 683), 1024 / 1920);
  const c = faceCrop([100, 100, 100, 100], 0.5);
  assert.ok(c.side > 50 && c.cx === 75);
});

test("filters and sorts", () => {
  assert.ok(memberMatches({ verdict: "reject", reason_codes: ["DUPLICATE_OF:p_1"] }, "reject", "DUPLICATE_OF"));
  assert.ok(!memberMatches({ verdict: "keep" }, "reject", ""));
  const gs = [
    { group_id: "g_e_10", size: 2, margin: 0.2, members: [] },
    { group_id: "g_e_2", size: 6, margin: 0.01, members: [] },
    { group_id: "g_e_3", size: 3, members: [] },
  ];
  assert.deepEqual(sortGroups(gs, "capture").map((g) => g.group_id), ["g_e_2", "g_e_3", "g_e_10"]);
  assert.deepEqual(sortGroups(gs, "close").map((g) => g.group_id), ["g_e_2", "g_e_10", "g_e_3"]);
  assert.deepEqual(sortGroups(gs, "size").map((g) => g.group_id), ["g_e_2", "g_e_3", "g_e_10"]);
  assert.deepEqual(captureOrder([{ photo_id: "p_0010" }, { photo_id: "p_0009" }]).map((m) => m.photo_id), ["p_0009", "p_0010"]);
  assert.deepEqual(captureOrder([{ photo_id: "ph_ff" }, { photo_id: "ph_0a" }]).map((m) => m.photo_id), ["ph_ff", "ph_0a"], "aliases keep API order");
  const aliased = [
    { group_id: "gx_b", group_label: "Group 10", size: 2, members: [] },
    { group_id: "gx_a", group_label: "Group 2", size: 2, members: [] },
  ];
  assert.deepEqual(sortGroups(aliased, "capture").map((g) => g.group_label), ["Group 2", "Group 10"]);
});

test("earliestExpiry reads absolute exp stamps only", () => {
  assert.equal(earliestExpiry(["http://x/img/a.jpg?exp=1792980300&sig=a", "http://x/img/b.jpg?exp=1792980240&sig=b", undefined]), 1792980240);
  assert.equal(earliestExpiry(["https://s3/x?X-Amz-Expires=900&X-Amz-Signature=z"]), undefined);
  assert.equal(earliestExpiry([]), undefined);
});

test("traceRows brackets each pivot span and attaches results to their calls", () => {
  const L = (seq: number, type: string, tool?: string) => ({ run_id: "r", seq, type, tool });
  const lines = [L(1, "tool_call", "a"), L(2, "tool_result", "a"), L(3, "model_reasoning"), L(4, "tool_call", "b"), L(5, "tool_result", "b"), L(6, "model_reasoning")];
  const rows = traceRows(lines, [{ kind: "tagged", policy: "", summary: "x", result_seq: 2, reasoning_seqs: [3], call_seq: 4 }]);
  assert.deepEqual(
    rows.map((r) => (r.kind === "pivot" ? `P${r.rows.map((x) => x.line.seq).join("")}` : `${r.line.seq}${r.attached ? "a" : ""}`)),
    ["1", "P234", "5", "6"],
  );
});

test("judge aliases: rank order, Frame N names, short ids, scrubbed text, alias group index", () => {
  const ms = [{ photo_id: "ph_911bd08c55", rank: 4 }, { photo_id: "ph_2eccb434ad", rank: 1 }];
  assert.equal(frameOrder(ms).by, "rank");
  assert.deepEqual(frameNames(ms), { ph_2eccb434ad: "Frame 1", ph_911bd08c55: "Frame 2" });
  assert.equal(frameOrder([{ photo_id: "p_0002" }, { photo_id: "p_0001" }]).by, "capture");
  assert.equal(shortPhoto("ph_2eccb434ad"), "2eccb4");
  assert.equal(shortPhoto("p_0068"), "p_0068");
  assert.equal(nameIdsInText("Escalate p_0071 vs ph_2eccb434ad", { ph_2eccb434ad: "Frame 1" }), "Escalate a frame vs Frame 1");
  assert.equal(groupIndex("gx_0843288799", "Group 8"), 8);
  assert.equal(groupIndex("g_ts2026_13", "Group 13"), 13);
});

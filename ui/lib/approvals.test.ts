import { strict as assert } from "node:assert";
import { test } from "node:test";
import {
  chooseCandidates,
  fitColumns,
  gist,
  refineWhy,
  idsToFrames,
  keepAllForReview,
  leaders,
  pickFrame,
  planSummary,
  plainReasons,
  proposedMap,
  relAge,
  sameAssign,
  toVerdictMap,
  type FrameVM,
} from "../components/approvals/logic.ts";

const frames: FrameVM[] = [1, 2, 3, 4, 5, 6].map((n) => ({ photo_id: `p_000${n}`, proxy_url: "", n }));
const proposed = proposedMap({ keep: ["p_0005"], reject: ["p_0001", "p_0002", "p_0003", "p_0006"], review: ["p_0004"] });

test("proposal map reads keep as a string or a list, and single-photo verdicts", () => {
  assert.equal(proposed.p_0005, "keep");
  assert.equal(proposed.p_0004, "review");
  assert.deepEqual(proposedMap({ keep: "p_9" }), { p_9: "keep" });
  assert.deepEqual(proposedMap("reject", "p_1"), { p_1: "reject" });
  assert.deepEqual(proposedMap(undefined), {});
});

test("pick another makes the clicked frame the keeper and sends the old keeper to review", () => {
  const a = pickFrame(frames, proposed, "p_0002");
  assert.equal(a.p_0002, "keep");
  assert.equal(a.p_0005, "review");
  assert.equal(a.p_0004, "review");
  assert.equal(a.p_0001, "reject");
  assert.equal(Object.values(a).filter((v) => v === "keep").length, 1);
});

test("picking the proposed keeper is the proposal", () => {
  assert.ok(sameAssign(frames, pickFrame(frames, proposed, "p_0005"), proposed));
  assert.ok(!sameAssign(frames, pickFrame(frames, proposed, "p_0001"), proposed));
});

test("keep all for review rejects nothing and still keeps one frame", () => {
  const a = keepAllForReview(frames, proposed);
  assert.equal(a.p_0005, "keep");
  assert.ok(Object.values(a).every((v) => v !== "reject"));
  const m = toVerdictMap(frames, a);
  assert.deepEqual(m.keep, ["p_0005"]);
  assert.equal(m.review.length, 5);
  assert.equal(m.reject.length, 0);
});

test("override map covers every frame; unmentioned frames go to review", () => {
  const m = toVerdictMap(frames, { p_0001: "keep" });
  assert.deepEqual(m.keep, ["p_0001"]);
  assert.equal(m.review.length, 5);
});

test("agent reasons become plain sentences with frame numbers, never raw ids", () => {
  const s = plainReasons("Top two frames within 0.01; main subject's eyes closed in p_0006", frames);
  assert.deepEqual(s, ["The top two frames scored within 0.01 of each other.", "The main subject's eyes are closed in Frame 6."]);
  assert.deepEqual(plainReasons("Top two frames within 0.00", frames), ["The top two frames scored exactly the same."]);
  assert.deepEqual(plainReasons("1 frame(s) with a merit the keeper lacks", frames), ["Another frame has something the keeper lacks."]);
  assert.equal(idsToFrames("see p_0099 and ph_abcdef1234", frames), "see a frame and a frame");
  assert.deepEqual(plainReasons("main subject's eyes closed in p_0006, p_0002", frames), ["The main subject's eyes are closed in Frames 2 and 6."]);
});

test("plan summary names what will be saved", () => {
  assert.equal(planSummary(frames, proposed), "keep Frame 5 · review Frame 4 · reject the other 4");
  assert.equal(planSummary(frames.slice(0, 2), { p_0001: "keep", p_0002: "reject" }), "keep Frame 1 · reject Frame 2");
  assert.equal(planSummary(frames.slice(0, 3), { p_0001: "keep", p_0002: "reject", p_0003: "reject" }, "rank"), "keep Frame 1 · reject Frames 2, 3");
});

test("approval frames use the burst page's numbering: capture order for p_ ids, else rank", async () => {
  const { fromApproval, fromPendingGroup } = await import("../components/approvals/logic.ts");
  const { frameNames } = await import("./cull.ts");
  const mem = (id: string, rank: number) => ({ photo_id: id, proxy_url: "", is_best: rank === 1, rank });
  // Aliased ids (demo, judge): the API lists approval members in any order; the group knows the ranks.
  const group = { group_id: "gx_1", size: 3, members: [mem("ph_ccc", 2), mem("ph_aaa", 3), mem("ph_bbb", 1)], verdict_counts: {} };
  const approval = { approval_id: "gx_1", run_id: "r", event_id: "e", group_id: "gx_1", members: [mem("ph_aaa", 3), mem("ph_ccc", 2), mem("ph_bbb", 1)] };
  const names = frameNames(group.members);
  const byFrame = (vm: { frames: FrameVM[] }) => Object.fromEntries(vm.frames.map((f) => [f.photo_id, `Frame ${f.n}`]));
  assert.deepEqual(byFrame(fromApproval(approval as never, group as never)), names);
  assert.deepEqual(byFrame(fromPendingGroup(group as never, "e")), names);
  assert.equal(names.ph_bbb, "Frame 1");
  // Capture ids: capture order, whatever the ranks.
  const cap = { ...group, members: [mem("p_0003", 1), mem("p_0001", 2), mem("p_0002", 3)] };
  assert.deepEqual(byFrame(fromPendingGroup(cap as never, "e")), { p_0001: "Frame 1", p_0002: "Frame 2", p_0003: "Frame 3" });
});

test("candidates: keeper plus best-ranked alternatives, in capture order", () => {
  const ranked = frames.map((f) => ({ ...f, rank: [4, 3, 5, 2, 1, 6][f.n - 1] }));
  const c = chooseCandidates(ranked, proposed, 4);
  assert.deepEqual(c.map((f) => f.n), [1, 2, 4, 5]);
  assert.equal(chooseCandidates(frames.slice(0, 3), proposed, 4).length, 3);
  // A frame the person picked from the filmstrip is pulled onto the stage.
  assert.deepEqual(chooseCandidates(ranked, proposed, 4, ["p_0006"]).map((f) => f.n), [2, 4, 5, 6]);
});

test("leaders only names a strict winner", () => {
  const fs = [
    { ...frames[0], score: 0.8, breakdown: { eyes_open_mean: 0.5 } },
    { ...frames[1], score: 0.7, breakdown: { eyes_open_mean: 0.5 } },
  ];
  const l = leaders(fs);
  assert.equal(l.score, "p_0001");
  assert.equal(l.eyes_open_mean, undefined);
  // 0.581 and 0.584 both show as 58: no winner.
  const near = leaders([
    { ...frames[0], breakdown: { fiqa_mean: 0.581 } },
    { ...frames[1], breakdown: { fiqa_mean: 0.584 } },
  ]);
  assert.equal(near.fiqa_mean, undefined);
});

test("fit columns picks the layout with the largest frames", () => {
  assert.equal(fitColumns(2, 1200, 600).cols, 2);
  assert.equal(fitColumns(6, 1200, 700).cols, 3);
  assert.equal(fitColumns(1, 800, 600).cols, 1);
  // minCols keeps the candidates in one row even when stacking would make each a little larger.
  assert.equal(fitColumns(2, 874, 657, 16 / 9, 10, 54).cols, 1);
  assert.equal(fitColumns(2, 874, 657, 16 / 9, 10, 54, 2).cols, 2);
});

test("relative age", () => {
  const now = Date.parse("2026-10-26T10:00:00Z");
  assert.equal(relAge("2026-10-26T09:59:50Z", now), "just now");
  assert.equal(relAge("2026-10-26T09:48:00Z", now), "12 min ago");
  assert.equal(relAge("2026-10-26T07:00:00Z", now), "3 h ago");
  assert.equal(relAge("2026-10-23T10:00:00Z", now), "3 d ago");
  assert.equal(relAge(undefined, now), "");
});

test("real scores replace the agent's rounded margin; the inbox gist tells requests apart", () => {
  const scored = frames.map((f) => ({ ...f, score: [0.72, 0.78, 0.7, 0.8217, 0.8358, 0.69][f.n - 1] }));
  const why = plainReasons("Top two frames within 0.01; main subject's eyes closed in p_0006", scored);
  assert.equal(refineWhy(why, scored)[0], "Frame 5 scored 83.6 and Frame 4 82.2 out of 100, only 1.4 pts apart.");
  assert.equal(refineWhy(why, scored, "rank")[0], "The top two frames scored 83.6 and 82.2 out of 100, only 1.4 pts apart.");
  const vm = { why, frames: scored, question: "q", order: "capture" } as unknown as Parameters<typeof gist>[0];
  assert.equal(gist(vm), "1.4 pts gap · eyes closed in Frame 6");
  assert.deepEqual(refineWhy(why, frames), why);
  // 0.8128 - 0.7824 = 0.0304, but shown as 0.813 and 0.782: the gap must read 0.031.
  const r = frames.slice(0, 2).map((f, i) => ({ ...f, score: [0.8128, 0.7824][i] }));
  assert.match(refineWhy(why, r)[0], /81\.3 and Frame 2 78\.2 out of 100, only 3\.1 pts apart/);
});

test("a tiny gap never prints as zero, and the agent's ranking replaces worker scores", async () => {
  const { fmtGap, applyRankScores, scoreLabel } = await import("../components/approvals/logic.ts");
  assert.equal(fmtGap(0.0024), "0.24 pts");
  assert.equal(fmtGap(0.014), "1.4 pts");
  assert.equal(fmtGap(0.0042), "0.42 pts");
  assert.equal(fmtGap(0.000004), "<0.01 pts");
  const two = frames.slice(0, 2).map((f, i) => ({ ...f, score: [0.78, 0.7] [i] }));
  const ranked = applyRankScores(two, [
    { photo_id: two[1].photo_id, score: 0.833 },
    { photo_id: two[0].photo_id, score: 0.8306 },
  ])!;
  assert.deepEqual(ranked.map((f) => [f.score, f.rank]), [[0.8306, 2], [0.833, 1]]);
  const why = ["The top two frames scored within 0.00 of each other."];
  assert.match(refineWhy(["The top two frames scored within 0 of each other."], ranked)[0], /83\.30 and Frame 1 83\.06 out of 100, only 0\.24 pts apart/);
  assert.equal(applyRankScores(two, [{ photo_id: "other", score: 1 }]), undefined);
  assert.equal(scoreLabel("agent"), "Score");
  assert.equal(scoreLabel(undefined), "Score");
  void why;
});

test("approval reasons read like the trace: frames, points, grouped digits, glossary terms", async () => {
  const { plainTerms } = await import("./glossary.ts");
  const fr: FrameVM[] = [{ photo_id: "ph_2eccb434ad", proxy_url: "", n: 1 }, { photo_id: "ph_911bd08c55", proxy_url: "", n: 2 }];
  const [s] = plainReasons("Margin 0.0249: ph_2eccb434ad 0.814 vs ph_911bd08c55 0.789. ph_911bd08c55 has motion blur (motion_ratio 6.1, lap_var 1231) and open_p 0.10/0.01, lap_var 10743", fr);
  assert.doesNotMatch(s, /ph_|Margin 0\./);
  assert.match(s, /^Gap 2\.5 pts: Frame 1 0\.814 vs Frame 2/);
  assert.match(s, /10,743/);
  assert.equal(plainTerms(s).includes("lap_var") || plainTerms(s).includes("open_p"), false);
  assert.match(plainTerms(s), /sharpness 1,231.*eyes-open score 10\/1\b/);
});

test("a 50/50 eyes/face-quality pair is the no-faces placeholder, never a leader", async () => {
  const { noFaces, leaders } = await import("../components/approvals/logic.ts");
  assert.equal(noFaces({ eyes_open_mean: 0.5, fiqa_mean: 0.5, sharp_faces_norm: 0.9 }), true);
  assert.equal(noFaces({ eyes_open_mean: 0.5, fiqa_mean: 0.62 }), false);
  const fr = (id: string, b: Record<string, number>) => ({ photo_id: id, proxy_url: "", n: 1, score: 0.5, breakdown: b });
  const lead = leaders([fr("a", { eyes_open_mean: 0.5, fiqa_mean: 0.5 }), fr("b", { eyes_open_mean: 0.4, fiqa_mean: 0.3 })] as never);
  assert.equal(lead.eyes_open_mean, "b");
});

test("Agent's reasoning shows eyes-open on the bars' 0-100 scale", () => {
  const r =
    "Margin 0.0249: ph_2eccb434ad 0.814 vs ph_2dfdacf6c8 0.789. ph_2eccb434ad main eyes open (0.94/0.81). ph_71860ba76a main eyes closed (open_p 0.10/0.01). Faces closed (open_p 0.15-0.21). One face (151 px, eyes partial 0.80/0.14, fiqa 0.47). Exposure ok, clip_lo 0.29-0.32.";
  const s = plainReasons(r, []).join(" ");
  assert.match(s, /eyes open \(94\/81\)/);
  assert.match(s, /open_p 10\/1\)/);
  assert.match(s, /open_p 15-21\)/);
  assert.match(s, /eyes partial 80\/14, fiqa 0\.47/);
  // Scores, fiqa and clipping stay as written; no eyes value is left on 0-1.
  assert.match(s, /0\.814 vs a frame 0\.789/);
  assert.match(s, /clip_lo 0\.29-0\.32/);
  assert.doesNotMatch(s, /\b0?\.\d+\/0?\.\d+/);
});

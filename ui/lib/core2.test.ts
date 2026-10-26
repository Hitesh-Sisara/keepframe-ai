import { strict as assert } from "node:assert";
import { test } from "node:test";
import { EMPTY_PICKS, lastTouched, nextUnpicked, picksReducer } from "./picks.ts";
import { heatRGBA, sharpMap } from "./sharpmap.ts";
import { blinkFrames, matchFaces } from "./facestrip.ts";
import { buildStory, storyHeadline } from "./story.ts";
import type { TraceLine } from "./types.ts";

test("picks: set, change, undo and redo walk the stack", () => {
  let s = picksReducer(EMPTY_PICKS, { type: "set", id: "a", pick: "keep" });
  s = picksReducer(s, { type: "set", id: "a", pick: "reject" });
  s = picksReducer(s, { type: "set", id: "b", pick: "review" });
  assert.deepEqual(s.picks, { a: "reject", b: "review" });
  const u1 = picksReducer(s, { type: "undo" });
  assert.deepEqual(u1.picks, { a: "reject" });
  assert.equal(lastTouched(s, u1), "b");
  const u2 = picksReducer(u1, { type: "undo" });
  assert.deepEqual(u2.picks, { a: "keep" });
  const r = picksReducer(u2, { type: "redo" });
  assert.deepEqual(r.picks, { a: "reject" });
  assert.equal(lastTouched(u2, r), "a");
  // A new pick clears the redo stack; setting the same pick is a no-op.
  const n = picksReducer(u2, { type: "set", id: "c", pick: "keep" });
  assert.equal(n.redo.length, 0);
  assert.equal(picksReducer(n, { type: "set", id: "c", pick: "keep" }), n);
  assert.equal(picksReducer(EMPTY_PICKS, { type: "undo" }), EMPTY_PICKS);
  const l = picksReducer(s, { type: "load", picks: { z: "keep" } });
  assert.deepEqual([l.picks, l.undo.length], [{ z: "keep" }, 0]);
});

test("picks: auto-advance goes to the next unpicked frame, wrapping", () => {
  const order = ["a", "b", "c", "d"];
  assert.equal(nextUnpicked(order, "a", { a: "keep" }), "b");
  assert.equal(nextUnpicked(order, "b", { a: "keep", b: "keep", c: "reject" }), "d");
  assert.equal(nextUnpicked(order, "d", { b: "keep", c: "keep", d: "keep" }), "a");
  assert.equal(nextUnpicked(order, "b", { a: "keep", b: "keep", c: "keep", d: "keep" }), "c");
  assert.equal(nextUnpicked(order, "d", { a: "keep", b: "keep", c: "keep", d: "keep" }), undefined);
});

test("sharpMap: flat image has no heat, a checkerboard is all sharp", () => {
  const w = 48, h = 48;
  const flat = new Uint8ClampedArray(w * h * 4).fill(128);
  const m = sharpMap(flat, w, h, 24);
  assert.equal(m.cols, 2);
  assert.equal(m.sharpShare, 0);
  const chk = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) chk.fill((x + y) % 2 ? 255 : 0, (y * w + x) * 4, (y * w + x) * 4 + 4);
  assert.equal(sharpMap(chk, w, h, 24).sharpShare, 1);
  assert.equal(heatRGBA(0.1)[3], 0);
  assert.ok(heatRGBA(0.9)[3] > 150);
});

test("matchFaces lines up a person across frames by position, not face id", () => {
  const f = (id: string, x: number, state: string, main = false) => ({ face_id: id, bbox_px: [x, 100, 80, 100], state, main_subject: main });
  const rows = matchFaces([
    [f("f0", 200, "open", true), f("f1", 800, "open")],
    [f("f0", 805, "open"), f("f1", 210, "closed", true)],
    [f("f0", 198, "open", true)],
  ]);
  assert.equal(rows.length, 2);
  assert.ok(rows[0].main);
  assert.deepEqual(rows[0].cells.map((c) => c?.eyes), ["open", "closed", "open"]);
  assert.deepEqual(blinkFrames(rows[0]), [1]);
  assert.equal(rows[1].cells[2], undefined);
  assert.deepEqual(matchFaces([[], []]), []);
});

test("buildStory tells the trace as plain steps and a headline", () => {
  const L = (seq: number, type: string, tool?: string, extra: Partial<TraceLine> = {}): TraceLine => ({ run_id: "r", seq, type, tool, ...extra });
  const lines: TraceLine[] = [
    L(1, "tool_call", "detect_faces"),
    L(2, "tool_result", "detect_faces", { result: { per_photo: [{ photo_id: "p_1", n_faces: 3 }] } }),
    L(3, "tool_call", "score_eyes_open"),
    L(4, "tool_result", "score_eyes_open", { result: { per_photo: [{ photo_id: "p_1", any_main_subject_closed: false }, { photo_id: "p_2", any_main_subject_closed: true }] } }),
    L(5, "model_reasoning", undefined, { text: "compare" }),
    L(6, "tool_call", "pick_best_in_cluster"),
    L(7, "tool_result", "pick_best_in_cluster", { result: { best_photo_id: "p_1", margin: 0.027, ranking: [{ photo_id: "p_1" }, { photo_id: "p_3" }] } }),
    L(8, "tool_call", "request_human_approval"),
    L(9, "interrupt_resumed", undefined, { answered_by: "scripted" }),
    L(10, "tool_result", "request_human_approval"),
  ];
  const names = { p_1: "Frame 1", p_2: "Frame 2", p_3: "Frame 3" };
  const steps = buildStory(lines, [{ kind: "eyes_compare_in_group", policy: "Policy 3", summary: "", result_seq: 4, reasoning_seqs: [5], call_seq: 6 }], names);
  assert.deepEqual(steps.map((s) => s.kind), ["faces", "eyes", "rank", "ask", "answer"]);
  assert.equal(steps[2].pivot?.index, 1);
  assert.deepEqual(steps[1].seqs, [3, 4, 5]);
  assert.match(steps[2].text, /2\.7 pts ahead of Frame 3/);
  assert.equal(steps[4].short, "auto-answered (test run)");
  assert.deepEqual(storyHeadline(steps).slice(0, 4), ["Faces found", "eyes closed on Frame 2", "compared burst: Frame 1 by 2.7 pts", "asked for review"]);
  for (const st of steps) assert.ok(st.short.split(" ").length <= 10, st.short);
  // Raw ids never leak: an unknown id reads "a frame".
  assert.ok(!JSON.stringify(buildStory(lines, [], {})).includes("p_"));
});

import { cardReason, CONFIDENCE_HINT, CONFIDENCE_WORD, confidenceOf } from "./cardreason.ts";
test("cardReason names a factor in 6 words and confidence is categorical", () => {
  const m = (id: string, verdict: "keep" | "reject" | "review", codes: string[], rank: number) => ({ photo_id: id, proxy_url: "", verdict, reason_codes: codes, rank, is_best: verdict === "keep" });
  const g = {
    margin: 0.027,
    tie: false,
    pending_approval: false,
    members: [m("p_0001", "reject", ["BURST_NOT_BEST", "EYES_CLOSED"], 3), m("p_0002", "review", ["REVIEW_LOW_CONFIDENCE"], 2), m("p_0003", "keep", ["KEEP_BEST_OF_GROUP"], 1), m("p_0004", "reject", ["BURST_NOT_BEST", "EYES_CLOSED"], 4)],
  };
  assert.equal(cardReason(g), "2 blinks · leads by 2.7");
  assert.equal(confidenceOf(g), "close");
  const plain = { ...g, margin: 0.2, members: [m("p_0001", "keep", [], 1), m("p_0002", "reject", ["BURST_NOT_BEST"], 2)] };
  assert.equal(cardReason(plain), "Leads by 20.0");
  assert.equal(confidenceOf(plain), "confident");
  assert.equal(confidenceOf({ ...plain, margin: 0.001 }), "close");
  assert.match(cardReason({ ...plain, margin: 0.001 }), /^Tied with Frame 2$/);
  const many = { ...g, members: [m("p_1", "keep", [], 1), ...["EYES_CLOSED", "BLUR_MOTION", "BLUR_FOCUS", "EXPOSURE_UNDER", "EXPOSURE_OVER", "LOW_CONTRAST", "DUPLICATE_OF:p_1"].map((c, i) => m(`p_${i + 2}`, "reject", [c], i + 2))] };
  assert.ok(cardReason(many).split(" ").filter((w) => w !== "·").length <= 6, cardReason(many));
  assert.equal(cardReason({ ...g, members: [m("p_1", "review", [], 1)] }), "No pick · 1 to review");
});

import { marginsToPts } from "./cull.ts";
import { fmtGap } from "./closecall.ts";
test("margins never read 0.0 when non-zero", () => {
  assert.equal(fmtGap(0.0024), "0.24 pts");
  assert.equal(fmtGap(0.003976), "0.40 pts");
  assert.equal(marginsToPts("Margin 0.00 < 0.05 (Policy 5).", 0.003976), "gap 0.40 pts (under 5) (Policy 5).");
  assert.equal(marginsToPts("tie (margin 0.00) → asked", 0.00146), "tie (gap 0.15 pts) → asked");
  assert.equal(marginsToPts("tie (margin 0.00) → asked"), "tie (gap under 0.5 pts) → asked");
  assert.equal(marginsToPts("margin 0.03 < 0.05", 0.026969), "gap 2.7 pts (under 5)");
});

import { bestAvailableFace } from "./facestrip.ts";
test("bestAvailableFace flags a keep whose main face is shut in every frame", () => {
  const f = (x: number, state: string, main = true) => ({ face_id: "f", bbox_px: [x, 100, 80, 100], state, main_subject: main });
  const shut = matchFaces([[f(200, "closed")], [f(205, "partial")], [f(198, "closed")]]);
  assert.equal(bestAvailableFace(shut, 1), 0);
  const oneOpen = matchFaces([[f(200, "closed")], [f(205, "open")], [f(198, "closed")]]);
  assert.equal(bestAvailableFace(oneOpen, 0), -1);
  assert.equal(bestAvailableFace(shut, -1), -1);
});

test("burst header chip never says Unsure next to a Keep / Best pick frame", () => {
  const m = (id: string, verdict: "keep" | "reject" | "review", best: boolean) => ({ photo_id: id, proxy_url: "", verdict, reason_codes: [], rank: best ? 1 : 2, is_best: best });
  // The demo's gx_3e26801f29: a tied group whose best frame is a Keep.
  const tied = { margin: 0.0024, tie: true, pending_approval: false, members: [m("p_1", "keep", true), m("p_2", "review", false), m("p_3", "reject", false)] };
  assert.equal(confidenceOf(tied), "close");
  assert.equal(CONFIDENCE_WORD[confidenceOf(tied)], "Close call");
  assert.equal(confidenceOf({ ...tied, tie: false, margin: 0.00003 }), "close");
  // Unsure stays for no pick, or a group waiting for a person.
  assert.equal(confidenceOf({ ...tied, members: [m("p_1", "review", false), m("p_2", "reject", false)] }), "unsure");
  assert.equal(confidenceOf({ ...tied, pending_approval: true }), "unsure");
  assert.match(CONFIDENCE_HINT, /not a frame's verdict/);
});

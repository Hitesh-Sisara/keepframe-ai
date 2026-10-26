import { strict as assert } from "node:assert";
import { test } from "node:test";
import {
  failureText,
  fmtAgo,
  fmtDuration,
  isLive,
  runFraction,
  runOrdinals,
  runPhase,
  runsSignature,
  runTone,
  shortId,
  tally,
  typicalDurationS,
} from "./runs.ts";

test("status maps to the four pill tones", () => {
  assert.equal(runTone("running"), "running");
  assert.equal(runTone("starting"), "running");
  assert.equal(runTone("worker_done"), "running");
  assert.equal(runTone("awaiting_approval"), "waiting");
  assert.equal(runTone("succeeded"), "done");
  assert.equal(runTone("failed"), "failed");
  assert.equal(isLive("awaiting_approval"), true);
  assert.equal(isLive("succeeded"), false);
  assert.equal(isLive(undefined), false);
});

test("phase follows the stamps each pass writes", () => {
  assert.equal(runPhase({ status: "starting" }), 0);
  assert.equal(runPhase({ status: "running" }), 1);
  assert.equal(runPhase({ status: "running", worker_ended_at: "2026-10-25T19:28:54Z" }), 2);
  assert.equal(runPhase({ status: "awaiting_approval" }), 2);
  assert.equal(runPhase({ status: "succeeded" }), -1);
});

test("progress fraction only when both counts are known", () => {
  assert.equal(runFraction({ photos_processed: 139, n_photos: 278 }), 0.5);
  assert.equal(runFraction({ photos_processed: 400, n_photos: 278 }), 1);
  assert.equal(runFraction({ n_photos: 278 }), undefined);
  assert.equal(runFraction({ photos_processed: 3, n_photos: 0 }), undefined);
});

test("runs are numbered per event, oldest first", () => {
  const o = runOrdinals([
    { run_id: "c", event_id: "e1", started_at: "2026-10-25T00:00:00Z" },
    { run_id: "a", event_id: "e1", started_at: "2026-10-23T00:00:00Z" },
    { run_id: "x", event_id: "e2", started_at: "2026-10-24T00:00:00Z" },
    { run_id: "b", event_id: "e1", started_at: "2026-10-24T00:00:00Z" },
  ]);
  assert.deepEqual(o, { a: 1, b: 2, c: 3, x: 1 });
});

test("short ids are six upper-case characters without the prefix", () => {
  assert.equal(shortId("run_01M5T8JMKP3CFY4CXEPE4GA31B"), "4GA31B");
  assert.equal(shortId("abc"), "ABC");
});

test("durations are compact and zero-padded", () => {
  assert.equal(fmtDuration(48.4), "48s");
  assert.equal(fmtDuration(247.5), "4m 08s");
  assert.equal(fmtDuration(3900), "1h 05m");
  assert.equal(fmtDuration(undefined), "");
  assert.equal(fmtDuration(-1), "");
});

test("relative time", () => {
  const now = Date.parse("2026-10-26T12:00:00Z");
  assert.equal(fmtAgo("2026-10-26T11:59:40Z", now), "just now");
  assert.equal(fmtAgo("2026-10-26T11:55:00Z", now), "5 min ago");
  assert.equal(fmtAgo("2026-10-26T09:00:00Z", now), "3 h ago");
  assert.equal(fmtAgo("2026-10-25T12:00:00Z", now), "yesterday");
  assert.equal(fmtAgo("2026-10-22T12:00:00Z", now), "4 days ago");
  assert.equal(fmtAgo(undefined, now), "");
});

test("typical duration is the median of finished runs", () => {
  assert.equal(typicalDurationS([]), undefined);
  assert.equal(
    typicalDurationS([
      { status: "succeeded", duration_s: 100 },
      { status: "failed", duration_s: 5 },
      { status: "succeeded", duration_s: 300 },
      { status: "succeeded", duration_s: 200 },
    ]),
    200,
  );
  assert.equal(typicalDurationS([{ status: "succeeded", duration_s: 100 }, { status: "succeeded", duration_s: 200 }]), 150);
});

test("tally fills missing counts with zero", () => {
  assert.deepEqual(tally({ keep: 3, reject: 1 }), { keep: 3, review: 0, reject: 1, total: 4 });
  assert.deepEqual(tally(undefined), { keep: 0, review: 0, reject: 0, total: 0 });
});

test("runs signature changes when a status changes", () => {
  const a = runsSignature([{ run_id: "r", status: "running", updated_at: "1" }]);
  const b = runsSignature([{ run_id: "r", status: "succeeded", updated_at: "2" }]);
  assert.notEqual(a, b);
  assert.equal(runsSignature(undefined), "");
});

test("failure text is short and readable", () => {
  assert.equal(failureText({ error: "worker failed" }), "Worker failed");
  assert.equal(failureText({ error_code: "TimeoutError" }), "TimeoutError");
  assert.equal(failureText({ error_code: "error" }), "Stopped before finishing");
  assert.equal(failureText({ error_code: "error", error: "worker failed" }), "Worker failed");
  assert.equal(failureText({}), "");
});

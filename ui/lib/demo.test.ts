import { strict as assert } from "node:assert";
import { test } from "node:test";
import { applyAnswerToGroup, demoFile, withBase } from "./demo.ts";
import type { Group } from "./types.ts";

test("demoFile maps every endpoint the pages call", () => {
  assert.equal(demoFile("/health"), "health.json");
  assert.equal(demoFile("/events", { limit: 100 }), "events.json");
  assert.equal(demoFile("/runs", { limit: 50, event_id: "ev_01" }), "runs.json");
  assert.equal(demoFile("/runs/run_demo02"), "runs/run_demo02.json");
  assert.equal(demoFile("/runs/run_demo02/agent"), "runs/run_demo02/agent.json");
  assert.equal(demoFile("/runs/run_demo02/trace", { limit: 500 }), "traces/run_demo02.json");
  assert.equal(demoFile("/runs/run_demo02/trace", { group_id: "gx_1", limit: 500 }), "traces/run_demo02/gx_1.json");
  assert.equal(demoFile("/events/ev_01/groups", { limit: 24, cursor: "o24" }), "groups/ev_01.json");
  assert.equal(demoFile("/events/ev_01/groups", { group_id: "gx_1" }), "groups/ev_01/gx_1.json");
  assert.equal(demoFile("/approvals", { limit: 100 }), "approvals.json");
  assert.equal(demoFile("/bench"), "bench.json");
  assert.equal(demoFile("/runs/x/unknown"), null);
});

test("demoFile never lets a path escape the demo folder", () => {
  assert.equal(demoFile("/runs/..%2F..%2Fsecret"), "runs/..%2F..%2Fsecret.json");
  assert.equal(demoFile("/events/ev_01/groups", { group_id: "../../x" }), "groups/ev_01/..%2F..%2Fx.json");
});

test("withBase prefixes demo assets only", () => {
  assert.equal(withBase("/demo/img/t/ph_1.webp", "/keepframe-ai"), "/keepframe-ai/demo/img/t/ph_1.webp");
  assert.equal(withBase("/demo/img/t/ph_1.webp", ""), "/demo/img/t/ph_1.webp");
  assert.equal(withBase("https://example.com/demo/x", "/b"), "https://example.com/demo/x");
});

test("an answered close call takes the visitor's verdicts and leaves the queue", () => {
  const g = {
    group_id: "gx_1",
    pending_approval: true,
    verdict_counts: { keep: 1, review: 1 },
    members: [
      { photo_id: "a", proxy_url: "", verdict: "keep", decided_by: "agent", is_best: true },
      { photo_id: "b", proxy_url: "", verdict: "review", decided_by: "agent", is_best: false },
    ],
  } as unknown as Group;
  const out = applyAnswerToGroup(g, { approval_id: "gx_1", group_id: "gx_1", verdicts: { a: "reject", b: "keep" }, note: "eyes", at: "t" });
  assert.equal(out.pending_approval, false);
  assert.deepEqual(out.verdict_counts, { reject: 1, keep: 1 });
  assert.equal(out.members[1].decided_by, "human");
  assert.equal(out.members[1].decision_source, "ui");
  assert.equal(applyAnswerToGroup(g, undefined), g);
});

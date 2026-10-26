import { strict as assert } from "node:assert";
import { test } from "node:test";
import { computeSeconds, groupBySeq, pivotExamples, pivotTitle, runStages, toolMix, toolWord } from "./overview.ts";
import type { Pivot, TraceLine } from "./types.ts";

const done = {
  status: "succeeded",
  started_at: "2026-10-25T19:28:39.030Z",
  agent_started_at: "2026-10-25T19:28:54.022Z",
  agent_ended_at: "2026-10-25T19:32:46.519Z",
  ended_at: "2026-10-25T19:32:46.519Z",
  worker_wall_s: 13.229,
  agent_wall_s: 16.22,
  duration_s: 247.5,
};

test("finished run: the four stages add up to the run's duration", () => {
  const st = runStages(done);
  assert.deepEqual(st.map((s) => s.key), ["sync", "score", "agent", "waiting", "done"]);
  const sum = st.reduce((a, s) => a + (s.s ?? 0), 0);
  assert.ok(Math.abs(sum - 247.489) < 0.01, `sum ${sum}`);
  assert.ok(Math.abs((st[0].s ?? 0) - 1.763) < 0.01);
  assert.equal(st[1].s, 13.229);
  assert.equal(st[2].s, 16.22);
  assert.ok(Math.abs((st[3].s ?? 0) - 216.277) < 0.01);
  assert.ok(st.every((s) => s.state === "done"));
});

test("live run waiting on a person marks Waiting active and counts the wait up to now", () => {
  const now = Date.parse(done.agent_started_at) + 100_000;
  const st = runStages({ ...done, status: "awaiting_approval", agent_ended_at: undefined, ended_at: undefined }, now);
  assert.deepEqual(st.map((s) => s.state), ["done", "done", "done", "active", "todo"]);
  assert.ok(Math.abs((st[3].s ?? 0) - (100 - 16.22)) < 0.01, `waiting ${st[3].s}`);
  // Without the agent's own work time the wait runs from the pause stamp; with neither it stays unknown.
  const paused = runStages({ status: "awaiting_approval", started_at: done.started_at, agent_started_at: done.agent_started_at, paused_at: done.agent_started_at }, now);
  assert.ok(Math.abs((paused[3].s ?? 0) - 100) < 0.01);
  assert.equal(runStages({ status: "awaiting_approval", started_at: done.started_at }, now)[3].s, undefined);
});

test("run still scoring: Score is active", () => {
  const st = runStages({ status: "running", started_at: done.started_at });
  assert.deepEqual(st.map((s) => s.state), ["done", "active", "todo", "todo", "todo"]);
  assert.equal(st[0].s, undefined);
});

test("missing stamps never produce numbers", () => {
  const st = runStages({ status: "succeeded" });
  assert.ok(st.every((s) => s.s === undefined));
});

test("compute seconds is worker + agent work", () => {
  assert.ok(Math.abs((computeSeconds(done) ?? 0) - 29.449) < 1e-9);
  assert.equal(computeSeconds({}), undefined);
});

const lines: TraceLine[] = [
  { run_id: "r", seq: 1, type: "tool_result", tool: "next_work_item", result: { group_id: "g1" } },
  { run_id: "r", seq: 2, type: "tool_call", tool: "score_sharpness" },
  { run_id: "r", seq: 3, type: "tool_call", tool: "pick_best_in_cluster", args: { group_id: "g1" } },
  { run_id: "r", seq: 4, type: "tool_result", tool: "next_work_item", result: { group_id: "g2" } },
  { run_id: "r", seq: 5, type: "tool_call", tool: "score_sharpness" },
  { run_id: "r", seq: 6, type: "tool_call", tool: "score_sharpness" },
];

test("each line is attributed to the burst being worked on", () => {
  const g = groupBySeq(lines);
  assert.equal(g.get(2), "g1");
  assert.equal(g.get(5), "g2");
});

test("pivot examples prefer one of each kind, with their group", () => {
  const p = (kind: string, seq: number): Pivot => ({ kind, policy: "P", summary: kind, result_seq: seq, reasoning_seqs: [], call_seq: seq + 1 });
  const ex = pivotExamples([p("small_margin_to_human", 2), p("small_margin_to_human", 3), p("sharpness_rescore", 5)], lines);
  assert.deepEqual(ex.map((e) => e.pivot.kind), ["sharpness_rescore", "small_margin_to_human"]);
  assert.deepEqual(ex.map((e) => e.groupId), ["g2", "g1"]);
});

test("tool mix counts calls, largest first, with readable names", () => {
  assert.deepEqual(toolMix(lines), [
    { tool: "score_sharpness", n: 3 },
    { tool: "pick_best_in_cluster", n: 1 },
  ]);
  assert.equal(toolWord("pick_best_in_cluster"), "Rank burst");
  assert.equal(toolWord("new_tool"), "New tool");
  assert.equal(pivotTitle("unknown"), "Changed its plan");
});

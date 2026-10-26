import { strict as assert } from "node:assert";
import { test } from "node:test";
import { escalationText, fmtPct1, fmtSig, framesText, framesToShow, runTimeText, tallyText } from "./format.ts";

test("cost keeps three significant figures and rounds half up", () => {
  assert.equal(fmtSig(0.001055), "0.00106");
  assert.equal(fmtSig(0.001076), "0.00108");
  assert.equal(fmtSig(undefined), "—");
});

test("cpu truncates to one decimal", () => {
  assert.equal(fmtPct1(99.95), "99.9");
  assert.equal(fmtPct1(97.89), "97.8");
});

test("group frame count reads on its own and handles zero span", () => {
  assert.equal(framesText({ size: 12, span_s: 0 }), "12 frames, same second");
  assert.equal(framesText({ size: 3, span_s: 1.24 }), "3 frames over 1.2 s");
  assert.equal(framesText({ size: 1 }), "1 frame");
});

test("escalations are labelled as policy escalations next to the review count", () => {
  assert.equal(escalationText({ escalation_count: 22, verdict_counts: { review: 10 } }), ", 22 policy escalations, 10 sent to review");
  assert.equal(
    escalationText({ escalations: { worker: 22, agent: 3 }, escalation_count: 99, verdict_counts: { review: 10 } }),
    ", 22 flagged by the worker, 3 asked by the agent, 10 sent to review",
  );
  assert.equal(escalationText({ escalations: { agent: 10 } }), ", 10 asked by the agent");
  assert.equal(escalationText({ escalation_count: 1 }), ", 1 policy escalation");
  assert.equal(escalationText({ escalation_count: 0, verdict_counts: { review: 0 } }), ", 0 sent to review");
  assert.equal(escalationText({}), "");
});

test("tally text for link names", () => {
  assert.equal(tallyText({ keep: 1, reject: 1, review: 0 }), ", 1 keep, 1 reject");
  assert.equal(tallyText(undefined), "");
});

test("run time clause never renders an empty or negative duration", () => {
  const clock = (iso?: string) => (iso ?? "").slice(11, 16);
  assert.equal(runTimeText({ status: "succeeded", duration_s: 59 }, clock), "took 59 s");
  assert.equal(runTimeText({ status: "succeeded", duration_s: 812 }, clock), "took 13 min 32 s");
  // Paused E07 shape: no duration from the API (ended_at before started_at).
  assert.equal(runTimeText({ status: "awaiting_approval", started_at: "2026-10-05T17:10:28Z", paused_at: "2026-10-05T17:11:17Z" }, clock), "waiting for approval since 17:11");
  assert.equal(runTimeText({ status: "running", started_at: "2026-10-05T17:10:28Z" }, clock), "running since 17:10");
  assert.equal(runTimeText({ status: "succeeded" }, clock), "");
});

test("large groups show the pick, reviews and the top 12; small groups show everything", () => {
  const mk = (n: number) =>
    Array.from({ length: n }, (_, i) => ({
      photo_id: `p${i}`,
      rank: i + 1,
      is_best: i === 0,
      verdict: i === 0 ? "keep" : i === 40 ? "review" : "reject",
    }));
  assert.equal(framesToShow(mk(24)).length, 24);
  const big = framesToShow(mk(75));
  assert.equal(big.length, 13); // top 12 (incl. the pick) + the review at rank 41
  assert.equal(big[0].photo_id, "p0");
  assert.ok(big.some((m) => m.photo_id === "p40"));
  assert.ok(framesToShow(mk(75), "p70").some((m) => m.photo_id === "p70"));
});

test("agent host comes only from the run's own fields", async () => {
  const { agentHostText } = await import("./format.ts");
  assert.equal(agentHostText({ agent_host: "agentcore" }), "AgentCore Runtime");
  assert.equal(agentHostText({ agent_host: "fargate", run_kind: "deployed" }), "Fargate task");
  assert.equal(agentHostText({ run_kind: "agentcore" }), "AgentCore Runtime");
  assert.equal(agentHostText({ run_kind: "local" }), "local");
  assert.equal(agentHostText({ run_kind: "deployed" }), ""); // no guessing
  assert.equal(agentHostText({}), "");
});

test("live agent text: resuming while RUN# still says paused", async () => {
  const { agentLiveText, resumeResultText } = await import("./format.ts");
  assert.equal(agentLiveText("awaiting_approval", { status: "running" }), "resuming…");
  assert.equal(agentLiveText("running", { status: "running" }), "running");
  assert.equal(agentLiveText("awaiting_approval", { status: "paused" }), "paused, waiting for a person");
  assert.equal(agentLiveText("running", { status: "unknown" }), "");
  assert.equal(agentLiveText("running", null), "");
  assert.equal(resumeResultText({ via: "agentcore", status: "paused" }), "The agent resumed and stopped at the next close call.");
  assert.equal(resumeResultText({ via: "api-direct", status: 200 }), "Verdicts recorded.");
  assert.equal(resumeResultText({ via: "http", status: 200 }), "The agent is resuming.");
});

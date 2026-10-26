import { strict as assert } from "node:assert";
import { test } from "node:test";
import { approvalsSourceText, decisionLine, isPerson, lastAnswerText } from "./decision.ts";

test("agent verdicts show their stored confidence", () => {
  assert.equal(decisionLine("agent", 0.5), "50% confident, by the agent");
  assert.equal(decisionLine(undefined, 0.86), "86% confident, by the agent");
});

test("scripted shows the agent's confidence, never a person", () => {
  assert.equal(decisionLine("scripted", 0.5), "Answered automatically (test run), agent confidence 50%");
  assert.equal(decisionLine("scripted", undefined), "Answered automatically (test run)");
  assert.equal(decisionLine("unknown", undefined), "confirmed outside the agent, source not recorded");
  assert.equal(decisionLine("ui", 0.6), "confirmed by a person in the review desk, agent confidence 60%");
});

test("only explicit person channels count as human agreement", () => {
  assert.deepEqual(
    ["ui", "file", "http", "scripted", "lead_test", "unknown", "agent"].map((s) => isPerson(s as never)),
    [true, true, true, false, false, false, false],
  );
  assert.equal(decisionLine("lead_test", 0.41), "Team mechanism test, not a photographer judgement, agent confidence 41%");
});

test("run banner wording for approvals_source", () => {
  assert.equal(approvalsSourceText("scripted"), "approvals answered automatically (test run)");
  assert.equal(approvalsSourceText(undefined), null);
});

test("run banner calls out a team mechanism test answer, never a person answer", () => {
  assert.equal(lastAnswerText("lead_test"), "last answer: team mechanism test");
  assert.equal(lastAnswerText("scripted"), "last answer: answered automatically (test run)");
  assert.equal(lastAnswerText("ui"), null);
  assert.equal(lastAnswerText(undefined), null);
  assert.equal(isPerson("lead_test"), false);
});

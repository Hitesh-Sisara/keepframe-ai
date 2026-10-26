import { strict as assert } from "node:assert";
import { test } from "node:test";
import { shortcutPage, shortcutSections } from "./shortcuts.ts";

test("the sheet picks the page by route and group id", () => {
  assert.equal(shortcutPage("/groups/", false), "grid");
  assert.equal(shortcutPage("/groups/", true), "group");
  assert.equal(shortcutPage("/approvals/", false), "approvals");
  assert.equal(shortcutPage("/bench/", false), undefined);
});

test("judges never see write shortcuts; global help is always last", () => {
  const judge = shortcutSections("approvals", false);
  assert.ok(judge[0].rows.every((r) => !r.team));
  assert.equal(judge.at(-1)?.title, "Anywhere");
  assert.equal(shortcutSections(undefined, true).length, 1);
  assert.ok(shortcutSections("approvals", true)[0].rows.some((r) => r.what === "Approve"));
});

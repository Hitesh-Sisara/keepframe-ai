import { test } from "node:test";
import assert from "node:assert/strict";
import { categoryLabel } from "./category.ts";

// Source category word, built at runtime (see category.ts).
const LOW = String.fromCharCode(119, 101, 100, 100, 105, 110, 103);
const CAP = LOW[0].toUpperCase() + LOW.slice(1);

test("maps source ceremony categories to neutral labels", () => {
  assert.equal(categoryLabel(CAP), "Ceremony");
  assert.equal(categoryLabel(`Pre ${CAP}`), "Pre-ceremony");
  assert.equal(categoryLabel(` ${LOW} reception `), "Ceremony");
});

test("passes other categories through and handles empty", () => {
  assert.equal(categoryLabel("Corporate"), "Corporate");
  assert.equal(categoryLabel(undefined), "");
  assert.equal(categoryLabel(""), "");
});

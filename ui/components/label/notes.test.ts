import assert from "node:assert/strict";
import { test } from "node:test";
import { byPhotoId, isUnsure, toggleUnsure } from "./notes.ts";

test("U adds and removes the unsure token without touching the rest of the note", () => {
  assert.equal(toggleUnsure(""), "unsure");
  assert.equal(toggleUnsure("artistic"), "unsure; artistic");
  assert.equal(toggleUnsure("unsure; artistic"), "artistic");
  assert.equal(toggleUnsure("artistic, unsure"), "artistic");
  assert.equal(toggleUnsure(toggleUnsure("eyes half open")), "eyes half open");
});

test("unsure is matched as a word, not inside other words", () => {
  assert.equal(isUnsure("Unsure"), true);
  assert.equal(isUnsure("a bit unsure, maybe"), true);
  assert.equal(isUnsure("unsurely"), false);
  assert.equal(toggleUnsure("unsurely"), "unsure; unsurely");
});

test("burst frames sort by photo id numerically", () => {
  const ids = ["p_0010", "p_0002", "p_0005", "p_0001"].map((photo_id) => ({ photo_id }));
  assert.deepEqual(ids.sort(byPhotoId).map((x) => x.photo_id), ["p_0001", "p_0002", "p_0005", "p_0010"]);
});

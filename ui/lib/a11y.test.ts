import { strict as assert } from "node:assert";
import { test } from "node:test";
import { frameAlt, ignoreShortcut, modalOpen, routeTitle, trapIndex } from "./a11y.ts";

test("focus trap wraps at both ends and enters from outside", () => {
  assert.equal(trapIndex(2, 3, false), 0);
  assert.equal(trapIndex(0, 3, true), 2);
  assert.equal(trapIndex(1, 3, false), null);
  assert.equal(trapIndex(-1, 3, false), 0);
  assert.equal(trapIndex(-1, 3, true), 2);
  assert.equal(trapIndex(0, 0, false), null);
});

const docWith = (open: boolean) => ({ querySelector: () => (open ? ({} as Element) : null) });

test("page shortcuts stand down while a modal is open, in text fields, and with modifiers", () => {
  const key = (o: Partial<KeyboardEvent> & { tag?: string }) => ({ metaKey: false, ctrlKey: false, altKey: false, target: { tagName: o.tag ?? "BODY" } as unknown as EventTarget, ...o });
  assert.equal(ignoreShortcut(key({}), docWith(false) as unknown as Document), false);
  assert.equal(ignoreShortcut(key({}), docWith(true) as unknown as Document), true);
  assert.equal(ignoreShortcut(key({ tag: "INPUT" }), docWith(false) as unknown as Document), true);
  assert.equal(ignoreShortcut(key({ metaKey: true }), docWith(false) as unknown as Document), true);
  assert.equal(modalOpen(undefined), false);
});

test("frame alt text follows one convention", () => {
  assert.equal(frameAlt({ n: 3, of: 6, verdict: "keep", best: true }), "Frame 3 of 6, keep, best pick");
  assert.equal(frameAlt({ n: 2, verdict: "reject" }), "Frame 2, reject");
  assert.equal(frameAlt({}), "Frame");
});

test("every route gets its own document title", () => {
  assert.equal(routeTitle("/"), "Events · KeepFrame");
  assert.equal(routeTitle("/events/"), "Event · KeepFrame");
  assert.equal(routeTitle("/groups/"), "Groups · KeepFrame");
  assert.equal(routeTitle("/approvals/"), "Approvals · KeepFrame");
  assert.equal(routeTitle("/bench/"), "Benchmark · KeepFrame");
  assert.equal(routeTitle("/label/"), "Label · KeepFrame");
  assert.equal(routeTitle("/x/"), "KeepFrame · Review desk");
});

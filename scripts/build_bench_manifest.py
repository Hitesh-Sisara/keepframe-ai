#!/usr/bin/env python3
"""Build bench/manifest_5k.jsonl: N events x K processed rows, deterministic sha256 sample.

Rows are manifest rows trimmed to opaque ids + keys + dims (no names, no URLs, no face data) so the
file can be committed. Events: the K-largest selected events (data/selected_events.json).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KEEP = ("photo_id", "event_id", "processed_key", "width", "height", "clicked_at", "camera")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=int, default=5)
    ap.add_argument("--per-event", type=int, default=1000)
    ap.add_argument("--seed", default="bench-5k-v1")
    ap.add_argument("--out", default="bench/manifest_5k.jsonl")
    a = ap.parse_args()
    from keepframe.io.manifest import read_manifest_dicts

    sel = json.loads((ROOT / "data/selected_events.json").read_text())["events"]
    sel = sorted(sel, key=lambda e: (-e["n_kept"], e["e_index"]))[: a.events]
    out = []
    for e in sorted(sel, key=lambda e: e["e_index"]):
        rows = [r for r in read_manifest_dicts(e["id"]) if r.get("processed_key")]
        rows.sort(key=lambda r: hashlib.sha256(f"{a.seed}:{r['photo_id']}".encode()).hexdigest())
        pick = sorted(rows[: a.per_event], key=lambda r: (r["clicked_at"] or "", r["photo_id"]))
        out += [{k: r.get(k) for k in KEEP} | {"bench_event": e["e_index"]} for r in pick]
        print(e["e_index"], e.get("category"), len(pick), file=sys.stderr)
    Path(a.out).write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in out))
    print(f"wrote {len(out)} rows to {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

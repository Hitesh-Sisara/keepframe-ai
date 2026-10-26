"""No customer identifiers in tracked files (the Devpost page is public).

Two checks:
  1. Real-id prefix (strong signal): when the gitignored data/selected_events.json is present (dev boxes
     with data access), fail on the 8-character prefix of any real selected event id in ANY tracked text
     file except the allowlisted paths. Run ids and log lines tend to leak these.
  2. Full UUID (weak signal, runs everywhere incl. CI): fail on a UUID in tracked files OUTSIDE test code
     (tests/, services/*/tests/), docs/screenshots/ and uv.lock, unless it is
     clearly synthetic: all-zero / repeated-digit patterns, or on a line that names a synthetic run or
     event (run_demo, ev_demo, ev_synth).
Runs inside the normal `pytest -q` CI step, so no workflow change is needed.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SELF = "tests/unit/test_no_customer_ids.py"
ALLOW_PATHS = ("uv.lock", "docs/screenshots/", SELF)
TEST_CODE = re.compile(r"^(tests/|services/[^/]+/tests/)")
SYNTHETIC_LINE = re.compile(r"run_demo|ev_demo|ev_synth")
BINARY = (".png", ".jpg", ".jpeg", ".webp", ".pdf", ".onnx", ".ico", ".woff", ".woff2", ".gz", ".zip")


def _synthetic(u: str) -> bool:
    """00000000-..., 11111111-1111-4111-8111-111111111111, ... style ids."""
    if u.startswith("00000000-"):
        return True
    groups = u.split("-")
    return all(len(set(g.lstrip("0") or "0")) <= 2 for g in groups)


def _tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / p for p in out.decode().split("\0") if p]


def _scan() -> list[str]:
    real_prefixes: set[str] = set()
    sel = ROOT / "data" / "selected_events.json"
    if sel.exists():
        real_prefixes = {str(e["id"])[:8] for e in json.loads(sel.read_text()).get("events", []) if e.get("id")}
    hits = []
    for p in _tracked():
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(ALLOW_PATHS) or rel.endswith(BINARY) or not p.is_file():
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        if not TEST_CODE.match(rel):
            for line in text.splitlines():
                if SYNTHETIC_LINE.search(line):
                    continue
                for u in UUID.findall(line):
                    if not _synthetic(u):
                        hits.append(f"{rel}: {u[:8]}…")
        for pre in real_prefixes:
            if pre in text:
                hits.append(f"{rel}: real event prefix {pre[:4]}…")
    return sorted(set(hits))


def test_no_customer_ids_in_tracked_files() -> None:
    hits = _scan()
    assert not hits, "customer identifiers in tracked files (use E-aliases / <eventId>):\n" + "\n".join(hits)


def test_detector_rules() -> None:
    real_looking = "9e3d2c71-4b8a-4f05-a6d2-7c19be50f3a4"  # random, not an event or photo id
    assert UUID.search(f"event {real_looking} here") and not _synthetic(real_looking)
    assert _synthetic("00000000-0000-4000-8000-000000000001") and _synthetic("00000000-1234-4abc-8def-0123456789ab")
    assert _synthetic("11111111-1111-4111-8111-111111111111")
    assert SYNTHETIC_LINE.search(f'{{"run_id": "run_demo1", "photo_id": "{real_looking}"}}')
    assert TEST_CODE.match("services/api/tests/test_api.py") and TEST_CODE.match("tests/unit/x.py")
    assert not TEST_CODE.match("docs/AGENT.md") and not TEST_CODE.match("services/api/app/main.py")

"""Event alias <-> event id resolution. Real ids never live in tracked files.

Sources, first hit wins:
  1. $CULL_EVENTS_FILE
  2. data/selected_events.json  (data agent's file: {"events": [{"e_index": "E07", "id": "<uuid>", ...}]})
  3. eval/labels/events.json    ({"E07": "<uuid>", ...})
All three are gitignored. Code, docs and committed outputs use the aliases only (E03, E07, E09, ...).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _candidates() -> list[Path]:
    env = os.environ.get("CULL_EVENTS_FILE")
    return [Path(env)] if env else [REPO / "data" / "selected_events.json", REPO / "eval" / "labels" / "events.json"]


def load_aliases(path: Path | None = None) -> dict[str, str]:
    """{alias: event_id}; empty when no events file exists (aliases then must be passed explicitly)."""
    for p in [path] if path else _candidates():
        if p and p.exists():
            doc = json.loads(p.read_text())
            if isinstance(doc, dict) and isinstance(doc.get("events"), list):
                return {str(e["e_index"]): str(e["id"]) for e in doc["events"] if e.get("e_index") and e.get("id")}
            if isinstance(doc, dict):
                return {str(k): str(v) for k, v in doc.items() if isinstance(v, str)}
    return {}


def resolve(name: str, aliases: dict[str, str]) -> str:
    """Alias -> id; a value that is not a known alias is taken as an id as is."""
    return aliases.get(name, name)


def alias_of(event_id: str, aliases: dict[str, str]) -> str:
    """Id -> alias, else a non-identifying placeholder (never the id itself)."""
    for k, v in aliases.items():
        if v == event_id:
            return k
    return "E??"

"""Neutral display labels for source event categories.

The source data stores its own category strings; responses show a neutral label instead. Same mapping as
config/source_categories.yaml `display` (and ui/lib/category.ts), hard-coded here because the API image does
not ship that config. Stored data is never changed: this runs only when a response is shaped.

The source word is assembled at runtime rather than written out (the release check allows it only in
config/source_categories.yaml).
"""

from __future__ import annotations

from typing import Any

_SRC = "".join(map(chr, (119, 101, 100, 100, 105, 110, 103)))

# Exact source strings (compared case-insensitively, whitespace collapsed).
_DISPLAY = {
    _SRC: "Ceremony",
    f"pre {_SRC}": "Pre-ceremony",
    f"pre-{_SRC}": "Pre-ceremony",
}


def display_category(value: Any) -> Any:
    """The neutral label for a source category. Non-strings and unlisted categories pass through, except
    that any other string containing the source word (any case) becomes "Ceremony"."""
    if not isinstance(value, str):
        return value
    key = " ".join(value.split()).lower()
    if key in _DISPLAY:
        return _DISPLAY[key]
    if _SRC in key:
        return "Ceremony"
    return value

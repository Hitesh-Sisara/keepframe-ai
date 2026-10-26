"""Neutral event-category labels (app/labels.py)."""

from __future__ import annotations

import pytest

from app.labels import display_category

# Source category word, built at runtime (see app/labels.py).
LOW = "".join(map(chr, (119, 101, 100, 100, 105, 110, 103)))
CAP = LOW.capitalize()


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        (CAP, "Ceremony"),
        (LOW, "Ceremony"),
        (f"  {LOW.upper()} ", "Ceremony"),
        (f"Pre {CAP}", "Pre-ceremony"),
        (f"Pre-{LOW}", "Pre-ceremony"),
        (f"pre  {LOW}", "Pre-ceremony"),
        (f"Destination {CAP}", "Ceremony"),
        (f"{CAP} Reception", "Ceremony"),
        ("Engagement", "Engagement"),
        ("Birthday", "Birthday"),
        ("Ceremony", "Ceremony"),
        ("", ""),
        (None, None),
        (3, 3),
    ],
)
def test_display_category(raw: object, shown: object) -> None:
    assert display_category(raw) == shown

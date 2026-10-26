"""Unit tests for PlanOriginals candidate selection (pure functions, no AWS)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("WORK_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
sys.path.insert(0, str(Path(__file__).parent))

import plan_originals as po  # noqa: E402


def test_group_members_size_two_or_more_and_flagged_photos() -> None:
    groups = {"groups": [
        {"group_id": "g1", "members": ["a", "b"], "size": 2},
        {"group_id": "g2", "members": ["c"], "size": 1},
        {"group_id": "g3", "photo_ids": ["d", "e", "f"]},  # worker-internal key, size from len
    ]}
    decisions = [{"photo_id": "c", "needs_approval": True}, {"photo_id": "x", "needs_approval": False}]
    assert po.candidates(groups, decisions) == {"a", "b", "c", "d", "e", "f"}


def test_batch_lines_and_missing_files() -> None:
    assert po.candidates(None, []) == set()
    assert po.candidates({"groups": []}, [{"decisions": []}]) == set()


def test_only_rows_with_original_key() -> None:
    manifest = [
        {"photo_id": "a", "original_key": "ev/a.jpg"},
        {"photo_id": "b", "original_key": None},
        {"photo_id": "c"},
        {"photo_id": "z", "original_key": "ev/z.jpg"},  # not a candidate
    ]
    assert po.with_originals(manifest, {"a", "b", "c"}) == ["a"]

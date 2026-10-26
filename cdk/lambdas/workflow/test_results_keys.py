"""Summarise / PlanOriginals read results/<E>/<runId>/... first, then the unversioned copy (no AWS: S3 is faked)."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("WORK_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
sys.path.insert(0, str(Path(__file__).parent))

import plan_originals  # noqa: E402
import runs  # noqa: E402
import summarise  # noqa: E402


class _NoSuchKey(Exception):
    pass


class FakeS3:
    exceptions = type("E", (), {"NoSuchKey": _NoSuchKey})

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.gets: list[str] = []
        self.puts: dict[str, bytes] = {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        self.gets.append(Key)
        if Key not in self.objects:
            raise _NoSuchKey(Key)
        return {"Body": _Body(self.objects[Key])}

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if Key not in self.objects:
            err = Exception("404")
            err.response = {"Error": {"Code": "404"}}  # type: ignore[attr-defined]
            raise err
        return {}

    def put_object(self, Bucket: str, Key: str, Body: bytes, **_: Any) -> None:  # noqa: N803
        self.puts[Key] = Body


class _Body(io.BytesIO):
    def iter_lines(self) -> Any:
        return iter(self.getvalue().splitlines())


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Any:
    def make(objects: dict[str, bytes]) -> tuple[FakeS3, list[dict[str, Any]]]:
        fake, updates = FakeS3(objects), []
        for mod in (runs, plan_originals):
            monkeypatch.setattr(mod, "_s3", fake)
        for mod in (summarise, plan_originals):
            monkeypatch.setattr(mod, "update_run", lambda e, r, f: updates.append(f))
        monkeypatch.setattr(summarise, "get_run_status", lambda e, r: "running")
        return fake, updates
    return make


def test_result_keys_rule() -> None:
    assert runs.result_keys("E", "run_1", "summary.json") == ("results/E/run_1/summary.json", "results/E/summary.json")


def test_summarise_prefers_versioned(s3: Any) -> None:
    fake, upd = s3({"results/E/run_1/summary.json": json.dumps({"n_photos": 5}).encode(),
                    "results/E/summary.json": json.dumps({"n_photos": 9}).encode()})
    summarise.handler({"event_id": "E", "run_id": "run_1"}, None)
    assert upd[-1]["n_photos"] == 5 and upd[-1]["summary_key"] == "results/E/run_1/summary.json"
    assert fake.gets == ["results/E/run_1/summary.json"]


def test_summarise_falls_back_to_unversioned(s3: Any) -> None:
    _, upd = s3({"results/E/summary.json": json.dumps({"n_photos": 9}).encode()})
    summarise.handler({"event_id": "E", "run_id": "run_old"}, None)
    assert upd[-1]["n_photos"] == 9 and upd[-1]["summary_key"] == "results/E/summary.json"


def test_summarise_missing_both(s3: Any) -> None:
    _, upd = s3({})
    summarise.handler({"event_id": "E", "run_id": "run_1"}, None)
    assert upd[-1]["summary_missing"] is True and "summary_key" not in upd[-1]


def _groups(*members: list[str]) -> bytes:
    return json.dumps({"groups": [{"members": m, "size": len(m)} for m in members]}).encode()


MANIFEST = b"\n".join(json.dumps({"photo_id": p, "original_key": f"o/{p}.jpg"}).encode() for p in "abcdxy")


def test_plan_originals_prefers_versioned(s3: Any) -> None:
    fake, upd = s3({
        "manifests/E.jsonl": MANIFEST,
        "results/E/run_1/groups.json": _groups(["a", "b"]),
        "results/E/run_1/decisions.jsonl": json.dumps({"photo_id": "x", "needs_approval": True}).encode(),
        "results/E/groups.json": _groups(["c", "d"]),
        "results/E/decisions.jsonl": json.dumps({"photo_id": "y", "needs_approval": True}).encode(),
    })
    out = plan_originals.handler({"event_id": "E", "run_id": "run_1"}, None)
    assert out["count"] == 3
    assert fake.puts["originals-requests/E/run_1.txt"] == b"a\nb\nx\n"


def test_plan_originals_falls_back_per_file(s3: Any) -> None:
    # versioned groups present, decisions only in the old unversioned location
    fake, _ = s3({
        "manifests/E.jsonl": MANIFEST,
        "results/E/run_1/groups.json": _groups(["a", "b"]),
        "results/E/decisions.jsonl": json.dumps({"photo_id": "y", "needs_approval": True}).encode(),
    })
    out = plan_originals.handler({"event_id": "E", "run_id": "run_1"}, None)
    assert out["count"] == 3 and fake.puts["originals-requests/E/run_1.txt"] == b"a\nb\ny\n"


def test_plan_originals_empty_versioned_decisions_do_not_fall_back(s3: Any) -> None:
    fake, _ = s3({
        "manifests/E.jsonl": MANIFEST,
        "results/E/run_1/groups.json": _groups(["a", "b"]),
        "results/E/run_1/decisions.jsonl": b"",
        "results/E/decisions.jsonl": json.dumps({"photo_id": "y", "needs_approval": True}).encode(),
    })
    out = plan_originals.handler({"event_id": "E", "run_id": "run_1"}, None)
    assert out["count"] == 2 and fake.puts["originals-requests/E/run_1.txt"] == b"a\nb\n"


def test_plan_originals_nothing(s3: Any) -> None:
    fake, upd = s3({"manifests/E.jsonl": MANIFEST})
    out = plan_originals.handler({"event_id": "E", "run_id": "run_1"}, None)
    assert out == {"count": 0, "candidates": 0, "photo_ids_key": None} and not fake.puts

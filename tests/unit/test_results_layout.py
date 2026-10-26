"""Per-run results keys and image_sha resolution."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from keepframe.worker import run_record

DIGEST = "sha256:" + "ab" * 32


def test_result_keys_versioned_and_latest() -> None:
    assert run_record.run_results_prefix("ev1", "run_01J9ZK") == "results/ev1/run_01J9ZK/"
    assert run_record.result_keys("ev1", "r2", "summary.json") == ("results/ev1/r2/summary.json",
                                                                  "results/ev1/summary.json")


@pytest.mark.parametrize("bad", ["", "../x", "a/b", "a..b", ".hidden", "x" * 129, "sp ace"])
def test_unsafe_run_ids_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        run_record.check_run_id(bad)


def test_image_sha_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in run_record.IMAGE_SHA_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    assert run_record.image_sha() is None
    monkeypatch.setenv("IMAGE_DIGEST", f"123.dkr.ecr.ap-south-1.amazonaws.com/keepframe@{DIGEST}")
    assert run_record.image_sha() == DIGEST
    monkeypatch.setenv("CULL_IMAGE_SHA", "main-abc123")  # a tag is not a digest: ignored, next source wins
    assert run_record.image_sha() == DIGEST


class _Meta(BaseHTTPRequestHandler):
    doc: dict[str, Any] = {}

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps(self.doc).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        pass


@pytest.mark.parametrize("doc", [
    {"Image": "123.dkr.ecr.ap-south-1.amazonaws.com/keepframe:main-abc", "ImageID": DIGEST},
    {"Image": f"123.dkr.ecr.ap-south-1.amazonaws.com/keepframe@{DIGEST}", "ImageID": "sha256:" + "cd" * 32},
])
def test_image_sha_from_ecs_task_metadata(monkeypatch: pytest.MonkeyPatch, doc: dict[str, Any]) -> None:
    for k in run_record.IMAGE_SHA_ENV:
        monkeypatch.delenv(k, raising=False)
    _Meta.doc = doc
    srv = HTTPServer(("127.0.0.1", 0), _Meta)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", f"http://127.0.0.1:{srv.server_port}/v4/x")
        assert run_record.image_sha() == DIGEST
    finally:
        srv.shutdown()


def test_image_sha_metadata_unreachable_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in run_record.IMAGE_SHA_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", "http://127.0.0.1:9/v4/none")
    assert run_record.image_sha() is None

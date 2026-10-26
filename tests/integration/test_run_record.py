"""RUN# DynamoDB item written by the worker (shape the API reads; failures swallowed)."""

from __future__ import annotations

from decimal import Decimal

from keepframe.worker.run_record import run_attributes, write_run_item

SUMMARY = {"run_id": "r1", "event_id": "e1", "status": "partial", "started_at": "2026-10-05T10:00:00Z",
           "ended_at": "2026-10-05T10:01:00Z", "opencv_version": "5.0.0", "cv2_file": "/x/cv2.abi3.so",
           "opencv_hal": "kleidicv-26.03+carotene",
           "results_run_prefix": "results/e1/r1/",
           "flavor": "stock", "instance_type": "c8g.xlarge", "image_sha": None, "thresholds_sha": "a" * 64,
           "verdict_counts": {"keep": 5, "reject": 2, "review": 1}, "escalation_count": 1, "n_photos": 9,
           "n_groups": 2, "images_per_s": 3.25, "wall_s": 60.0, "failures": [{"photo_id": "p", "error": "x"}]}


class FakeTable:
    def __init__(self, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail

    def update_item(self, **kw):
        if self.fail:
            raise RuntimeError("ResourceNotFoundException")
        self.calls.append(kw)


def test_attributes_match_api_contract():
    a = run_attributes(SUMMARY)
    assert a["gsi1pk"] == "RUNS" and a["status"] == "worker_done" and a["worker_status"] == "partial"
    for k in ("run_id", "event_id", "worker_started_at", "worker_ended_at", "worker_wall_s", "worker_escalations",
              "opencv_version", "cv2_file", "opencv_hal", "flavor", "instance_type", "thresholds_sha", "verdict_counts",
              "n_photos", "n_groups"):
        assert k in a, k
    for k in ("ended_at", "wall_s", "escalation_count", "started_at"):  # owned by the store writer / if_not_exists
        assert k not in a, k
    assert a["photos_processed"] == 8 and a["worker_escalations"] == 1
    assert a["results_run_prefix"] == "results/e1/r1/" and a["results_prefix"] == "results/e1/"
    assert "image_sha" not in a  # None values are not written
    assert run_attributes({**SUMMARY, "status": "failed"})["status"] == "failed"
    solo = run_attributes(SUMMARY, agent_follows=False)
    assert solo["status"] == "succeeded" and solo["ended_at"] == SUMMARY["ended_at"]
    assert solo["decided_by"] == "worker" and solo["llm_provider"] == "none"


def test_update_item_keys_decimals_and_if_not_exists():
    t = FakeTable()
    assert write_run_item(SUMMARY, client=t) is True
    call = t.calls[0]
    assert call["Key"] == {"pk": "EVENT#e1", "sk": "RUN#r1"}
    assert call["UpdateExpression"].startswith("SET ")
    assert "#started = if_not_exists(#started, :started)" in call["UpdateExpression"]
    assert call["ExpressionAttributeNames"]["#started"] == "started_at"
    vals = call["ExpressionAttributeValues"].values()
    assert Decimal("3.25") in vals and not any(isinstance(v, float) for v in vals)


def test_failures_are_swallowed(monkeypatch):
    assert write_run_item(SUMMARY, client=FakeTable(fail=True)) is False
    monkeypatch.delenv("WORK_BUCKET", raising=False)
    assert write_run_item(SUMMARY) is False  # LOCAL_MODE: no DynamoDB
    monkeypatch.setenv("WORK_BUCKET", "b")
    monkeypatch.setenv("CULL_DISABLE_DDB", "1")
    assert write_run_item(SUMMARY) is False  # explicit switch


def test_run_kind_and_note(monkeypatch):
    from keepframe.worker.run_record import run_kind

    monkeypatch.delenv("CULL_RUN_KIND", raising=False)
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    assert run_kind() == "local"
    monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", "http://169.254.170.2/v4/x")
    assert run_kind() == "deployed"
    monkeypatch.setenv("CULL_RUN_KIND", "local_calibration")
    assert run_kind() == "local_calibration"
    t = FakeTable()
    assert write_run_item(SUMMARY, client=t, run_note="E07 rerun") is True
    c = t.calls[0]
    assert "#kind = if_not_exists(#kind, :kind)" in c["UpdateExpression"]
    assert c["ExpressionAttributeValues"][":kind"] == "local_calibration"
    assert "E07 rerun" in c["ExpressionAttributeValues"].values()

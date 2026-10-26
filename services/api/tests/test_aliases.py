"""Security review S-1: no real source event/photo id reaches a judge-token client.

The fixture below is shaped like real source data (UUID event and photo ids, worker group ids
g_<eventId>_<n>, contact-sheet keys, trace args, DUPLICATE_OF:<photoId> reason codes, a raw error carrying an
S3 key), so the sweep checks the cases the review found live. The UUIDs are generated here; none is a real id.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from tests.conftest import judge, team

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
EV = str(uuid.UUID(int=0x5EC0_0000_0000_4000_8000_0000_0000_0001))
PHOTOS = [str(uuid.UUID(int=0x5EC1_0000_0000_4000_8000_0000_0000_0000 + i)) for i in range(1, 5)]
GROUP = f"g_{EV}_1"
RUN = "run_aws_e09_20261009T060000Z"
WORKER_RUN = f"run_{EV}_20261009T050000Z"
UNKNOWN = str(uuid.UUID(int=0x5EC2_0000_0000_4000_8000_0000_0000_0099))  # in a trace only, never mapped


def _jpeg() -> bytes:
    import cv2
    import numpy as np

    ok, buf = cv2.imencode(".jpg", np.full((8, 8, 3), 128, np.uint8))
    assert ok
    return buf.tobytes()


@pytest.fixture()
def real_shaped(client: TestClient, root: Path) -> TestClient:
    """Adds a source-shaped event (exported after the two synthetic ones, so it is ev_03)."""
    from app import backend, data

    s3, ddb = root / "s3", root / "dynamodb"
    meta = {
        "event_id": EV,
        "name": "Real-looking name",
        "category": "Ceremony",
        "n_photos": 4,
        "exported_at": "2026-10-09T00:00:00Z",
    }
    (s3 / "manifests" / f"{EV}.meta.json").write_text(json.dumps(meta))
    (s3 / "manifests" / f"{EV}.jsonl").write_text("".join(json.dumps({"photo_id": p}) + "\n" for p in PHOTOS))
    for prefix in ("proxy", "sheets"):
        (s3 / prefix / EV).mkdir(parents=True, exist_ok=True)
    for p in PHOTOS:
        (s3 / "proxy" / EV / f"{p}.jpg").write_bytes(_jpeg())
    (s3 / "sheets" / EV / f"{GROUP}.jpg").write_bytes(_jpeg())
    res = s3 / "results" / EV
    (res / RUN).mkdir(parents=True)
    (res / RUN / "groups.json").write_text(
        json.dumps(
            {"groups": [{"group_id": GROUP, "photo_ids": PHOTOS, "size": 4, "span_s": 1.2}], "singletons": []}
        )
    )
    (res / RUN / "summary.json").write_text(
        json.dumps({"event_id": EV, "run_id": RUN, "opencv_hal": "carotene"})
    )
    pk = f"EVENT#{EV}"
    rows: list[dict[str, Any]] = [
        {
            "pk": pk,
            "sk": f"RUN#{RUN}",
            "gsi1pk": "RUNS",
            "run_id": RUN,
            "event_id": EV,
            "status": "awaiting_approval",
            "started_at": "2026-10-09T06:00:00Z",
            "updated_at": "2026-10-09T06:01:00Z",
            "results_run_prefix": f"results/{EV}/{RUN}/",
            "error": f"ClientError: GetObject proxy/{EV}/{PHOTOS[0]}.jpg AccessDenied",
        },
        {
            "pk": pk,
            "sk": f"RUN#{WORKER_RUN}",
            "gsi1pk": "RUNS",
            "run_id": WORKER_RUN,
            "event_id": EV,
            "status": "failed",
            "started_at": "2026-10-09T05:00:00Z",
            "error": f"no manifest for {EV}",
        },
        {
            "pk": pk,
            "sk": f"GROUP#{GROUP}",
            "group_id": GROUP,
            "event_id": EV,
            "run_id": RUN,
            "best_photo_id": PHOTOS[1],
            "ranking": [{"photo_id": p, "score": 0.9 - i / 10} for i, p in enumerate(PHOTOS)],
            "gsi3pk": "PENDING",
            "gsi3sk": f"2026-10-09T06:01:00Z#{EV}#{GROUP}",
            "approval_id": GROUP,
            "proposed": {"keep": [PHOTOS[1]], "reject": [PHOTOS[0], PHOTOS[2], PHOTOS[3]], "review": []},
            "reason": f"tie between {PHOTOS[1]} and {PHOTOS[2]}",
            "confidence": 0.5,
            "contact_sheet_key": f"sheets/{EV}/{GROUP}.jpg",
            "interrupt_id": "int-1",
        },
    ]
    for i, p in enumerate(PHOTOS):
        rows.append(
            {
                "pk": pk,
                "sk": f"PHOTO#{p}",
                "photo_id": p,
                "group_id": GROUP,
                "run_id": RUN,
                "verdict": "keep" if i == 1 else "reject",
                "decided_by": "agent",
                "confidence": 0.7,
                "reason_codes": ["KEEP_BEST_OF_GROUP"] if i == 1 else [f"DUPLICATE_OF:{PHOTOS[1]}"],
                "reason_text": f"Sharper than {PHOTOS[0]}",
                "gsi1pk": f"EVENT#{EV}#VERDICT#keep",
            }
        )
    with (ddb / "keepframe-decisions.jsonl").open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    trace = [
        {"seq": 1, "type": "tool_call", "tool": "ingest_event", "args": {"event_id": EV}},
        {
            "seq": 2,
            "type": "tool_result",
            "tool": "ingest_event",
            "result": {"photos": PHOTOS, "key": f"manifests/{EV}.jsonl"},
        },
        {
            "seq": 3,
            "type": "model_reasoning",
            "text": f"Compare {PHOTOS[1]} with {PHOTOS[2]} in {GROUP}; also {UNKNOWN}.",
        },
        {
            "seq": 4,
            "type": "tool_call",
            "tool": "request_human_approval",
            "args": {"items": [{"group_id": GROUP, "contact_sheet_key": f"sheets/{EV}/{GROUP}.jpg"}]},
        },
        {"seq": 5, "type": "error", "error": f"S3 NoSuchKey proxy/{EV}/{UNKNOWN}.jpg"},
    ]
    with (ddb / "keepframe-traces.jsonl").open("a") as f:
        for t in trace:
            f.write(json.dumps({"pk": f"RUN#{RUN}", "sk": f"{t['seq']:06d}", "run_id": RUN, **t}) + "\n")
    backend.reset_backend()
    data.clear_meta_cache()
    return client


def _judge_sweep(c: TestClient) -> dict[str, Any]:
    """Every judge-readable route, followed through the ids the API itself hands out."""
    out: dict[str, Any] = {}
    out["events"] = c.get("/events", headers=judge()).json()
    ev = next(e for e in out["events"]["items"] if e["label"] == "Event 3")["event_id"]
    out["groups"] = c.get(f"/events/{ev}/groups", headers=judge()).json()
    g = out["groups"]["items"][0]
    out["group"] = c.get(f"/events/{ev}/groups", params={"group_id": g["group_id"]}, headers=judge()).json()
    pid = g["members"][0]["photo_id"]
    out["photo"] = c.get(f"/events/{ev}/photos/{pid}", headers=judge()).json()
    out["runs"] = c.get("/runs", headers=judge()).json()
    out["runs_ev"] = c.get("/runs", params={"event_id": ev}, headers=judge()).json()
    for r in out["runs_ev"]["items"]:
        rid = r["run_id"]
        out[f"run:{rid}"] = c.get(f"/runs/{rid}", headers=judge()).json()
        out[f"trace:{rid}"] = c.get(f"/runs/{rid}/trace", headers=judge()).json()
        out[f"agent:{rid}"] = c.get(f"/runs/{rid}/agent", headers=judge()).json()
    out["trace_group"] = c.get(
        f"/runs/{RUN}/trace", params={"group_id": g["group_id"]}, headers=judge()
    ).json()
    out["bench"] = c.get("/bench", headers=judge()).json()
    out["health"] = c.get("/health", headers=judge()).json()
    # error bodies too
    out["err_raw_event"] = c.get(f"/events/{EV}/groups", headers=judge()).json()
    out["err_raw_photo"] = c.get(f"/events/{ev}/photos/{PHOTOS[0]}", headers=judge()).json()
    return out


def test_no_uuid_in_any_judge_response(real_shaped: TestClient) -> None:
    out = _judge_sweep(real_shaped)
    blob = json.dumps(out)
    found = sorted(set(UUID.findall(blob)))
    assert found == [], f"uuid-shaped strings in judge responses: {found}"
    for p in [EV, *PHOTOS, UNKNOWN]:
        assert p not in blob and p[:8] not in blob
    assert "contact_sheet_key" not in blob and "results_run_prefix" not in blob  # storage keys are dropped
    assert "_local/objects" not in blob and "X-Amz" not in blob  # no presigned S3 URLs


def test_aliases_are_the_documented_shapes(real_shaped: TestClient) -> None:
    out = _judge_sweep(real_shaped)
    ev = next(e for e in out["events"]["items"] if e["label"] == "Event 3")
    assert ev["event_id"] == "ev_03"
    (g,) = out["groups"]["items"]
    assert g["group_id"] == "g_ev_03_1" and g["group_label"] == "Group 1"
    assert all(re.fullmatch(r"ph_[0-9a-f]{10}", m["photo_id"]) for m in g["members"])
    assert len({m["photo_id"] for m in g["members"]}) == 4
    best = next(m for m in g["members"] if m["is_best"])
    assert g["best_photo_id"] == best["photo_id"]  # the same alias in both places
    photo = out["photo"]
    assert photo["reason_codes"][0].startswith("DUPLICATE_OF:ph_")
    assert "<id>" not in photo["reason_text"] and "ph_" in photo["reason_text"]
    assert {r["run_id"] for r in out["runs_ev"]["items"]} == {RUN, "run_ev_03_20261009T050000Z"}


def test_alias_round_trip(real_shaped: TestClient) -> None:
    out = _judge_sweep(real_shaped)
    g = out["groups"]["items"][0]
    for m in g["members"]:
        one = real_shaped.get(f"/events/ev_03/photos/{m['photo_id']}", headers=judge()).json()
        assert one["photo_id"] == m["photo_id"]
    (again,) = out["group"]["items"]
    assert again["group_id"] == g["group_id"]
    # Team keeps real ids and can still use them (ops); the same objects behind both views.
    tg = real_shaped.get(f"/events/{EV}/groups", headers=team()).json()["items"][0]
    assert tg["group_id"] == GROUP and {m["photo_id"] for m in tg["members"]} == set(PHOTOS)
    assert real_shaped.get(f"/events/ev_03/photos/{PHOTOS[0]}", headers=team()).status_code == 200


def test_judge_raw_ids_are_refused(real_shaped: TestClient) -> None:
    assert real_shaped.get(f"/events/{EV}/groups", headers=judge()).status_code == 404
    assert real_shaped.get(f"/events/ev_03/photos/{PHOTOS[0]}", headers=judge()).status_code == 404
    assert real_shaped.get("/events/ev_03/groups", params={"group_id": GROUP}, headers=judge()).status_code == 404
    assert real_shaped.get("/events/ev_03/photos/ph_0000000000", headers=judge()).status_code == 404
    assert real_shaped.get("/events/ev_99/groups", headers=judge()).status_code == 404
    assert (
        real_shaped.get("/events/ev_03/groups", params={"group_id": "g_ev_01_1"}, headers=judge()).status_code
        == 404
    )


def test_trace_scrub(real_shaped: TestClient) -> None:
    items = {line["seq"]: line for line in real_shaped.get(f"/runs/{RUN}/trace", headers=judge()).json()["items"]}
    assert items[1]["args"] == {"event_id": "ev_03"}
    assert all(re.fullmatch(r"ph_[0-9a-f]{10}", p) for p in items[2]["result"]["photos"])
    assert items[2]["result"]["key"] == "manifests/ev_03.jsonl"
    assert "g_ev_03_1" in items[3]["text"] and "<id>" in items[3]["text"]  # unknown uuid redacted
    assert items[4]["args"]["items"][0]["group_id"] == "g_ev_03_1"
    assert "contact_sheet_key" not in items[4]["args"]["items"][0]
    (sheet,) = items[4]["contact_sheet_urls"]
    assert urlparse(sheet).path == "/img/ev_03/s/g_ev_03_1.jpg"
    assert items[5]["error"] == "S3 NoSuchKey proxy/ev_03/<id>.jpg"


def test_run_error_is_a_code_for_the_judge(real_shaped: TestClient) -> None:
    """S-13: the judge gets error_code only; the team keeps the text (ids still real for ops)."""
    run = real_shaped.get(f"/runs/{RUN}", headers=judge()).json()
    assert run["error_code"] == "ClientError" and "error" not in run
    assert run["opencv_hal"] == "carotene"  # read from results/<E>/<run>/summary.json
    listed = {r["run_id"]: r for r in real_shaped.get("/runs", headers=judge()).json()["items"]}
    assert listed[RUN]["error_code"] == "ClientError" and "error" not in listed[RUN]
    assert listed["run_ev_03_20261009T050000Z"]["error_code"] == "error"
    team_run = real_shaped.get(f"/runs/{RUN}", headers=team()).json()
    assert (
        team_run["error"].startswith("ClientError: GetObject proxy/")
        and team_run["error_code"] == "ClientError"
    )


def test_error_code_never_carries_text() -> None:
    from app.views import error_code

    assert error_code("ThrottlingException: Rate exceeded for arn:aws:...") == "ThrottlingException"
    assert error_code("TimeoutError") == "TimeoutError"
    assert error_code("no manifest for x") == "error"
    assert error_code("") is None and error_code(None) is None


def test_image_links_serve_bytes_and_carry_no_key(real_shaped: TestClient) -> None:
    g = real_shaped.get("/events/ev_03/groups", headers=judge()).json()["items"][0]
    for url in [g["contact_sheet_url"], *(m["proxy_url"] for m in g["members"])]:
        u = urlparse(url)
        assert UUID.search(url) is None and u.path.startswith("/img/ev_03/")
        r = real_shaped.get(f"{u.path}?{u.query}")
        assert r.status_code == 200 and r.content[:2] == b"\xff\xd8", url
        assert "location" not in r.headers  # streamed, not a redirect to an S3 key


def test_image_link_for_another_event_is_refused(real_shaped: TestClient) -> None:
    g = real_shaped.get("/events/ev_03/groups", headers=judge()).json()["items"][0]
    u = urlparse(g["members"][0]["proxy_url"])
    assert real_shaped.get(f"{u.path.replace('ev_03', 'ev_01')}?{u.query}").status_code == 403


def test_alias_key_missing_fails_closed(real_shaped: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import aliases, config

    monkeypatch.delenv("LOCAL_MODE", raising=False)
    monkeypatch.setenv("CULL_ALIAS_KEY", "")
    config.settings.cache_clear()
    aliases.alias_key.clear()

    class NoSecret:
        def get_secret_value(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("no secret")

    monkeypatch.setattr(aliases, "client", lambda _svc: NoSecret())
    assert aliases.alias_key.get.__self__ is aliases.alias_key  # sanity
    with pytest.raises(Exception) as e:
        aliases.alias_key.get()
    assert getattr(e.value, "status_code", None) == 503


def test_team_can_opt_in_to_aliases(real_shaped: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config

    monkeypatch.setenv("CULL_ALIAS_TEAM", "1")
    config.settings.cache_clear()
    blob = json.dumps(real_shaped.get("/events/ev_03/groups", headers=team()).json())
    assert UUID.search(blob) is None


def test_team_approval_by_alias_with_aliased_override(
    real_shaped: TestClient, monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    from app import config

    monkeypatch.setenv("CULL_ALIAS_TEAM", "1")
    config.settings.cache_clear()
    (a,) = [
        x for x in real_shaped.get("/approvals", headers=team()).json()["items"] if x["approval_id"] == "g_ev_03_1"
    ]
    keep = a["members"][2]["photo_id"]
    rest = [m["photo_id"] for m in a["members"] if m["photo_id"] != keep]
    r = real_shaped.post(
        "/approvals/g_ev_03_1",
        headers=team(),
        json={"decision": "override", "human_verdict": {"keep": [keep], "reject": rest}},
    )
    assert r.status_code == 200, r.text
    assert UUID.search(r.text) is None
    rows = {
        json.loads(line)["sk"]: json.loads(line)
        for line in (root / "dynamodb" / "keepframe-decisions.jsonl").read_text().splitlines()
    }
    stored = rows[f"GROUP#{GROUP}"]["human_decision"]["human_verdict"]
    assert stored["keep"] == [PHOTOS[2]] and set(stored["reject"]) == {PHOTOS[0], PHOTOS[1], PHOTOS[3]}

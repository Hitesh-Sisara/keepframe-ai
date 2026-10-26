from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from tests.conftest import judge, ph, team
from tests.fixtures.make_fixtures import EVENT_ID, RUN_ID, STOCK_RUN_ID

EVENT2 = "7d2a9e44-1c3b-4f6a-8e55-0a9b8c7d6e02"

READ_ROUTES = [
    "/health",
    "/events",
    "/events/ev_01/groups",
    "/events/ev_01/photos/{p_8831}",
    "/runs",
    f"/runs/{RUN_ID}",
    f"/runs/{RUN_ID}/trace",
    f"/runs/{RUN_ID}/agent",
    "/bench",
]


# ---------------------------------------------------------------- auth


@pytest.mark.parametrize("path", [*READ_ROUTES, "/approvals"])
def test_every_read_route_needs_a_token(client: TestClient, path: str) -> None:
    path = path.format(p_8831=ph(EVENT_ID, "p_8831"))
    r = client.get(path)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert client.get(path, headers={"Authorization": "Bearer not-a-real-token-at-all"}).status_code == 401
    assert client.get(path, headers={"Authorization": "Basic abc"}).status_code == 401


@pytest.mark.parametrize("path", READ_ROUTES)
def test_judge_and_team_can_read(client: TestClient, path: str) -> None:
    path = path.format(p_8831=ph(EVENT_ID, "p_8831"))
    assert client.get(path, headers=judge()).status_code == 200
    assert client.get(path, headers=team()).status_code == 200


def test_mutations_need_a_token(client: TestClient) -> None:
    assert client.post("/runs", json={"event_id": EVENT_ID, "flavor": "stock"}).status_code == 401
    assert client.post("/approvals/g_412", json={"decision": "approve"}).status_code == 401


def test_judge_cannot_mutate(client: TestClient) -> None:
    r = client.post("/runs", headers=judge(), json={"event_id": EVENT_ID, "flavor": "stock"})
    assert r.status_code == 403
    r = client.post("/approvals/g_412", headers=judge(), json={"decision": "approve"})
    assert r.status_code == 403
    # Even an unknown or malformed mutation is refused before validation for the judge.
    assert client.post("/runs", headers=judge(), json={}).status_code == 403
    assert client.delete("/runs", headers=judge()).status_code in (403, 405)


def test_judge_cannot_list_approvals(client: TestClient) -> None:
    assert client.get("/approvals", headers=judge()).status_code == 403
    assert client.get("/approvals", headers=team()).status_code == 200


def test_health_reports_role(client: TestClient) -> None:
    assert client.get("/health", headers=judge()).json()["role"] == "judge"
    assert client.get("/health", headers=team()).json()["role"] == "team"


def test_secrets_manager_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import auth

    calls: list[str] = []

    class FakeSM:
        def get_secret_value(self, SecretId: str) -> dict[str, str]:  # noqa: N803
            calls.append(SecretId)
            if SecretId == "keepframe/judge-token":
                return {"SecretString": json.dumps({"token": "j" * 32, "previous": "o" * 32})}
            return {"SecretString": "t" * 32}

    monkeypatch.delenv("CULL_JUDGE_TOKEN", raising=False)
    monkeypatch.delenv("CULL_TEAM_TOKEN", raising=False)
    from app import config

    config.settings.cache_clear()
    monkeypatch.setattr(auth, "client", lambda _svc: FakeSM())
    auth.token_cache.clear()
    assert auth._match("j" * 32) == "judge"
    assert auth._match("o" * 32) == "judge"  # previous token valid during rotation
    assert auth._match("t" * 32) == "team"
    assert auth._match("x" * 32) is None
    assert sorted(calls) == ["keepframe/judge-token", "keepframe/team-token"]  # cached afterwards
    auth.token_cache.clear()
    config.settings.cache_clear()


def test_secret_unavailable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import auth, config

    class Broken:
        def get_secret_value(self, SecretId: str) -> dict[str, str]:  # noqa: N803
            raise RuntimeError("no access")

    monkeypatch.delenv("CULL_JUDGE_TOKEN", raising=False)
    monkeypatch.delenv("CULL_TEAM_TOKEN", raising=False)
    config.settings.cache_clear()
    monkeypatch.setattr(auth, "client", lambda _svc: Broken())
    auth.token_cache.clear()
    assert auth._match("") is None
    assert auth._match("t" * 32) is None
    auth.token_cache.clear()
    config.settings.cache_clear()


# ---------------------------------------------------------------- events


def test_events_index_hides_ids_and_names(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    body = client.get("/events", headers=judge()).json()
    assert [e["label"] for e in body["items"]] == ["Event 1", "Event 2"]
    first = body["items"][0]
    assert first["event_id"] == "ev_01"  # S-1: never the real event uuid
    assert first["category"] == "Ceremony"
    assert first["verdict_counts"] == {"keep": 3, "reject": 5, "review": 2}
    assert first["latest_run"]["run_id"] == RUN_ID
    raw = json.dumps(body)
    assert "do_not_expose" not in raw  # no source user / white-label ids
    assert "Synthetic Garden Ceremony" not in raw
    assert "name" not in first


def test_events_names_opt_in(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config

    monkeypatch.setenv("CULL_SHOW_EVENT_NAMES", "1")
    config.settings.cache_clear()
    body = client.get("/events", headers=judge()).json()
    assert body["items"][0]["name"] == "Synthetic Garden Ceremony"
    assert "do_not_expose" not in json.dumps(body)


def test_events_pagination(client: TestClient) -> None:
    page1 = client.get("/events?limit=1", headers=judge()).json()
    assert len(page1["items"]) == 1 and page1["next_cursor"]
    page2 = client.get(f"/events?limit=1&cursor={page1['next_cursor']}", headers=judge()).json()
    assert page2["items"][0]["event_id"] == "ev_02"
    assert page2["next_cursor"] is None
    assert client.get("/events?cursor=%%%", headers=judge()).status_code == 400


# ---------------------------------------------------------------- groups


def test_groups_from_dynamodb(client: TestClient) -> None:
    body = client.get(f"/events/{EVENT_ID}/groups", headers=team()).json()
    assert body["source"] == "merged" and body["total"] == 3
    assert [g["group_id"] for g in body["items"]] == ["g_410", "g_411", "g_412"]
    assert body["event_verdict_counts"] == {"keep": 3, "reject": 5, "review": 2}
    g412 = body["items"][2]
    assert g412["pending_approval"] is True
    assert g412["size"] == 4 and g412["margin"] == 0.03
    assert g412["verdict_counts"] == {"keep": 0, "reject": 2, "review": 2}
    assert [m["photo_id"] for m in g412["members"]] == ["p_8833", "p_8831", "p_8832", "p_8830"]
    assert g412["members"][0]["is_best"] and g412["members"][0]["rank"] == 1
    assert g412["reason_counts"] == {"REVIEW_LOW_CONFIDENCE": 2, "BLUR_FOCUS": 1, "BURST_NOT_BEST": 1}
    assert "scores" not in g412["members"][0]  # list view stays light
    assert "contact_sheet_url" in g412


def test_groups_pagination(client: TestClient) -> None:
    seen: list[str] = []
    cursor = None
    for _ in range(5):
        q = "?limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = client.get(f"/events/{EVENT_ID}/groups{q}", headers=team()).json()
        seen += [g["group_id"] for g in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == ["g_410", "g_411", "g_412"]


def test_groups_cursor_is_bound_to_the_event(client: TestClient) -> None:
    body = client.get(f"/events/{EVENT_ID}/groups?limit=1", headers=team()).json()
    r = client.get(f"/events/{EVENT2}/groups?cursor={body['next_cursor']}", headers=team())
    assert r.status_code == 400


def test_groups_s3_fallback(client: TestClient) -> None:
    body = client.get(f"/events/{EVENT2}/groups", headers=team()).json()
    assert body["source"] == "s3"
    assert body["items"][0]["group_id"] == "g_900"
    assert body["items"][0]["verdict_counts"] == {"keep": 0, "reject": 0, "review": 0}


def test_single_group_detail(client: TestClient) -> None:
    body = client.get(f"/events/{EVENT_ID}/groups?group_id=g_412", headers=team()).json()
    (g,) = body["items"]
    best = g["members"][0]
    assert best["photo_id"] == "p_8833" and "scores" in best and "reason_text" in best
    assert client.get(f"/events/{EVENT_ID}/groups?group_id=g_999", headers=team()).status_code == 404


def test_unknown_event_and_bad_ids(client: TestClient) -> None:
    assert client.get("/events/nope/groups", headers=judge()).status_code == 404
    assert client.get("/events/..%2F..%2Fetc/groups", headers=judge()).status_code in (404, 422)
    assert client.get(f"/events/{EVENT_ID}/photos/a%23b", headers=team()).status_code == 422


# ---------------------------------------------------------------- photos + presign


def test_photo_detail_and_presigned_urls(client: TestClient) -> None:
    body = client.get(f"/events/{EVENT_ID}/photos/p_8831", headers=team()).json()
    assert body["verdict"] == "review"
    assert body["reason_codes"] == ["REVIEW_LOW_CONFIDENCE"]
    assert body["scores"]["n_faces"] == 3
    unknown = body["scores"]["faces"][2]
    assert unknown["state"] == "unknown" and unknown["left_open_p"] is None and unknown["fiqa"] is None
    assert {f["source"] for f in body["scores"]["faces"]} <= {"frsInfo", "yunet"}
    assert body["group"]["best_photo_id"] == "p_8833"
    url = urlparse(body["proxy_url"])
    # S-1: an alias link served by the API, never an S3 key (proxy/<eventId>/<photoId>.jpg) in the URL.
    assert url.path == f"/img/ev_01/p/{ph(EVENT_ID, 'p_8831')}.jpg"
    assert EVENT_ID not in body["proxy_url"] and "p_8831" not in body["proxy_url"]
    img = client.get(f"{url.path}?{url.query}")
    assert img.status_code == 200 and img.content[:2] == b"\xff\xd8"
    assert img.headers["content-type"] == "image/jpeg"
    sheet = urlparse(body["contact_sheet_url"])
    assert client.get(f"{sheet.path}?{sheet.query}").status_code == 200
    # Tampered signature, an expired link, a raw id or a non-image kind is refused.
    assert client.get(f"{url.path}?{url.query}x").status_code == 403
    assert client.get(f"{url.path}?exp=1&sig=x").status_code == 403
    assert client.get(f"/img/ev_01/p/p_8831.jpg?{url.query}").status_code == 404
    assert client.get(f"/img/ev_01/r/{ph(EVENT_ID, 'p_8831')}.jpg?{url.query}").status_code == 404
    assert client.get("/_local/objects/results/x.json?expires=9999999999&sig=a").status_code == 404


def test_presign_ttl_capped_at_15_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config

    monkeypatch.setenv("CULL_PRESIGN_TTL_S", "86400")
    config.settings.cache_clear()
    assert config.settings().presign_ttl_s == 900
    config.settings.cache_clear()


def test_photo_404(client: TestClient) -> None:
    assert client.get(f"/events/{EVENT_ID}/photos/p_0000", headers=team()).status_code == 404


# ---------------------------------------------------------------- runs


def test_runs_list_and_detail(client: TestClient) -> None:
    body = client.get("/runs", headers=judge()).json()
    assert [r["run_id"] for r in body["items"]] == [RUN_ID, STOCK_RUN_ID]
    assert body["items"][0]["event_label"] == "Event 1"
    run = client.get(f"/runs/{RUN_ID}", headers=judge()).json()
    assert run["opencv_version"] == "5.0.0"
    assert run["flavor"] == "stock"
    assert run["cv2_file"].endswith("cv2.abi3.so")
    assert run["status"] == "awaiting_approval"
    assert run["llm_provider"] == "anthropic"
    assert run["planner_model"] == "claude-sonnet-5"
    assert run["reasons_model"] == "claude-haiku-4.5"
    assert "gsi1pk" not in run and "pk" not in run
    assert client.get("/runs/run_nope", headers=judge()).status_code == 404


def test_opencv_hal_passes_through_runs(client: TestClient) -> None:
    """Summarise copies opencv_hal onto RUN#; both the list rows and the detail must carry it."""
    from app import data
    from app.backend import backend

    run = data.find_run(RUN_ID)
    assert run is not None
    backend().update_item(data.decisions_table(), run["pk"], run["sk"], set_values={"opencv_hal": "carotene"})
    assert client.get(f"/runs/{RUN_ID}", headers=judge()).json()["opencv_hal"] == "carotene"
    rows = {r["run_id"]: r for r in client.get("/runs", headers=judge()).json()["items"]}
    assert rows[RUN_ID]["opencv_hal"] == "carotene"
    assert "opencv_hal" not in rows[STOCK_RUN_ID]  # absent, never null


def test_runs_pagination(client: TestClient) -> None:
    p1 = client.get("/runs?limit=1", headers=team()).json()
    p2 = client.get(f"/runs?limit=1&cursor={p1['next_cursor']}", headers=team()).json()
    assert [r["run_id"] for r in p1["items"] + p2["items"]] == [RUN_ID, STOCK_RUN_ID]
    assert p2["next_cursor"] is None


def test_start_run_local(client: TestClient) -> None:
    # COOL is gone (Marketplace-billed): only the stock wheel can be requested.
    assert (
        client.post("/runs", headers=team(), json={"event_id": EVENT2, "flavor": "cool"}).status_code == 422
    )
    r = client.post("/runs", headers=team(), json={"event_id": EVENT2})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["run_id"].startswith("run_") and len(body["run_id"]) == 30
    assert body["status"] == "starting" and body["flavor"] == "stock"
    listed = client.get(f"/runs?event_id={EVENT2}", headers=team()).json()["items"]
    assert listed[0]["run_id"] == body["run_id"]
    # A second start while the first is active is refused.
    assert client.post("/runs", headers=team(), json={"event_id": EVENT2}).status_code == 409


def test_start_run_rejects_active_event_and_bad_input(client: TestClient) -> None:
    assert client.post("/runs", headers=team(), json={"event_id": EVENT_ID}).status_code == 409
    assert client.post("/runs", headers=team(), json={"event_id": EVENT2, "flavor": "x86"}).status_code == 422
    assert client.post("/runs", headers=team(), json={"event_id": "missing"}).status_code == 404


def test_start_run_calls_step_functions(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config, main

    seen: dict[str, Any] = {}

    class FakeSfn:
        def start_execution(self, **kw: Any) -> dict[str, str]:
            seen.update(kw)
            return {
                "executionArn": "arn:aws:states:ap-south-1:<AWS_ACCOUNT_ID>:execution:CullEvent:" + kw["name"]
            }

    monkeypatch.setenv(
        "CULL_STATE_MACHINE_ARN", "arn:aws:states:ap-south-1:<AWS_ACCOUNT_ID>:stateMachine:CullEvent"
    )
    config.settings.cache_clear()
    monkeypatch.setattr(main, "client", lambda svc: FakeSfn())
    r = client.post("/runs", headers=team(), json={"event_id": EVENT2, "flavor": "stock"})
    assert r.status_code == 202
    assert r.json()["status"] == "running"
    assert seen["stateMachineArn"].endswith(":CullEvent")
    assert json.loads(seen["input"]) == {"event_id": EVENT2, "flavor": "stock", "run_id": r.json()["run_id"]}
    assert seen["name"] == r.json()["run_id"]


def test_start_run_marks_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config, main

    class Boom:
        def start_execution(self, **kw: Any) -> None:
            raise RuntimeError("throttled")

    monkeypatch.setenv(
        "CULL_STATE_MACHINE_ARN", "arn:aws:states:ap-south-1:<AWS_ACCOUNT_ID>:stateMachine:CullEvent"
    )
    config.settings.cache_clear()
    monkeypatch.setattr(main, "client", lambda svc: Boom())
    assert client.post("/runs", headers=team(), json={"event_id": EVENT2}).status_code == 502
    latest = client.get(f"/runs?event_id={EVENT2}", headers=team()).json()["items"][0]
    assert latest["status"] == "failed"


# ---------------------------------------------------------------- trace


def test_trace_paged_by_seq(client: TestClient) -> None:
    p1 = client.get(f"/runs/{RUN_ID}/trace?limit=10", headers=judge()).json()
    assert p1["source"] == "dynamodb"
    assert [line["seq"] for line in p1["items"]] == list(range(1, 11))
    assert p1["next_after"] == 10
    p2 = client.get(f"/runs/{RUN_ID}/trace?limit=10&after=10", headers=judge()).json()
    p3 = client.get(f"/runs/{RUN_ID}/trace?limit=10&after={p2['next_after']}", headers=judge()).json()
    seqs = [line["seq"] for line in p1["items"] + p2["items"] + p3["items"]]
    assert seqs == list(range(1, 29))
    assert p3["next_after"] is None
    assert all("pk" not in line and "ttl" not in line for line in p3["items"])


def test_trace_pivots_are_the_three_plan_moments(client: TestClient) -> None:
    body = client.get(f"/runs/{RUN_ID}/trace?limit=500", headers=judge()).json()
    pivots = {(p["kind"], p["result_seq"], p["call_seq"]) for p in body["pivots"]}
    assert pivots == {
        ("sharpness_rescore", 18, 20),
        ("eyes_compare_in_group", 23, 25),
        ("small_margin_to_human", 26, 28),
    }
    approval_call = next(line for line in body["items"] if line["seq"] == 28)
    assert approval_call["contact_sheet_urls"][0].startswith("http://testserver/img/ev_01/s/")


def test_trace_group_filter(client: TestClient) -> None:
    body = client.get(f"/runs/{RUN_ID}/trace?group_id=g_412", headers=team()).json()
    seqs = [line["seq"] for line in body["items"]]
    assert seqs[0] == 4  # the cluster result that formed g_412
    assert seqs[1:] == list(range(17, 29))  # then the whole trace-schema chain
    assert 9 not in seqs  # g_410 ranking is not part of g_412
    assert len(body["pivots"]) == 3


def test_trace_s3_fallback(client: TestClient, root: Any) -> None:
    (root / "dynamodb" / "keepframe-traces.jsonl").write_text("")
    from app import backend

    backend.reset_backend()
    body = client.get(f"/runs/{RUN_ID}/trace?limit=5", headers=judge()).json()
    assert body["source"] == "s3"
    assert [line["seq"] for line in body["items"]] == [1, 2, 3, 4, 5]
    assert body["next_after"] == 5
    tail = client.get(f"/runs/{RUN_ID}/trace?after=25", headers=judge()).json()
    assert [line["seq"] for line in tail["items"]] == [26, 27, 28]


# ---------------------------------------------------------------- approvals


def _install_agent(monkeypatch: pytest.MonkeyPatch, fail: bool = False) -> list[dict[str, Any]]:
    from app import agent_resume

    calls: list[dict[str, Any]] = []

    def fake(run_id: str, decisions: list[dict[str, Any]], run: Any) -> dict[str, Any]:
        calls.append(
            {"run_id": run_id, "decisions": decisions, "session": agent_resume.session_id_for(run, run_id)}
        )
        if fail:
            raise agent_resume.ResumeUnavailable("agent down")
        return {"via": "test", "status": 200}

    monkeypatch.setattr(agent_resume, "resume", fake)
    return calls


def test_approvals_list(client: TestClient) -> None:
    body = client.get("/approvals", headers=team()).json()
    (a,) = body["items"]
    assert a["approval_id"] == "g_412" and a["run_id"] == RUN_ID
    assert a["proposed"]["keep"] == "p_8833"
    assert a["confidence"] == 0.6
    assert [m["photo_id"] for m in a["members"]] == ["p_8830", "p_8831", "p_8832", "p_8833"]
    assert a["contact_sheet_url"].startswith("http://testserver/img/ev_01/s/")
    assert "gsi3pk" not in a and "pk" not in a


def test_approve_resumes_agent_and_clears_queue(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_agent(monkeypatch)
    r = client.post("/approvals/g_412", headers=team(), json={"decision": "approve", "note": "fine"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "resolved"
    (call,) = calls
    assert call["run_id"] == RUN_ID
    assert call["decisions"][0]["human_verdict"]["keep"] == "p_8833"
    assert call["decisions"][0]["interrupt_id"] == "intr_synthetic_0001"
    assert len(call["session"]) >= 33
    assert client.get("/approvals", headers=team()).json()["items"] == []
    assert client.post("/approvals/g_412", headers=team(), json={"decision": "approve"}).status_code == 404
    g = client.get(f"/events/{EVENT_ID}/groups?group_id=g_412", headers=team()).json()["items"][0]
    assert g["pending_approval"] is False


def test_override_validation(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_agent(monkeypatch)
    url = "/approvals/g_412"
    assert client.post(url, headers=team(), json={"decision": "override"}).status_code == 422
    whole_reject = {
        "decision": "override",
        "human_verdict": {"reject": ["p_8830", "p_8831", "p_8832", "p_8833"]},
    }
    assert (
        client.post(url, headers=team(), json=whole_reject).status_code == 422
    )  # never reject a whole group
    outsider = {"decision": "override", "human_verdict": {"keep": ["p_8810"]}}
    assert client.post(url, headers=team(), json=outsider).status_code == 422
    twice = {"decision": "override", "human_verdict": {"keep": ["p_8831"], "reject": ["p_8831"]}}
    assert client.post(url, headers=team(), json=twice).status_code == 422
    single = {"decision": "override", "human_verdict": "keep"}
    assert client.post(url, headers=team(), json=single).status_code == 422  # group needs a map
    ok = {
        "decision": "override",
        "note": "8831 has the better expression",
        "human_verdict": {"keep": ["p_8831"], "reject": ["p_8830", "p_8832", "p_8833"]},
    }
    r = client.post(url, headers=team(), json=ok)
    assert r.status_code == 200
    assert r.json()["human_decision"]["human_verdict"]["keep"] == ["p_8831"]


def test_resume_failure_keeps_item_pending(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_agent(monkeypatch, fail=True)
    r = client.post("/approvals/g_412", headers=team(), json={"decision": "approve"})
    assert r.status_code == 502
    (a,) = client.get("/approvals", headers=team()).json()["items"]
    assert a["resume_error"] == "agent down"
    calls = _install_agent(monkeypatch)
    assert client.post("/approvals/g_412", headers=team(), json={"decision": "approve"}).status_code == 200
    assert len(calls) == 1


def test_concurrent_answer_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch, root: Any) -> None:
    from datetime import UTC, datetime

    path = root / "dynamodb" / "keepframe-decisions.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        if row["sk"] == "GROUP#g_412":
            row["resume_lock"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    from app import backend

    backend.reset_backend()
    _install_agent(monkeypatch)
    assert client.post("/approvals/g_412", headers=team(), json={"decision": "approve"}).status_code == 409


def test_agent_resume_http_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import agent_resume, config

    sent: dict[str, Any] = {}

    class Resp:
        status = 200

        def read(self, n: int) -> bytes:
            return b'{"status": "resumed", "run_id": "r", "secret": "x"}'

        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

    def fake_urlopen(req: Any, timeout: int) -> Resp:
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data)
        return Resp()

    monkeypatch.delenv("CULL_AGENT_RUNTIME_ARN", raising=False)
    monkeypatch.setenv("CULL_AGENT_URL", "http://127.0.0.1:8080/invocations")
    config.settings.cache_clear()
    monkeypatch.setattr(agent_resume.urllib.request, "urlopen", fake_urlopen)
    out = agent_resume.resume("run_x", [{"id": "g_1"}], None)
    assert out == {"via": "http", "status": 200, "ack": {"status": "resumed", "run_id": "r"}}
    assert sent["body"] == {"mode": "resume", "run_id": "run_x", "decisions": [{"id": "g_1"}]}
    monkeypatch.setenv("CULL_AGENT_URL", "http://evil.example/x")
    config.settings.cache_clear()
    with pytest.raises(agent_resume.ResumeUnavailable):
        agent_resume.resume("run_x", [], None)
    config.settings.cache_clear()


ARN = "arn:aws:bedrock-agentcore:ap-south-1:<AWS_ACCOUNT_ID>:runtime/keepframe_agent-abc"


class FakeAgentCore:
    """Stub bedrock-agentcore client: records calls, answers with a JSON blob on a `response` stream."""

    def __init__(
        self,
        answer: Any = None,
        raw: bytes | None = None,
        exc: Exception | None = None,
        status_code: int = 200,
    ) -> None:
        import io

        self.calls: list[dict[str, Any]] = []
        self._exc = exc
        self._status_code = status_code
        self._raw = raw if raw is not None else json.dumps(answer).encode()
        self._io = io

    def invoke_agent_runtime(self, **kw: Any) -> dict[str, Any]:
        self.calls.append({**kw, "payload": json.loads(kw["payload"])})
        if self._exc is not None:
            raise self._exc
        return {"statusCode": self._status_code, "response": self._io.BytesIO(self._raw)}


def _use_agentcore(monkeypatch: pytest.MonkeyPatch, fake: FakeAgentCore) -> FakeAgentCore:
    from app import agent_resume, config

    monkeypatch.setenv("CULL_AGENT_RUNTIME_ARN", ARN)
    config.settings.cache_clear()
    monkeypatch.setattr(agent_resume, "client", lambda svc: fake)
    return fake


def _client_error(code: str) -> Exception:
    from botocore.exceptions import ClientError

    return ClientError({"Error": {"Code": code, "Message": "x"}}, "InvokeAgentRuntime")


def test_session_id_rule_is_pinned() -> None:
    """The workflow Lambdas replicate this rule; a change here must be a contract change."""
    from app.agent_resume import session_id_for

    assert session_id_for(None, "run_01J9X") == "keepframe-run_01J9X00000000000000"
    assert len(session_id_for(None, "run_01J9X")) == 33
    long_id = "run_01JA2B3C4D5E6F7G8H9J0K1M2N3P"  # new_run_id() shape: run_ + 26 chars
    assert session_id_for(None, long_id) == f"keepframe-{long_id}"
    assert session_id_for({"agent_session_id": "s" * 40}, "run_x") == "s" * 40
    assert session_id_for({"agent_session_id": "short"}, "run_x") == "keepframe-run_x000000000000000000"


def test_agentcore_resume_surfaces_agent_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import agent_resume

    fake = _use_agentcore(
        monkeypatch,
        FakeAgentCore(
            {"status": "paused", "run_id": "run_01J9X", "pending_approvals": ["g_9"], "x": "y" * 99}
        ),
    )
    out = agent_resume.resume("run_01J9X", [{"group_id": "g_412", "human_verdict": "approve"}], None)
    assert out == {
        "via": "agentcore",
        "status": "paused",
        "http_status": 200,
        "run_id": "run_01J9X",
        "ack": {"pending_approvals": 1},
    }
    (call,) = fake.calls
    assert call["agentRuntimeArn"] == ARN
    assert call["runtimeSessionId"] == "keepframe-run_01J9X00000000000000"
    assert call["payload"] == {
        "mode": "resume",
        "run_id": "run_01J9X",
        "decisions": [{"group_id": "g_412", "human_verdict": "approve"}],
    }


def test_agentcore_resume_tolerates_non_json_and_bounds_the_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import agent_resume

    _use_agentcore(monkeypatch, FakeAgentCore(raw=b"x" * (200 * 1024)))
    out = agent_resume.resume("run_x", [], None)
    assert out["via"] == "agentcore" and out["status"] == "unknown" and "ack" not in out
    assert agent_resume.MAX_RESPONSE_BYTES == 64 * 1024


@pytest.mark.parametrize(
    "fake",
    [
        FakeAgentCore(exc=_client_error("RuntimeClientError")),
        FakeAgentCore(exc=_client_error("ThrottlingException")),
        FakeAgentCore(exc=_client_error("ResourceNotFoundException")),
        FakeAgentCore({"status": "running"}, status_code=502),
        FakeAgentCore({"status": "error", "error": "decision 0: human_verdict is required"}),
    ],
    ids=["runtime-client-error", "throttling", "not-found", "http-502", "agent-refused"],
)
def test_agentcore_failures_are_resume_unavailable(
    monkeypatch: pytest.MonkeyPatch, fake: FakeAgentCore
) -> None:
    from app import agent_resume

    _use_agentcore(monkeypatch, fake)
    with pytest.raises(agent_resume.ResumeUnavailable) as e:
        agent_resume.resume("run_x", [{"group_id": "g_1"}], None)
    assert not isinstance(e.value, agent_resume.NotConfigured)


def test_agentcore_failure_keeps_approval_pending(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_agentcore(monkeypatch, FakeAgentCore(exc=_client_error("ThrottlingException")))
    r = client.post("/approvals/g_412", headers=team(), json={"decision": "approve"})
    assert r.status_code == 502
    (a,) = client.get("/approvals", headers=team()).json()["items"]
    assert a["resume_error"] == "AgentCore ThrottlingException"


def test_approval_via_agentcore_sets_run_status_now(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, root: Any
) -> None:
    fake = _use_agentcore(monkeypatch, FakeAgentCore({"status": "running", "run_id": RUN_ID}))
    r = client.post("/approvals/g_412", headers=team(), json={"decision": "approve", "note": "ok"})
    assert r.status_code == 200, r.text
    assert r.json()["resume"]["via"] == "agentcore" and r.json()["resume"]["status"] == "running"
    (call,) = fake.calls
    (d,) = call["payload"]["decisions"]
    assert d["group_id"] == "g_412" and d["human_verdict"]["keep"] == "p_8833"  # approve = the proposal
    run = _rows(root)[f"RUN#{RUN_ID}"]
    assert run["status"] == "running" and "paused_at" not in run
    assert run["agent_status_via"] == "agentcore"
    # The agent applies verdicts on this path, not the API.
    assert _rows(root)["PHOTO#p_8833"]["decided_by"] == "agent"


def test_agentcore_status_stamp_never_overwrites_a_newer_agent_write(root: Any, client: TestClient) -> None:
    from app import data, main
    from app.backend import backend

    run = data.find_run(RUN_ID)
    assert run is not None
    # The agent wrote RUN# (done) after the API read it.
    backend().update_item(
        data.decisions_table(),
        run["pk"],
        run["sk"],
        set_values={"status": "succeeded", "updated_at": "2099-01-01T00:00:00Z"},
    )
    main._stamp_agent_status(run, "paused")
    assert _rows(root)[f"RUN#{RUN_ID}"]["status"] == "succeeded"


def test_run_agent_endpoint_without_agentcore(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CULL_AGENT_RUNTIME_ARN", "CULL_AGENT_URL"):
        monkeypatch.delenv(name, raising=False)
    from app import config

    config.settings.cache_clear()
    body = client.get(f"/runs/{RUN_ID}/agent", headers=judge()).json()
    assert body == {"run_id": RUN_ID, "path": "none", "run_status": "awaiting_approval", "live": None}
    monkeypatch.setenv("CULL_AGENT_URL", "http://127.0.0.1:8080/invocations")
    config.settings.cache_clear()
    assert client.get(f"/runs/{RUN_ID}/agent", headers=judge()).json()["path"] == "http"
    assert client.get("/runs/run_nope/agent", headers=judge()).status_code == 404
    assert client.get(f"/runs/{RUN_ID}/agent").status_code == 401


def test_run_agent_endpoint_live_agentcore_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import main

    main._agent_probe_cache.clear()
    fake = _use_agentcore(monkeypatch, FakeAgentCore({"status": "paused", "run_id": RUN_ID}))
    body = client.get(f"/runs/{RUN_ID}/agent", headers=judge()).json()
    assert body["path"] == "agentcore"
    assert body["session_id"] == f"keepframe-{RUN_ID}"
    assert body["live"]["status"] == "paused" and body["live"]["checked_at"]
    (call,) = fake.calls
    assert call["payload"] == {"mode": "status", "run_id": RUN_ID}
    assert call["runtimeSessionId"] == f"keepframe-{RUN_ID}"
    client.get(f"/runs/{RUN_ID}/agent", headers=judge())
    assert len(fake.calls) == 1  # cached for a few seconds
    # A finished run is never probed (no runtime session started for a judge browsing history).
    done = client.get(f"/runs/{STOCK_RUN_ID}/agent", headers=judge()).json()
    assert done["live"] is None and done["live_skipped"] == "run is not active"
    assert len(fake.calls) == 1


def test_run_agent_endpoint_unreachable_runtime(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import main

    main._agent_probe_cache.clear()
    _use_agentcore(monkeypatch, FakeAgentCore(exc=_client_error("RuntimeClientError")))
    body = client.get(f"/runs/{RUN_ID}/agent", headers=team()).json()
    assert body["live"]["status"] == "unreachable"
    assert body["live"]["error"] == "AgentCore RuntimeClientError"
    judged = client.get(f"/runs/{RUN_ID}/agent", headers=judge()).json()
    assert judged["live"]["status"] == "unreachable" and "error" not in judged["live"]  # S-13


def test_agent_host_and_run_kind_pass_through(client: TestClient, root: Any) -> None:
    from app import data
    from app.backend import backend

    run = data.find_run(RUN_ID)
    assert run is not None
    backend().update_item(
        data.decisions_table(),
        run["pk"],
        run["sk"],
        set_values={"run_kind": "agentcore", "agent_host": "agentcore"},
    )
    one = client.get(f"/runs/{RUN_ID}", headers=judge()).json()
    assert one["run_kind"] == "agentcore" and one["agent_host"] == "agentcore"
    listed = {r["run_id"]: r for r in client.get("/runs", headers=judge()).json()["items"]}
    assert listed[RUN_ID]["agent_host"] == "agentcore" and listed[RUN_ID]["run_kind"] == "agentcore"
    assert client.get(f"/runs/{RUN_ID}/agent", headers=judge()).json()["agent_host"] == "agentcore"


# ---------------------------------------------------------------- bench


def test_bench(client: TestClient) -> None:
    body = client.get("/bench", headers=judge()).json()
    assert body["synthetic"] is True and body["generated_at"]
    assert [h["label"] for h in body["hosts"]] == ["stock", "c7g-stock"]
    stock = body["hosts"][0]
    assert stock["instance_type"] == "c8g.xlarge" and stock["images_per_sec"] == 27.0
    assert set(stock) >= {"opencv_version", "cv2_file", "cost_per_1k_usd", "cpu_util_mean", "peak_rss_mb"}
    assert stock["stages"][0] == {"name": "decode_webp", "p50_ms": 30.0, "p95_ms": 44.0}
    assert all("host_label" not in h for h in body["hosts"])
    assert "stages" not in body  # flat rows are folded into hosts


def test_bench_passes_carotene_and_kleidicv_labels(client: TestClient, root: Any) -> None:
    """2026-10-08 bench axis: the carotene-HAL wheel against the KleidiCV wheel on the same host."""
    stages = [{"name": "clahe", "p50_ms": 2.4, "p95_ms": 2.7}]
    doc = {
        "generated_at": "2026-10-08T12:00:00+00:00",
        "hosts": [
            {
                "label": "stock-carotene",
                "instance_type": "c8g.xlarge",
                "images_per_sec": 27.0,
                "stages": stages,
            },
            {
                "label": "stock-kleidicv",
                "instance_type": "c8g.xlarge",
                "images_per_sec": 31.0,
                "stages": stages,
            },
        ],
    }
    (root / "s3" / "bench" / "results" / "latest" / "results.json").write_text(json.dumps(doc))
    body = client.get("/bench", headers=judge()).json()
    assert [h["label"] for h in body["hosts"]] == ["stock-carotene", "stock-kleidicv"]
    assert body["hosts"][1]["images_per_sec"] == 31.0 and body["hosts"][1]["stages"][0]["name"] == "clahe"


def test_bench_published_2026_10_08_doc_keeps_opencv_hal(client: TestClient, root: Any) -> None:
    """The real benchmark doc: four hosts, opencv_hal per host, dual (contract + flat) shape."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "bench" / "results" / "2026-10-08" / "results.json"
    if not src.exists():
        pytest.skip("bench/results/2026-10-08/results.json not in this checkout")
    (root / "s3" / "bench" / "results" / "latest" / "results.json").write_text(src.read_text())
    body = client.get("/bench", headers=judge()).json()
    by = {h["label"]: h for h in body["hosts"]}
    assert list(by) == ["stock-carotene", "stock-kleidicv", "src-gcc14-carotene", "src-gcc14-kleidicv"]
    assert by["stock-carotene"]["opencv_hal"] == "carotene"
    assert by["src-gcc14-kleidicv"]["opencv_hal"] == "kleidicv-26.03+carotene"
    assert "KleidiCV" in by["stock-kleidicv"]["custom_hal"]
    assert all(len(h["stages"]) == 9 for h in body["hosts"])
    assert body["synthetic"] is False


def test_bench_legacy_shape_is_normalised(client: TestClient, root: Any) -> None:
    from tests.fixtures.make_fixtures import legacy_bench_doc

    path = root / "s3" / "bench" / "results" / "latest" / "results.json"
    path.write_text(json.dumps(legacy_bench_doc()))
    body = client.get("/bench", headers=judge()).json()
    assert body["generated_at"] == "2026-10-12"
    by = {h["label"]: h for h in body["hosts"]}
    assert set(by) == {"stock", "stock-kleidicv"}
    k = by["stock-kleidicv"]
    assert k["images_per_sec"] == 248.0 and k["cost_per_1k_usd"] == 0.0011
    assert k["cpu_util_mean"] == 90.0 and k["instance_type"] == "c8g.4xlarge"
    assert by["stock"]["stages"] == [{"name": "clahe", "p50_ms": 2.4, "p95_ms": 3.3}]


def test_bench_dual_shape_prefers_contract_fields(client: TestClient, root: Any) -> None:
    """the benchmark temporarily emits both shapes; nested contract stages win, combined instance_type ignored."""
    doc = {
        "generated_at": "2026-10-05T12:39:46+00:00",
        "instance_type": "c7g.xlarge / c8g.xlarge",
        "hosts": [
            {
                "label": "stock",
                "host_label": "stock",
                "images_per_sec": 28.4,
                "images_per_s": 1.0,
                "stages": [{"name": "brisque", "p50_ms": 26.4, "p95_ms": 28.5}],
            },
            {"label": "c7g-stock", "images_per_sec": 25.3, "stages": []},
        ],
        "stages": [
            {"stage": "brisque", "host_label": "stock", "p50_ms": 99.0, "p95_ms": 99.0},
            {"stage": "brisque", "host_label": "c7g-stock", "p50_ms": 29.8, "p95_ms": 32.1},
        ],
    }
    (root / "s3" / "bench" / "results" / "latest" / "results.json").write_text(json.dumps(doc))
    hosts = client.get("/bench", headers=judge()).json()["hosts"]
    assert hosts[0]["images_per_sec"] == 28.4 and hosts[0]["stages"][0]["p50_ms"] == 26.4
    assert "instance_type" not in hosts[0]
    assert hosts[1]["stages"] == [{"name": "brisque", "p50_ms": 29.8, "p95_ms": 32.1}]


def test_bench_missing(client: TestClient, root: Any) -> None:
    (root / "s3" / "bench" / "results" / "latest" / "results.json").unlink()
    assert client.get("/bench", headers=judge()).status_code == 404


def test_handler_is_mangum() -> None:
    from mangum import Mangum

    import app.main

    assert isinstance(app.main.handler, Mangum)


# ---------------------------------------------------------------- resume paths


def _rows(root: Any) -> dict[str, dict[str, Any]]:
    path = root / "dynamodb" / "keepframe-decisions.jsonl"
    return {r["sk"]: r for r in (json.loads(line) for line in path.read_text().splitlines())}


def test_no_resume_path_applies_verdicts_directly(client: TestClient, root: Any) -> None:
    body = {
        "decision": "override",
        "note": "8831 has the better expression",
        "human_verdict": {"keep": ["p_8831"], "reject": ["p_8830", "p_8832", "p_8833"]},
    }
    r = client.post("/approvals/g_412", headers=team(), json=body)
    assert r.status_code == 200, r.text
    assert r.json()["resume"] == {"via": "api-direct", "status": 200, "ack": {"written": 4}}
    rows = _rows(root)
    keep = rows["PHOTO#p_8831"]
    assert keep["verdict"] == "keep" and keep["decided_by"] == "human" and keep["human_source"] == "ui"
    assert keep["confidence"] != 1.0  # the stored model confidence is not overwritten
    assert keep["reason_codes"] == ["KEEP_BEST_OF_GROUP"]
    assert keep["gsi1pk"] == f"EVENT#{EVENT_ID}#VERDICT#keep"
    assert rows["PHOTO#p_8833"]["reason_codes"] == ["BURST_NOT_BEST"]
    assert rows["GROUP#g_412"]["resolved_via"] == "api-direct"
    assert "gsi3pk" not in rows["GROUP#g_412"]
    assert rows[f"RUN#{RUN_ID}"]["resume_decisions"][0]["human_verdict"]["keep"] == ["p_8831"]
    g = client.get(f"/events/{EVENT_ID}/groups?group_id=g_412", headers=team()).json()["items"][0]
    assert g["verdict_counts"] == {"keep": 1, "reject": 3, "review": 0}


def test_resume_path_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import agent_resume, config

    for name in ("CULL_AGENT_RUNTIME_ARN", "CULL_AGENT_URL"):
        monkeypatch.delenv(name, raising=False)
    # The removed Fargate fallback's env vars no longer select anything.
    monkeypatch.setenv("CULL_AGENT_TASKDEF_ARN", "td")
    monkeypatch.setenv("CULL_CLUSTER_ARN", "c")
    config.settings.cache_clear()
    assert agent_resume.configured_path() is None
    monkeypatch.setenv("CULL_AGENT_URL", "http://127.0.0.1:8080/invocations")
    config.settings.cache_clear()
    assert agent_resume.configured_path() == "http"
    monkeypatch.setenv(
        "CULL_AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:ap-south-1:<AWS_ACCOUNT_ID>:runtime/x"
    )
    config.settings.cache_clear()
    assert agent_resume.configured_path() == "agentcore"
    config.settings.cache_clear()


# ---------------------------------------------------------------- real-data shapes


def test_group_label_from_worker_ids() -> None:
    from app.main import group_label

    ev = "00000000-0000-4000-8000-00000000e009"
    assert group_label(f"g_{ev}_12", ev) == "Group 12"
    assert group_label("g_412", ev) == "Group 412"
    assert group_label("cluster-abcdef0123456789", ev) == "cluster-abcd…"
    assert group_label("burst-a", ev) == "burst-a"


def test_groups_carry_short_label(client: TestClient) -> None:
    items = client.get(f"/events/{EVENT_ID}/groups", headers=team()).json()["items"]
    assert [g["group_label"] for g in items] == ["Group 410", "Group 411", "Group 412"]
    (a,) = client.get("/approvals", headers=team()).json()["items"]
    assert a["group_label"] == "Group 412"


def _write_rows(root: Any, rows: list[dict[str, Any]]) -> None:
    path = root / "dynamodb" / "keepframe-decisions.jsonl"
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_worker_run_item_is_listed_and_found(client: TestClient, root: Any) -> None:
    """Shape written by keepframe/worker/run_record.py: sk RUN#<run_id>, gsi1pk RUNS, worker-style id."""
    from app import backend

    rid = f"run_{EVENT_ID}_20261005T124646Z"
    _write_rows(
        root,
        [
            {
                "pk": f"EVENT#{EVENT_ID}",
                "sk": f"RUN#{rid}",
                "gsi1pk": "RUNS",
                "run_id": rid,
                "event_id": EVENT_ID,
                "status": "succeeded",
                "started_at": "2026-10-13T12:46:46.071Z",
                "verdict_counts": {"keep": 609, "reject": 42, "review": 10},
                "escalation_count": 22,
                "llm_provider": "none",
            }
        ],
    )
    backend.reset_backend()
    items = client.get("/runs", headers=team()).json()["items"]
    assert items[0]["run_id"] == rid and items[0]["escalation_count"] == 22
    # The judge sees the id with the event uuid aliased, and that id resolves back.
    seen = client.get("/runs", headers=judge()).json()["items"][0]["run_id"]
    assert seen == "run_ev_01_20261005T124646Z"
    assert client.get(f"/runs/{seen}", headers=judge()).json()["verdict_counts"]["review"] == 10
    assert client.get(f"/runs/{seen}/trace", headers=judge()).status_code == 200
    assert client.get(f"/runs/{seen}/agent", headers=judge()).json()["run_id"] == seen
    assert client.get(f"/runs/{rid}", headers=team()).json()["verdict_counts"]["review"] == 10


def test_runs_fallback_when_index_partition_is_empty(client: TestClient, root: Any) -> None:
    from app import backend

    path = root / "dynamodb" / "keepframe-decisions.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for r in rows:
        if r.get("gsi1pk") == "RUNS":
            del r["gsi1pk"]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    backend.reset_backend()
    assert [r["run_id"] for r in client.get("/runs", headers=judge()).json()["items"]] == [
        RUN_ID,
        STOCK_RUN_ID,
    ]


def test_event_metas_are_cached(client: TestClient, root: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import data
    from app.backend import backend

    data.clear_meta_cache()
    calls = {"n": 0}
    real = backend().list_keys

    def counting(*a: Any, **k: Any) -> list[str]:
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(backend(), "list_keys", counting)
    client.get("/runs", headers=judge())
    client.get("/events", headers=judge())
    assert calls["n"] == 1


# ---------------------------------------------------------------- who decided


def test_decision_source_rules() -> None:
    from app.views import decision_source, photo_view

    agent = {"photo_id": "p1", "decided_by": "agent", "confidence": 0.7}
    assert decision_source(agent) == "agent" and photo_view(agent)["confidence"] == 0.7
    assert decision_source({"decided_by": "worker"}) == "agent"
    # Relabelled real-run row: scripted, with a placeholder 1.0 on the PHOTO# and the agent's 0.5 on the GROUP#.
    scripted = {
        "photo_id": "p1",
        "decided_by": "scripted",
        "human_source": "scripted",
        "confidence": 1,
        "reason_text": "Auto-approved by a script (no human review)",
        "human_note": "auto-approved as proposed (scripted smoke run)",
    }
    view = photo_view(scripted, {"confidence": 0.5})
    assert view["decision_source"] == "scripted" and view["confidence"] == 0.5
    assert view["reason_text"] == "Auto-approved by a script (no human review)"
    assert "confidence" not in photo_view(scripted)  # never the 1.0 placeholder
    assert photo_view({**scripted, "agent_confidence": 0.42})["confidence"] == 0.42
    # Person channels need an explicit stamp.
    for ch in ("ui", "file", "http"):
        assert decision_source({"decided_by": "human", "human_source": ch}) == ch
    assert decision_source({"decided_by": "human"}, {"human_decision": {"human_source": "file"}}) == "file"
    # Unstamped human rows are unknown, never a person, whatever the note says.
    assert decision_source({"decided_by": "human", "human_note": "eyes better in 8831"}) == "unknown"
    assert decision_source({"decided_by": "human"}, {"human_decision": {"decision": "approve"}}) == "unknown"
    legacy = photo_view(
        {"photo_id": "p", "decided_by": "human", "confidence": 1, "reason_text": "Decided by a person"}
    )
    assert legacy["decision_source"] == "unknown" and "confidence" not in legacy
    assert legacy["reason_text"] == "Confirmed outside the agent (source not recorded)"


def test_ui_approval_is_stamped_and_rendered_as_a_person(client: TestClient) -> None:
    r = client.post("/approvals/g_412", headers=team(), json={"decision": "approve", "note": "looks right"})
    assert r.status_code == 200, r.text
    assert r.json()["human_decision"]["human_source"] == "ui"
    (g,) = client.get(f"/events/{EVENT_ID}/groups?group_id=g_412", headers=team()).json()["items"]
    by = {m["photo_id"]: m for m in g["members"]}
    assert by["p_8833"]["decision_source"] == "ui"
    assert by["p_8833"]["confidence"] == 0.6  # the agent's proposal confidence, labelled as such in the UI
    assert by["p_8833"]["human_note"] == "looks right"


def test_scripted_answers_render_as_scripted(client: TestClient, root: Any) -> None:
    from app import backend

    path = root / "dynamodb" / "keepframe-decisions.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for r in rows:
        if r["sk"] == "PHOTO#p_8833":
            r.update(
                decided_by="scripted",
                human_source="scripted",
                confidence=1,
                human_note="auto-approved as proposed (scripted smoke run)",
                reason_text="Auto-approved by a script (no human review)",
            )
        if r["sk"] == f"RUN#{RUN_ID}":
            r["approvals_source"] = "scripted"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    backend.reset_backend()
    (g,) = client.get(f"/events/{EVENT_ID}/groups?group_id=g_412", headers=team()).json()["items"]
    m = next(m for m in g["members"] if m["photo_id"] == "p_8833")
    assert m["decision_source"] == "scripted"
    assert m["confidence"] == 0.6  # the GROUP#'s proposal confidence, not the 1.0 placeholder
    photo = client.get(f"/events/{EVENT_ID}/photos/p_8833", headers=team()).json()
    assert photo["decision_source"] == "scripted" and photo["confidence"] == 0.6
    assert client.get(f"/runs/{RUN_ID}", headers=judge()).json()["approvals_source"] == "scripted"


def test_trace_marks_answer_sources() -> None:
    from app.views import trace_view

    legacy = {
        "seq": 22,
        "type": "interrupt_resumed",
        "decisions": [[{"id": "g", "note": "approved as proposed (reviewer)", "human_verdict": {}}]],
    }
    assert trace_view(legacy)["answered_by"] == "unknown"  # pre-stamp lines are never a person
    stamped = {**legacy, "decisions": [[{"id": "g", "human_source": "scripted"}]]}
    assert trace_view(stamped)["answered_by"] == "scripted"
    ui = {
        "seq": 23,
        "type": "tool_result",
        "tool": "request_human_approval",
        "result": {"decisions": [{"id": "g", "human_source": "ui"}]},
    }
    assert trace_view(ui)["answered_by"] == "ui"
    mixed = {
        "seq": 24,
        "type": "interrupt_resumed",
        "decisions": [{"human_source": "ui"}, {"human_source": "scripted"}],
    }
    assert trace_view(mixed)["answered_by"] == "scripted"
    assert "answered_by" not in trace_view({"seq": 1, "type": "tool_call", "tool": "score_sharpness"})


def test_photo_and_members_pass_through_stamps_verbatim(client: TestClient) -> None:
    photo = client.get(f"/events/{EVENT_ID}/photos/p_8810", headers=team()).json()
    assert photo["human_source"] == "scripted" and photo["decided_by"] == "scripted"
    assert photo["agent_confidence"] == 0.55 and photo["confidence"] == 0.55  # never the stored 1.0
    assert photo["decision_source"] == "scripted"
    agent = client.get(f"/events/{EVENT_ID}/photos/p_8811", headers=team()).json()
    assert agent["agent_confidence"] == agent["confidence"] and "human_source" not in agent
    (g,) = client.get(f"/events/{EVENT_ID}/groups?group_id=g_410", headers=team()).json()["items"]
    by = {m["photo_id"]: m for m in g["members"]}
    assert by["p_8810"]["human_source"] == "scripted" and by["p_8810"]["agent_confidence"] == 0.55
    listed = client.get(f"/events/{EVENT_ID}/groups", headers=team()).json()["items"][0]["members"]
    assert next(m for m in listed if m["photo_id"] == "p_8810")["human_source"] == "scripted"


# ---------------------------------------------------------------- E07 shape: worker groups + agent stub, paused run


@pytest.fixture()
def e07(client: TestClient, root: Any) -> TestClient:
    from app import backend, data
    from tests.fixtures.make_fixtures import add_event3

    add_event3(root)
    backend.reset_backend()
    data.clear_meta_cache()
    return client


def test_groups_merge_worker_file_with_agent_stub(e07: TestClient) -> None:
    from tests.fixtures.make_fixtures import BURST, EVENT3

    p1 = e07.get(f"/events/{EVENT3}/groups?limit=24", headers=team()).json()
    assert p1["source"] == "merged" and p1["total"] == 30
    assert len(p1["items"]) == 24 and p1["next_cursor"]
    first, burst = p1["items"][0], p1["items"][1]
    # The agent's stub contributes approval state; groups.json contributes membership and span.
    assert first["group_label"] == "Group 1" and first["size"] == 2 and first["span_s"] == 0.0
    assert [m["photo_id"] for m in first["members"]] == ["p_e7_a", "p_e7_b"]
    assert first["pending_approval"] is True
    assert burst["size"] == 30 and len(burst["members"]) == 30 and burst["best_photo_id"] == BURST[7]
    assert all(g["size"] > 0 and g["members"] for g in p1["items"])
    p2 = e07.get(f"/events/{EVENT3}/groups?limit=24&cursor={p1['next_cursor']}", headers=team()).json()
    assert len(p2["items"]) == 6 and p2["next_cursor"] is None
    assert len({g["group_id"] for g in p1["items"] + p2["items"]}) == 30
    # A cursor from one event is refused on another.
    assert e07.get(f"/events/{EVENT_ID}/groups?cursor={p1['next_cursor']}", headers=team()).status_code == 400
    (one,) = e07.get(f"/events/{EVENT3}/groups?group_id=g_{EVENT3}_1", headers=team()).json()["items"]
    assert one["size"] == 2 and one["pending_approval"] is True
    # Group detail of the 30-frame burst: every member comes from groups.json, not just rows in DynamoDB.
    (b,) = e07.get(f"/events/{EVENT3}/groups?group_id=g_{EVENT3}_2", headers=team()).json()["items"]
    assert len(b["members"]) == 30 and [m["photo_id"] for m in b["members"]] == BURST
    assert sum(m["is_best"] for m in b["members"]) == 1 and b["members"][7]["is_best"]


def test_dynamo_only_group_takes_members_from_its_proposal(e07: TestClient, root: Any) -> None:
    from app import data
    from tests.fixtures.make_fixtures import EVENT3

    (root / "s3" / "results" / EVENT3 / "groups.json").unlink()
    data.clear_meta_cache()
    body = e07.get(f"/events/{EVENT3}/groups", headers=team()).json()
    assert body["source"] == "dynamodb" and body["total"] == 1
    assert [m["photo_id"] for m in body["items"][0]["members"]] == ["p_e7_a", "p_e7_b"]


def test_paused_run_has_no_negative_duration(e07: TestClient) -> None:
    from tests.fixtures.make_fixtures import PAUSED_RUN_ID

    run = e07.get(f"/runs/{PAUSED_RUN_ID}", headers=judge()).json()
    assert run["status"] == "awaiting_approval" and "duration_s" not in run
    assert run["paused_at"] == "2026-10-05T17:11:17.529Z"
    done = e07.get(f"/runs/{STOCK_RUN_ID}", headers=judge()).json()
    assert done["duration_s"] == 465.0  # 17:02:10 → 17:09:55


def test_duration_rules() -> None:
    from app.views import run_duration_s

    base = {"status": "succeeded", "started_at": "2026-10-05T17:10:28Z"}
    assert run_duration_s({**base, "ended_at": "2026-10-05T17:09:11Z"}) is None  # ended before it started
    assert run_duration_s({**base, "ended_at": "2026-10-05T17:09:11Z", "wall_s": 51.2}) == 51.2
    assert run_duration_s({**base, "status": "awaiting_approval", "ended_at": "2026-10-05T17:30:00Z"}) is None
    assert run_duration_s({**base, "ended_at": "2026-10-05T17:11:28Z"}) == 60.0


def test_split_run_shape_and_old_shape_escalations() -> None:
    from app.views import run_view

    split = {
        "pk": "EVENT#e",
        "sk": "RUN#r",
        "status": "succeeded",
        "started_at": "2026-10-05T17:06:42Z",
        "ended_at": "2026-10-05T17:20:14Z",
        "worker_escalations": 22,
        "agent_escalations": 10,
        "worker_wall_s": 51.0,
        "agent_wall_s": 760.0,
        "photos_processed": 661,
        "agent_photos": 21,
        "escalation_count": 99,
    }
    v = run_view(split)
    assert v["duration_s"] == 812.0 and v["escalations"] == {"worker": 22, "agent": 10}
    assert v["agent_photos"] == 21 and v["worker_escalations"] == 22
    paused = {**split, "status": "awaiting_approval", "paused_at": "2026-10-05T17:11:17Z"}
    paused.pop("ended_at")
    pv = run_view(paused)
    assert "duration_s" not in pv and pv["paused_at"] == "2026-10-05T17:11:17Z"
    assert "paused_at" not in run_view({**split, "paused_at": "2026-10-05T17:11:17Z"})  # only while paused
    # Old shape: one escalation_count, passed through unattributed (E07: 686 from the worker on an agent run).
    old_e07 = run_view({"sk": "RUN#e07", "escalation_count": 686, "llm_provider": "anthropic"})
    assert "escalations" not in old_e07 and old_e07["escalation_count"] == 686
    # Old shape with inconsistent stamps but a worker wall time.
    old = {
        "sk": "RUN#o",
        "status": "succeeded",
        "started_at": "2026-10-05T17:10:28Z",
        "ended_at": "2026-10-05T17:09:11Z",
        "wall_s": 51.2,
    }
    assert run_view(old)["duration_s"] == 51.2


def test_run_kind_chip_source(e07: TestClient) -> None:
    from app.views import run_kind
    from tests.fixtures.make_fixtures import PAUSED_RUN_ID

    runs = {r["run_id"]: r for r in e07.get("/runs?limit=50", headers=judge()).json()["items"]}
    assert runs[PAUSED_RUN_ID]["run_kind"] == "deployed"  # run_aws_* with no stored kind
    cal = runs["run_e7_policy_rerun_2"]
    assert cal["run_kind"] == "local_calibration" and cal["run_note"].startswith("policy rerun")
    assert "run_kind" not in runs[RUN_ID]  # unknown origin: no chip
    assert run_kind({"run_id": "run_infra_smoke_1"}) == "deployed"
    assert run_kind({"run_id": "run_aws_x", "run_kind": "local"}) == "local"  # a stored kind wins
    assert run_kind({"run_id": "run_x", "run_kind": "agentcore"}) == "agentcore"  # verbatim
    assert run_kind({"run_id": "run_x", "run_kind": "Bad Kind!"}) is None


def test_lead_test_rows_are_neither_a_person_nor_a_script(client: TestClient) -> None:
    from app.views import decision_source, trace_view

    photo = client.get(f"/events/{EVENT_ID}/photos/p_8820", headers=team()).json()
    assert photo["decision_source"] == "lead_test" and photo["human_source"] == "lead_test"
    assert photo["reason_text"] == "Team mechanism test, not a photographer judgement"
    assert photo["confidence"] == 0.41  # agent's own value, never the 1.0 placeholder
    assert decision_source({"decided_by": "human", "human_source": "lead_test"}) == "lead_test"
    line = {
        "seq": 9,
        "type": "interrupt_resumed",
        "decisions": [{"human_source": "ui"}, {"human_source": "lead_test"}],
    }
    assert trace_view(line)["answered_by"] == "lead_test"


# ---------------------------------------------------------------- labeller / team mechanism test


def test_lead_mechanism_test_answer_is_never_a_person(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, root: Any
) -> None:
    fake = _use_agentcore(monkeypatch, FakeAgentCore({"status": "running", "run_id": RUN_ID}))
    r = client.post(
        "/approvals/g_412",
        headers=team(),
        json={"decision": "approve", "note": "resume path check", "labeller": "lead-mechanism-test"},
    )
    assert r.status_code == 200, r.text
    hd = r.json()["human_decision"]
    assert hd["human_source"] == "lead_test" and hd["decided_by"] == "lead_test"
    assert hd["labeller"] == "lead-mechanism-test"
    rows = _rows(root)
    stored = rows["GROUP#g_412"]["human_decision"]
    assert stored["human_source"] == "lead_test" and stored["decided_by"] == "lead_test"
    assert stored["labeller"] == "lead-mechanism-test"
    (d,) = rows[f"RUN#{RUN_ID}"]["resume_decisions"]
    assert d["human_source"] == "lead_test" and d["labeller"] == "lead-mechanism-test"
    # The agent gets the labeller too, so it stamps lead_test on its own writes.
    (call,) = fake.calls
    (sent,) = call["payload"]["decisions"]
    assert sent["labeller"] == "lead-mechanism-test" and sent["human_source"] == "lead_test"
    run = client.get(f"/runs/{RUN_ID}", headers=judge()).json()
    assert run["last_answer_source"] == "lead_test" and run["last_labeller"] == "lead-mechanism-test"


def test_lead_mechanism_test_api_direct_writes_lead_test_rows(client: TestClient, root: Any) -> None:
    body = {
        "decision": "override",
        "human_verdict": {"keep": ["p_8831"], "reject": ["p_8830", "p_8832", "p_8833"]},
        "labeller": "lead-mechanism-test",
    }
    assert client.post("/approvals/g_412", headers=team(), json=body).status_code == 200
    rows = _rows(root)
    for pid in ("p_8830", "p_8831", "p_8832", "p_8833"):
        row = rows[f"PHOTO#{pid}"]
        assert row["decided_by"] == "lead_test" and row["human_source"] == "lead_test", pid
        assert row["labeller"] == "lead-mechanism-test"
        assert not row["reason_text"].startswith("Decided by a person")
    g = client.get(f"/events/{EVENT_ID}/groups?group_id=g_412", headers=team()).json()["items"][0]
    keep = next(m for m in g["members"] if m["photo_id"] == "p_8831")
    assert keep["decision_source"] == "lead_test" and keep["labeller"] == "lead-mechanism-test"
    photo = client.get(f"/events/{EVENT_ID}/photos/p_8831", headers=team()).json()
    assert photo["decision_source"] == "lead_test" and photo["labeller"] == "lead-mechanism-test"


def test_other_labeller_is_verbatim_and_stays_a_person(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, root: Any
) -> None:
    fake = _use_agentcore(monkeypatch, FakeAgentCore({"status": "running", "run_id": RUN_ID}))
    r = client.post("/approvals/g_412", headers=team(), json={"decision": "approve", "labeller": "photog-a"})
    assert r.status_code == 200, r.text
    stored = _rows(root)["GROUP#g_412"]["human_decision"]
    assert stored["human_source"] == "ui" and stored["decided_by"] == "human"
    assert stored["labeller"] == "photog-a"
    (sent,) = fake.calls[0]["payload"]["decisions"]
    assert sent["labeller"] == "photog-a" and sent["human_source"] == "ui"
    run = client.get(f"/runs/{RUN_ID}", headers=judge()).json()
    assert run["last_answer_source"] == "ui" and run["last_labeller"] == "photog-a"


def test_review_desk_answer_has_no_labeller(client: TestClient, root: Any) -> None:
    assert client.post("/approvals/g_412", headers=team(), json={"decision": "approve"}).status_code == 200
    stored = _rows(root)["GROUP#g_412"]["human_decision"]
    assert stored["human_source"] == "ui" and stored["decided_by"] == "human" and "labeller" not in stored


@pytest.mark.parametrize("bad", ["x" * 65, "a\nb", "<script>", {"labeller": 1}])
def test_labeller_is_validated(client: TestClient, bad: Any) -> None:
    body = {"decision": "approve", **(bad if isinstance(bad, dict) else {"labeller": bad})}
    assert client.post("/approvals/g_412", headers=team(), json=body).status_code == 422
    assert (
        client.post(
            "/approvals/g_412", headers=team(), json={"decision": "approve", "human_source": "scripted"}
        ).status_code
        == 422
    )


def test_rows_stored_as_ui_with_the_lead_labeller_read_as_lead_test() -> None:
    """The hand relabel of the 2026-10-08 AgentCore answer: a labeller stamp alone is enough."""
    from app.views import decision_source, last_answer, trace_view

    group = {"human_decision": {"human_source": "ui", "labeller": "lead-mechanism-test"}}
    assert decision_source({"decided_by": "human", "human_source": "ui"}, group) == "lead_test"
    assert decision_source({"decided_by": "human", "labeller": "lead-mechanism-test"}) == "lead_test"
    line = {
        "type": "interrupt_resumed",
        "seq": 3,
        "decisions": [{"human_source": "ui", "labeller": "lead-mechanism-test"}],
    }
    assert trace_view(line)["answered_by"] == "lead_test"
    assert last_answer({"resume_decisions": [{"human_source": "ui", "labeller": "lead-mechanism-test"}]}) == {
        "last_answer_source": "lead_test",
        "last_labeller": "lead-mechanism-test",
    }
    assert last_answer({}) == {}


# ---------------------------------------------------------------- per-run results keys (2026-10-09)


def test_per_run_results_keys_win_over_the_unversioned_copy(client: TestClient, root: Any) -> None:
    from app import data

    d = root / "s3" / "results" / EVENT_ID
    (d / RUN_ID).mkdir()
    summary = json.loads((d / "summary.json").read_text())
    (d / RUN_ID / "summary.json").write_text(
        json.dumps({**summary, "wall_s": 12.5, "opencv_hal": "per-run-hal"})
    )
    run = client.get(f"/runs/{RUN_ID}", headers=team()).json()
    assert run["opencv_hal"] == "per-run-hal" and run["wall_s"] == 12.5  # only the versioned copy has these
    # Another run of the same event without its own folder falls back to the unversioned latest copy.
    assert data.result_keys(EVENT_ID, "groups.json", {"run_id": STOCK_RUN_ID}) == [
        f"results/{EVENT_ID}/{STOCK_RUN_ID}/groups.json",
        f"results/{EVENT_ID}/groups.json",
    ]
    assert (
        data.read_result_json(EVENT_ID, "summary.json", {"run_id": STOCK_RUN_ID})["thresholds_sha"]
        == "3f9a1c2"
    )


def test_results_run_prefix_must_be_this_events_shape() -> None:
    from app import data

    ok = {"run_id": "run_a", "results_run_prefix": f"results/{EVENT_ID}/run_b/"}
    assert data.run_prefix(EVENT_ID, ok) == f"results/{EVENT_ID}/run_b/"
    for bad in ("results/other/run_b/", f"results/{EVENT_ID}/../x/", f"results/{EVENT_ID}/a/b/"):
        assert (
            data.run_prefix(EVENT_ID, {"run_id": "run_a", "results_run_prefix": bad})
            == f"results/{EVENT_ID}/run_a/"
        )
    assert data.run_prefix(EVENT_ID, {"run_id": "../x"}) is None
    assert data.run_prefix(EVENT_ID, None) is None


def test_stored_source_category_shown_neutral(client: TestClient) -> None:
    """A stored source category (the source word) is shown as its neutral label to judge and team."""
    from app import data

    src = "".join(map(chr, (119, 101, 100, 100, 105, 110, 103))).capitalize()
    metas = data.event_metas()
    original = metas[0].get("category")
    metas[0]["category"] = src
    try:
        for headers in (judge, team):
            for path in ("/events", f"/runs/{RUN_ID}", "/events/ev_01/groups"):
                body = client.get(path, headers=headers()).json()
                assert src.lower() not in json.dumps(body).lower(), path
                event = body["items"][0] if path == "/events" else body["event"]
                assert event["category"] == "Ceremony", path
    finally:
        metas[0]["category"] = original

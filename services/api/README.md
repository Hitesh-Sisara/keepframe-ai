# KeepFrame API (`services/api`)

FastAPI on Lambda arm64 (Mangum). Handler `app.main.handler`. Routes are listed below; every route needs
`Authorization: Bearer <token>`.

| Method | Path | Token | Notes |
|---|---|---|---|
| GET | `/health` | judge, team | `{ok, role, api_version}` |
| GET | `/events` | judge, team | event index from `manifests/*.meta.json`; `label` "Event N", category, counts. No source ids; `name` only with `CULL_SHOW_EVENT_NAMES=1` |
| GET | `/events/{event_id}/groups` | judge, team | paged (`limit`, `cursor`); DynamoDB `GROUP#` items, falls back to `results/<eventId>/groups.json`; `?group_id=` returns one group with full member scores |
| GET | `/events/{event_id}/photos/{photo_id}` | judge, team | scores, reasons, presigned proxy + contact-sheet URLs |
| POST | `/runs` | team | `{event_id}` (`flavor` accepts only `stock`) → RUN# item + `StartExecution` on `CULL_STATE_MACHINE_ARN`; 409 if a run is active |
| GET | `/runs`, `/runs/{run_id}` | judge, team | paged; `opencv_version`, `cv2_file`, counts, cost; `run_kind` and `agent_host` exactly as stored |
| GET | `/runs/{run_id}/agent` | judge, team | read-only: configured resume `path` (`agentcore`\|`http`\|`none`), `session_id`, and for AgentCore a live `{"mode":"status"}` probe (`live.status` running\|paused\|done\|error\|unreachable); active runs only, cached 5 s |
| GET | `/runs/{run_id}/trace` | judge, team | paged by `after` (seq) + `limit`; falls back to `results/<eventId>/trace.jsonl`; `pivots[]` marks the "vision changed the next action" steps; `?group_id=` filters to one group's chain |
| GET | `/approvals` | team | GSI3 `PENDING` queue |
| POST | `/approvals/{id}` | team | `{decision: approve|override, note, human_verdict?, labeller?}` (`labeller` ≤ 64 chars; `lead-mechanism-test` stores `human_source = decided_by = lead_test`, any other value is kept verbatim with `human_source ui`; it is passed to the agent in the resume decisions) → stores the human decision on the item and as `resume_decisions` on RUN#, then resumes the agent (see below) |
| GET | `/img/{ev}/{p\|s}/{alias}.jpg?exp&sig` | link is the credential | a frame (`p`) or contact sheet (`s`) by alias; HMAC-signed, expires with `CULL_PRESIGN_TTL_S`; bytes are streamed (no redirect, no S3 key) |
| GET | `/bench` | judge, team | `bench/results/latest/results.json` normalised to the contract shape `{generated_at, hosts:[{label, instance_type, …, stages:[{name,p50_ms,p95_ms}]}]}` (old flat aliases accepted) |

Hosting and inference (docs/AGENT.md "AgentCore Runtime", "Model provider"): the agent runs on Amazon
Bedrock AgentCore Runtime; its LLM calls go to Claude on Amazon Bedrock (default) or an optional Anthropic-compatible endpoint. The API never calls
a model.

Agent resume, first configured path wins: (1) AgentCore `InvokeAgentRuntime` on `CULL_AGENT_RUNTIME_ARN`
(infra resolves it from SSM `/keepframe/agent-runtime-arn`) with `{mode: "resume", run_id, decisions}` and
`runtimeSessionId = session_id_for(run, run_id)` (RUN# `agent_session_id` if ≥ 33 chars, else
`f"keepframe-{run_id}".ljust(33, "0")`; the workflow Lambdas must use the same rule). The response stream
(read up to 64 KB) carries the agent's JSON `{status: running|paused|done|error, ...}`; the API returns it as
`resume.status` and sets RUN# `status` at once (paused → `awaiting_approval`, done → `succeeded`, error →
`failed`) unless the agent has written RUN# since. A `ClientError` (RuntimeClientError, Throttling, …), an
HTTP ≥ 400 or an agent `{"status":"error"}` reply counts as "did not resume". (2) POST the same payload to
`CULL_AGENT_URL` (the local agent_runtime container's `/invocations`); (3) none configured: the API
writes the human verdicts onto the PHOTO# items itself (`decided_by=human`, confidence 1.0) and resolves the approval (`resolved_via=api-direct`). A configured
path that fails returns 502 and leaves the approval pending with `resume_error`. The Fargate `RunTask`
fallback was removed on 2026-10-09 with the cull-agent task definition; its `CULL_AGENT_TASKDEF_ARN` /
`CULL_CLUSTER_ARN` / `CULL_AGENT_*` network env vars are ignored.

GET `/runs/{run_id}` also returns `llm_provider`, `planner_model`, `reasons_model` when the agent writes them.

## Client ids are aliases (security review S-1)

A source's public CDN may serve `<eventId>/<photoId>.webp` without auth, so no real id pair may reach the judge.
Storage keeps real ids; the API translates at the boundary (`app/aliases.py`):

| id | alias | rule |
|---|---|---|
| event | `ev_NN` | 1-based manifest export order, the same N as the "Event N" label |
| photo | `ph_<10 hex>` | HMAC-SHA256(alias key, event/photo); 40 bits so a 2,442-photo event has no collisions |
| worker group `g_<eventId>_<n>` | `g_ev_NN_<n>` | any other group id: `gx_<10 hex>` |
| run `run_<eventId>_<ts>` | `run_ev_NN_<ts>` | ULID run ids are unchanged |

- Every judge-token JSON response (errors included) goes through one scrub pass: a known id becomes its alias,
  UUIDs inside longer strings (trace args/results/reasoning, reason codes such as `DUPLICATE_OF:<id>`, error
  text) are mapped, and any unknown UUID is redacted to `<id>`. `contact_sheet_key`, `results_run_prefix`,
  `results_prefix`, `execution_arn` are dropped.
- Path and query params accept aliases. Raw source ids are refused (404) for the judge and accepted for the team.
  Team responses keep real ids (ops, the labelling CSV export) unless `CULL_ALIAS_TEAM=1`.
- Image URLs are `/img/...` links, never presigned S3 URLs (the key would show the uuid pair).
- The judge sees `error_code` (e.g. `ClientError`), never `RUN#.error`, `resume_error` or the agent probe's
  error text (S-13).
- The alias key comes from Secrets Manager `CULL_ALIAS_SECRET_ID` (default `keepframe/alias-key`, a plain
  string or `{"key": "..."}`, ≥ 16 chars), cached 5 minutes. If it can't be read, aliased responses fail
  closed with 503. Rotating the key changes every `ph_`/`gx_` alias and invalidates outstanding image links.

The judge token can only issue GET requests; any other method returns 403 before the route runs.
Presigned URLs expire after `CULL_PRESIGN_TTL_S` (default 600 s, hard cap 900 s).

## Environment (names only)

`WORK_BUCKET`, `CULL_DECISIONS_TABLE`, `CULL_TRACES_TABLE`, `CULL_GSI1_NAME`/`CULL_GSI2_NAME`/`CULL_GSI3_NAME`
(defaults `gsi1`/`gsi2`/`gsi3`), `CULL_STATE_MACHINE_ARN`, `CULL_AGENT_RUNTIME_ARN`,
`CULL_AGENT_RUNTIME_QUALIFIER`, `CULL_AGENT_URL`, `CULL_JUDGE_SECRET_ID`, `CULL_TEAM_SECRET_ID`
(defaults `keepframe/judge-token`, `keepframe/team-token`), `CULL_ALIAS_SECRET_ID`, `CULL_ALIAS_TEAM`,
`CULL_PUBLIC_BASE` (prefix for `/img` links; empty = same origin), `CULL_CORS_ORIGINS`, `CULL_PRESIGN_TTL_S`,
`CULL_SHOW_EVENT_NAMES`. Local only: `CULL_ALIAS_KEY` (test key override), `LOCAL_MODE`, `CULL_LOCAL_ROOT`, `CULL_LOCAL_PUBLIC_BASE`,
`CULL_JUDGE_TOKEN`, `CULL_TEAM_TOKEN`.

A token secret may be a plain string or `{"token": "...", "previous": "..."}`; `previous` stays valid
during a rotation window. Values are cached for 5 minutes per Lambda container.

## Develop and test

```bash
uv pip install -r services/api/requirements-dev.txt     # or: uv sync --extra api --extra dev
cd services/api && python -m pytest -q tests              # synthetic fixtures generated at test time
services/api/dev.sh                                        # uvicorn on :8000 over synthetic data
```

`tests/fixtures/make_fixtures.py` draws abstract scenes with OpenCV 5 (no faces, no real photos): one
event, 3 groups, 10 photos, `docs/AGENT.md` "Trace schema" trace (seq 17–28) and one pending approval.

# Security review (KeepFrame, 2026-10-09)

Scope: API (`services/api`), AgentCore runtime (`services/agent_runtime/app.py`, `cdk/lib/agentcore-stack.ts`),
interim UI path (`cdk/lib/ui-interim-stack.ts`, `services/ui_static/handler.py`), S3/CloudTrail, IAM, the source-store
read-only guarantee, supply chain and privacy. Reviewed on `main` 3094695 plus the infra branch at 047c5aa (Fargate
fallback removal, deployed but not on main yet), and live in account <AWS_ACCOUNT_ID> / ap-south-1. Read-only: no
code was changed, nothing was written to AWS or the source store. Secret values were never printed. Checks were counts or
key-name only.

`LIVE` = verified against the deployed account. `CODE` = read from source only.

**Status refresh 2026-10-09 (main 0c64a30).** Closed: S-1 (id aliasing), S-3 (retention incl. preview/), S-9,
S-13, S-17, S-19, S-21 (dead ECS resume path), S-22, S-24 (real-id prefixes). Still open: S-2 (source key scope,
operator), S-4 (alarm topic has no subscriber, operator), S-5/S-6 (image CVEs and digest pins: not in the b6d4fb4
release, waits for the next one), S-7, S-8 (optional), S-14 (CSP on `/ui`), S-20 (admin MFA, operator). The commands
behind each "Closed" are under "Re-verification" at the end.

## Summary

The auth model holds up live. The judge token gets 401/403 where it should, the runtime has no secret in its
config, the bucket is private, and presigned links last 10 min. Five items needed work before judging (status as of the refresh above):

1. **[Closed live]** The judge API and the trace returned real source event and photo UUIDs, and the source's public CDN serves the
   matching photo with no auth (HEAD 200). The S3 privacy layer can be bypassed by anyone holding the judge token.
2. **[Open, operator]** We cannot prove the source store's key is read-only. It came from the source owner's prod env file, and its
   scope isn't visible to our account token. The store's analytics show PutObject and DeleteObject on the source
   buckets this week, from unknown callers (probably the owner's own traffic).
3. **[Closed live]** Customer pixels outside the 7-day lifecycle: `bench/5k/` (5,000 real processed images, 1.8 GB) and
   `sheets/` (contact sheets with faces) never expire. RESPONSIBLE_USE says sheets are kept "for the competition
   window". The lifecycle rules must match that wording.
4. **[Partly fixed, user]** The `keepframe-opencv26` budget ($120) has 0 notifications, and both keepframe SNS topics have 0
   subscriptions. Alarms and budget breaches reach nobody.
5. **[Open, infra-2]** Live images carry 2 CRITICAL CVEs (curl, worker image) and 7 to 8 HIGH ones (agent, API). Base images are
   pinned by tag, not digest, except `Dockerfile.sync`.

## Findings

| # | Severity | Finding | Evidence (command, LIVE/CODE) | Fix | Status |
|---|---|---|---|---|---|
| 1 | High | Judge-visible responses carry real source ids. `GET /events` and `/events/{id}/groups` return the source event UUID and photo UUIDs, group ids embed the event UUID (`g_<uuid>_<n>`), and `/runs/{id}/trace` tool-call args carry them too (17 of 19 tool calls on the E03 AgentCore run). `$SOURCE_CDN_PROCESSED<eventId>/<photoId>.webp` returns 200 with no auth for a returned pair, so the presigned-S3 safeguard and the "Event 1" labels can be bypassed. JUDGE_ACCESS.md says ids are not shown, but only names are hidden. | `curl -H "Authorization: Bearer $JUDGE" $API/events/<id>/groups`, then `curl -I "$SOURCE_CDN_PROCESSED<eventId>/<photoId>.webp"` → 200 image/webp (LIVE) | Give the API an opaque per-deployment alias, for example HMAC(event_id, secret) or E-index aliases for events and `p_<hmac8>` for photos, applied in `event_view`/`_member_view`/`trace_view`, with the reverse mapping server-side. At minimum, alias in API responses and the trace view. The S3 keys can stay as they are. Update JUDGE_ACCESS/RESPONSIBLE_USE wording until fixed. | **Closed (LIVE 2026-10-09)**: see R-1. Residual: real ids already known to someone still open on the source's public CDN (source-side, out of our control) |
| 2 | High | The source store's read-only key scope is unverified. The source-sync secret holds a key copied from the source owner's production env file (operator-held, never committed). It isn't a token visible to our account token (`/accounts/{a}/tokens/{id}` → 404), so read-only cannot be shown. ListBuckets is denied, so it's at least bucket-scoped. The store's analytics for Oct 5–8 show `PutObject`/`DeleteObject(s)` on $SOURCE_BUCKET_PROCESSED and $SOURCE_BUCKET_ORIGINALS (for example processed: 117,156 Put, 1,887 Delete), but the dataset has no per-key dimension. These are most likely the owner's normal traffic. Code only calls GET/HEAD/LIST (see #10). | The store's operations analytics by bucketName/actionType (LIVE); token lookup (LIVE) | Have the source store's owner mint a dedicated key with **Object Read only**, scoped to the 4 buckets and expiring after judging. Put it in `keepframe/source` and revoke the copied prod key's use here. Record the key id and permission in docs (not the value). | Open (operator: dedicated read-only source key) |
| 3 | High | Pixel retention is wider than documented. The lifecycle only expires `raw/`, `originals/`, `proxy/` (7 d). `bench/5k/` holds 5,000 real processed customer images (1.82 GB) with no expiry, and RESPONSIBLE_USE doesn't mention it. `sheets/` (142 contact sheets, faces) is documented as "kept for the competition window" but has no expiry rule to enforce that. `raw/` objects from 2026-10-05 are still present on 10-09, which is expected inside 7 days. | `aws s3api get-bucket-lifecycle-configuration`, `aws s3 ls --summarize` per prefix (LIVE) | Add lifecycle rules: `bench/5k/` 7 d (re-copy for each bench), `sheets/` and `preview/` expiring at end of judging (e.g. 2026-11-15 via date-based rule), `sessions/` same. Mention bench/5k in RESPONSIBLE_USE's table. | **Closed (LIVE)**: sheets/ 7 d, preview/ 7 d, bench/5k/ 30 d, see R-3 |
| 4 | Medium | No one is notified. Budget `keepframe-opencv26` (120 USD) has **0 notifications**. SNS `keepframe-alarms` and `keepframe-alerts` have **0 subscriptions**. CloudWatch alarms and the CullEvent failure notification go nowhere. | `aws budgets describe-notifications-for-budget`, `aws sns list-subscriptions-by-topic` (LIVE) | Add 50/80/100% ACTUAL and 100% FORECASTED notifications (email) to the budget, and one email subscription on both topics (confirm by hand). | Partly fixed: budgets now notify SNS `keepframe-alarms`, which has 0 subscribers (operator: confirm an e-mail subscription) |
| 5 | Medium | Known CVEs in live images. Worker `keepframe:main-ec7cf96` has CRITICAL 2 (curl), HIGH 2 (curl, zlib). Agent `keepframe-agent:main-ec7cf96` has HIGH 7 (util-linux, zlib, gcc-12 libs). API image (Lambda asset) had HIGH 8 (curl-minimal, rpm, pcre2) on main-4a6b721. | `aws ecr describe-image-scan-findings` (LIVE) | Add `apt-get upgrade -y` (Debian) / `dnf upgrade -y` (Lambda base) in the build stage. Drop `curl` from the worker runtime stage (only needed at build). Rebuild and re-scan before the final release. | Open: the in-progress core release from b6d4fb4 does not carry the CVE/digest rebuild. Stays open until the following release is re-scanned |
| 6 | Medium | Base images are pinned by tag. `Dockerfile` (`python:3.12-slim`, Docker Hub), `Dockerfile.agent` (`public.ecr.aws/docker/library/python:3.12-slim-bookworm`), `services/api/Dockerfile` (`public.ecr.aws/lambda/python:3.12-arm64`) are not digest-pinned. Only `Dockerfile.sync` uses `@sha256`. ECR repos are `MUTABLE`. | `grep -n ^FROM Dockerfile* services/*/Dockerfile*` (CODE); `aws ecr describe-repositories` (LIVE) | Pin `FROM ...@sha256:<digest>` (record in a comment with the tag and date). Set ECR tag immutability for `main-*` tags, or switch the repos to IMMUTABLE (release tags are unique already). | Open |
| 7 | Medium | Agent and API Python deps are pinned, not hashed. `requirements.txt` (worker) is a hashed uv export (73 hashes). `services/agent_runtime/requirements-agent.txt` (13 pins) and `services/api/requirements.txt` (19 pins) carry **no hashes**, and the API installs with `--no-deps`. The OpenCV wheel is hash-pinned via `install_opencv_wheel.sh --require-hashes`. | `grep -c -- --hash` per file (CODE) | Generate both from uv.lock with hashes (`uv export --only-group … --hashes`) and install with `--require-hashes`. | Open |
| 8 | Medium | The AgentCore runtime is reachable by IAM only, but without a resource policy. PUBLIC network mode, no `authorizerConfiguration`, no resource policy (`get-resource-policy` returns empty). IAM simulation of `InvokeAgentRuntime` on the runtime ARN: **allowed** for the API Lambda role, the invoke-agent and agent-status Lambda roles, `cdk-…-cfn-exec-role` (AdministratorAccess) and user `admin`. Every other role in the account is implicitDeny. So only SigV4 callers with account admin or the three intended roles can invoke. That's acceptable. A resource policy would make it explicit and survive future broad grants. | `aws iam simulate-principal-policy --action-names bedrock-agentcore:InvokeAgentRuntime` over every role (LIVE) | Optional hardening: attach a resource policy that allows only the three role ARNs (and the account root for admin break-glass). Document the IAM-only posture in ARCHITECTURE. | Open (optional; still no resource policy) |
| 9 | Low | The AgentCore execution role is close to least-privilege, with one extra. One secret (`keepframe/anthropic-??????`), one bucket, two tables, ECR pull on one repo, logs scoped to `/aws/bedrock-agentcore/runtimes/*`. But S3 is `GetObject`+`PutObject` on `bucket/*` (the whole bucket, including `manifests/`, `bench/`), where the agent only needs `proxy/`, `sheets/`, `results/`, `preview/`, `raw/` (read) and `sheets/`, `results/`, `preview/` (write). `logs:DescribeLogGroups` is on `log-group:*` (list only). Lifecycle: idle 900 s, max 8 h. | `aws iam get-role-policy` on AgentRuntimeRole (LIVE); agentcore-stack.ts (CODE) | Prefix-scope the agent's S3 grant. Consider `maxLifetime` 7200 to match the workflow's 2 h agent budget. | **Closed (LIVE)**: see R-9 |
| 10 | Info (pass) | The source read-only guarantee holds in code. Source store: `keepframe/io/sync.py` only calls `src.get_object`. `services/sync/entrypoint.sh` only does `rclone copy src:… s3:…` / `rclone cat s3:…` (the source is never a destination). `scripts/export_manifest_n1ql.py` only paginates `list_objects_v2`. No `put/delete/copy` targets the source client. Couchbase: only `scripts/n1ql_ro.py` talks to :8093, it sends `readonly: true` and rejects mutating statements by regex. Used by `pick_events_n1ql.py` and `export_manifest_n1ql.py` only, both run locally and never in AWS. Postgres stub uses `BEGIN TRANSACTION READ ONLY`. No source API calls: `$SOURCE_CDN_PROCESSED` is used only to build manifest URL strings, never fetched. | `grep -rnE "put_object\|delete_object\|upload_\|rclone (sync\|move\|delete)"`, `grep -rn source_client` (CODE) | Evidence query below. Remaining risk is #2 (credential scope), not code. | Pass |
| 11 | Info (pass) | Judge token enforcement. No token or a bad token → 401. Judge `GET /health` 200 (`role: judge`). Judge `POST /runs` and `POST /approvals/x` → 403 "read-only" (rejected in `authenticate` before routing). Judge `GET /approvals` → 403 "Team token required". `PUT`/`DELETE` → 404 at API Gateway (explicit routes). Tokens come from Secrets Manager with a 5-min cache, constant-time compare, fail closed on an unreadable secret, 48-char generated values, `{token, previous}` rotation. | curl matrix against `$API` (LIVE); auth.py (CODE) | — | Pass (re-checked LIVE after S-1) |
| 12 | Info (pass) | Input validation. Ids are checked against `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$` with no `..` → 422 (`a%23b`, 200-char id). Encoded traversal is rejected by API GW (400). Cursors are validated per query (400). The contact-sheet key regex is limited to `sheets/…`. The `labeller` field is ≤64 chars, `^[A-Za-z0-9 ._:@+/-]{1,64}$`, team-only, and `lead-mechanism-test` maps to `lead_test`. Note: `labeller` is echoed to the judge as `last_labeller` (1 run today). It's free text, so never put a person's name or e-mail in it (use `photog-a` style handles). | curl (LIVE); main.py `ApprovalDecision` (CODE) | Docs: state the handle-only rule for `labeller` in JUDGE_ACCESS / labelling guide. | Pass |
| 13 | Info (pass) | Error leakage is small. 4xx details are fixed strings, the 502 on resume is generic, and `ResumeUnavailable` strings are capped at 200 chars and carry error codes, not stack traces. `RUN#.error` (agent `f"{type(e).__name__}: {e}"[:500]`) is in `RUN_FIELDS` and readable by the judge, and an exception message could contain an S3 key with real ids. 0 runs carry `error` today. FastAPI docs/openapi are disabled (404). | main.py, agent_runtime/app.py, views.py (CODE); `GET /runs` (LIVE) | Map agent errors to a code (`error_code`) for the judge view and keep the message server-side. Fold into #1. | Closed with S-1 (judge error text is scrubbed) |
| 14 | Info (pass) | CORS and the interim UI path. The UI is now same-origin (`/ui` on the same HTTP API), so `CULL_CORS_ORIGINS` is empty and API GW has no CORS config. A cross-origin preflight returns 404 and no ACAO header is sent. That's the safest setting, so keep it empty unless CloudFront is reinstated. `keepframe-ui-static`: GET/HEAD only (POST → 404 at API Gateway, 405 in the handler), assets baked in (no bucket), realpath containment plus `..`/NUL rejection (traversal → 400/404), role = logs only, env `UI_BASE_PATH` only. The built `ui/out-interim` has 0 UUIDs, 0 real-id prefixes, 0 API-key prefixes. HSTS, nosniff, XFO DENY and no-referrer are set. **Missing: Content-Security-Policy.** With the bearer token in `sessionStorage`, a CSP (`default-src 'self'; connect-src 'self' https://*.s3.ap-south-1.amazonaws.com; img-src 'self' https://*.s3.ap-south-1.amazonaws.com data:; frame-ancestors 'none'`) limits token theft by any injected script. The UI routes are unauthenticated by design and share the API stage throttle (20 rps / 40 burst), so static-asset load counts against the API's limit. | curl `/ui/` headers, traversal probes (LIVE); handler.py (CODE) | Add CSP (and `Permissions-Policy`) to `SECURITY_HEADERS`. Next export inlines a bootstrap script, so use a hash or `'unsafe-inline'` for script-src only if needed, and test in the browser. | Open (no CSP on `/ui`) |
| 15 | Info (pass) | Presigned URLs. `CULL_PRESIGN_TTL_S=600`, clamped to 30–900 s in config. A live proxy URL has `X-Amz-Expires=600`. The presigned GET → 200, the same object unsigned → 403. | `GET /events/{id}/groups` → parse `X-Amz-Expires`; curl (LIVE) | — | Pass (now signed `/img` links, see R-1) |
| 16 | Info (pass) | Rate limiting. Stage throttle is 20 rps / 40 burst. The API Lambda has no reserved concurrency, and the account limit is 10 concurrent executions, so a flood of 401s can starve the workflow Lambdas (same account pool). | `aws apigatewayv2 get-stages`, `aws lambda get-account-settings` (LIVE) | Accept for the hackathon, or set API reserved concurrency to 5. | Changed: stage throttle 100 rps / 200 burst (LIVE). Reserved concurrency still unset |
| 17 | Info (pass) | No secret values in config or logs. AgentCore `environmentVariables` hold 19 names/ids (`CULL_ANTHROPIC_SECRET_ID` is a name). Lambda env (8 functions), SSM (all parameters decrypted; `/keepframe/*` are plain `String` names/ARNs; `/gh-runner/github-token` is a pre-existing non-project SecureString), CloudFormation outputs (ARNs only), ECS task-def env (cull-sync/worker/probe) and the CullEvent definition contain 0 API-key prefixes, 0 LLM-key fragment, 0 source-token fragment, 0 `AKIA`. All 21 log groups (incl. `/aws/bedrock-agentcore/runtimes/…-DEFAULT`, 560 KB, and `/keepframe/*`) have 0 matches for API-key prefixes, the LLM-key suffix, and `x-api-key`/`Bearer `/`ANTHROPIC_API_KEY=`. | `aws logs filter-log-events --filter-pattern '<API-key prefix>'` etc. per group, counts only (LIVE) | The AgentCore runtime log group has **no retention** (never expires). Set 30 d like the others. | **Closed (LIVE)**: AgentCore log group 30 d, see R-17 |
| 18 | Low | Photo/event ids in logs. `/keepframe/sync` has 665 distinct UUIDs (rclone file names), and the API, agent, AgentCore and workflow logs carry tens. No e-mails or names. Logs are private to the account with 30-day retention (except #17). These are pseudonymous ids, and with #1 they resolve to public CDN photos. Acceptable inside the account, but never paste log excerpts into docs or video without aliasing. | `filter-log-events … \| grep -oE <uuid> \| sort -u \| wc -l` (LIVE) | No change beyond #1 and the AgentCore retention. | Pass |
| 19 | Low | IAM wildcards (reviewed live). The API role is scoped (prefix-scoped S3 reads, 2 secrets, 1 runtime ARN). After 047c5aa its ECS RunTask/PassRole/TagResource grants are gone live. CullEvent role: `ecs:RunTask` on `cull-sync:*`, `cull-worker:*` with the `ecs:cluster` condition, PassRole on exactly the 4 sync/worker roles, and `ecs:StopTask`/`DescribeTasks` on `*` (CDK-generated). CI bench role: PassRole on `role/ComputeStack-*` (conditioned to ecs-tasks), wider than needed but limited to project roles. Pre-existing `gh-runner-orchestrator` has `ec2:RunInstances`/`CreateFleet` on `*` (not part of this project). Worker/agent task roles have `s3:GetObject/PutObject` on `bucket/*`. | `aws iam get-role-policy` on each role (LIVE) | Narrow `keepframe-gh-bench` PassRole to the worker/probe role ARNs. Optional prefix scoping on the worker role. | **Partly closed (LIVE)**: bench PassRole scoped, see R-19 |
| 20 | Low | Admin credentials. IAM user `admin` (AdministratorAccess) has an active access key from 2026-08-21, **no MFA**, and is the operator credential for all tooling. | `aws iam list-access-keys`, `list-mfa-devices` (LIVE) | Add MFA. Rotate the key after judging. Consider a scoped operator role. | Open (operator: admin MFA = 0) |
| 21 | Low | Dead code: `agent_resume._ecs` and its settings (`CULL_AGENT_TASKDEF_ARN`, subnets, SGs, `CULL_CLUSTER_ARN`) stay in the API, though the fallback task def is gone live and the env var is unset. Harmless (no IAM grant left), but it's untested surface and confuses the trail. Also, `main` still contains the fallback CDK (`api-stack.ts` RunTask/PassRole grants, `compute-stack.ts` AgentTask): 047c5aa is only on the infra branch, and its diff deletes `ui-interim-stack.ts`, so a naive merge would remove the deployed interim UI stack. | `git diff origin/main <infra-branch> --stat` (CODE); `aws ecs list-task-definition-families` (LIVE) | Merge the infra branch onto current main keeping `ui-interim-stack.ts`, then remove `_ecs` + settings + tests. | **Closed (main ed7fa54 + LIVE)**: `agent_resume` has only agentcore/http/NotConfigured paths. No ECS settings in config. No agentTask in CDK. API Lambda env has no ECS keys. Live task families are cull-probe/sync/worker only. `ui-interim-stack.ts` kept |
| 22 | Info (pass) | S3 and audit. Work bucket: Block Public Access all on, BucketOwnerEnforced (ACLs off), SSE-S3 + bucket key, SSE-C blocked, bucket policy = TLS-only deny (no `Principal:*` allow), `IsPublic=false`, no CORS. All 4 buckets in the account have BPA. CloudTrail `keepframe-trail`: multi-region, logging, log-file validation on, management events plus S3 data events on `arn:aws:s3:::keepframe-work-…/` objects, last delivery current. Trail bucket BPA on, 365-day expiry. DynamoDB: decisions PITR on, traces TTL on `ttl`. | `get-public-access-block`, `get-bucket-policy(-status)`, `get-event-selectors` (LIVE) | Account-level S3 Block Public Access is **not** set (`NoSuchPublicAccessBlockConfiguration`), so turn it on (it costs nothing). | **Closed (LIVE)**: account PAB on, see R-22 |
| 23 | Info (pass) | Supply chain. All GitHub Actions are pinned by full commit SHA with version comments. gitleaks runs in CI (8.30.1, sha256-verified binary, `--redact`) and pre-commit. `config/models.lock` pins sha256 plus immutable URLs (HF commit revs, opencv_contrib commit) and `fetch_models.py` verifies them. `uv.lock` has 688 hashes. rclone is sha256-verified. Branch protection is unavailable on the private repo plan, so `main`-only OIDC trust (`ref:refs/heads/main` / environment `production`) is the control. Note: the `production` environment does not exist, so anyone who can push to `main` can deploy. | `grep uses: .github/workflows/*` (CODE); `gh api …/environments` (LIVE) | When the repo goes public, enable branch protection and an environment with required reviewers. | Pass |
| 24 | Info (pass) | Privacy checks in the repo. `tests/unit/test_no_customer_ids.py` passes (2 passed) without the real-ids file and with a correctly shaped `data/selected_events.json`. Tracked images are 3 diagrams, 5 UI screenshots (synthetic data, one bench chart), and 5 ODC-By AI faces in `tests/fixtures/faces/`. `.gitignore` covers `data/`, `eval/labels/*.csv`, `eval/contact_sheets/`, `docs/screenshots/real-data/`, `*.jsonl`, `.env*`. `scripts/privacy_sweep.sh` is clean. gitleaks over 280+ commits and the working tree is clean on 3094695. Gaps: (a) the id test's strong check is skipped in CI (no real-ids file), and the real-ids sweep file (`selected_events_for_sweep.json`) is a bare list with `event_id`, so feeding it as `data/selected_events.json` crashes the test (`'list' object has no attribute 'get'`). (b) the development-log folder is allowlisted and carries 8-char real-event prefixes in 5 places (`run_agent_<prefix>_…`, `g_<prefix>…`). The UI never shows names, e-mails, whitelabel or user ids (`event_view` allowlist, `CULL_SHOW_EVENT_NAMES=0`), but the manifests in S3 do hold `name` and `whitelabel_id` (private bucket, never served). | `uv run pytest -q tests/unit/test_no_customer_ids.py`; `gitleaks detect` (LIVE run on this box) | Make the test accept both shapes (`list[{event_id}]` and `{events:[{id}]}`). Alias the 5 prefixes, or drop that folder from the allowlist before the repo goes public. | **Closed (main c87a0f6)**: 0 real 8-char prefixes in any tracked file, the development-log folder no longer allowlisted, test passes with and without the real-ids file. Minor residual: the test still reads only the `{events:[{id}]}` shape, not the list-of-`event_id` sweep file |

## Re-verification (2026-10-09, LIVE)

R-1 (S-1). With the judge token: `GET /events?limit=100`, `/events/ev_01/groups?limit=24`, `?group_id=g_ev_01_1`,
`/events/ev_01/photos/ph_…`, `/runs?limit=100`, `/runs/{id}`, `/runs/{id}/trace?limit=500` (280 lines),
`/runs/{id}/agent`, `/bench`, each scanned with a UUID regex. **0 UUIDs** in all 10 responses, 0 `amazonaws.com`
hosts. The trace's 10 `sheets/…` substrings are aliased (`sheets/ev_09/g_ev_09_21.jpg`), not real keys. Ids are
`ev_NN`, `g_ev_NN_n`, `ph_<10 hex>`. Image links are `/img/ev_01/p/ph_….jpg?exp=…&sig=…`:
- signed → 200 image/jpeg (photo and contact sheet)
- tampered sig → 403
- expired or more than 2×TTL in the future → 403
- no sig → 422
- traversal → 404
- headers: `Cache-Control: private, max-age=300` and `Referrer-Policy: no-referrer`

A real source event id as path or `?event_id=` gets 404 for the judge, and an unknown alias (`ev_99`) gets 404. The
team token still gets real UUIDs (`GET /events`, 2 of 2), as designed. The judge still gets 403 on `POST /runs`.
Residual: aliasing is presentation only. Anyone who already has a real id pair can fetch it from the source's public
CDN. That's a source-side control.

R-3 (S-3). `aws s3api get-bucket-lifecycle-configuration` shows `expire-sheets-7d` (sheets/, 7 d),
`expire-preview-7d` (preview/, 7 d) and `expire-bench-5k-30d` (bench/5k/, 30 d), plus the raw/originals/proxy 7 d
rules, all Enabled. `sessions/` (agent session snapshots, JSON, no pixels) still has no expiry (Low).

R-9 (S-9). `aws iam get-role-policy` on AgentRuntimeRole: GetObject on exif/, manifests/, originals/, preview/,
proxy/, raw/, results/, sessions/, sheets/. PutObject on preview/, proxy/, results/, sessions/, sheets/.
ListBucket conditioned on those prefixes. No `bucket/*`.

R-17 (S-17). `aws logs describe-log-groups --log-group-name-prefix /aws/bedrock-agentcore`: retention 30. The only
groups without retention left are 3 CDK custom-resource Lambdas (no project data).

R-19 (S-19). `keepframe-gh-bench` PassRole: exactly the worker task/exec and probe task/exec role ARNs, with
`iam:PassedToService=ecs-tasks.amazonaws.com`.

R-22 (S-22). `aws s3control get-public-access-block --account-id <AWS_ACCOUNT_ID>`: all four flags True.

Still open, re-checked:
- S-4: budgets `keepframe-competition-gross` (120 USD, 50/80/100% actual, 100% forecast) and
  `keepframe-net-spend-actual` (1 USD, 10%) publish to SNS `keepframe-alarms`. That topic and
  `keepframe-alerts` have 0 confirmed and 0 pending subscriptions. `keepframe-opencv26` still has 0
  notifications (delete it or leave it as superseded).
- S-5: the runtime image `keepframe-agent:main-ec7cf96` is still HIGH 7. The worker `keepframe:main-ec7cf96`
  is still CRITICAL 2 / HIGH 2. The current API image (CDK asset) has no scan result (repo scan-on-push off).
- S-8: `get-resource-policy` on the runtime is still empty.
- S-14: `curl -I /ui/` returns no `Content-Security-Policy`.
- S-20: `aws iam list-mfa-devices --user-name admin` → 0.

## Source store / CloudTrail evidence queries

AWS side. Prove nothing in this account wrote outside the work bucket, and that every S3 write landed in it:

```bash
# CloudTrail Lake is not enabled; query the trail bucket with Athena, or quick-check via LookupEvents (management only).
# S3 data events (object-level) for the work bucket only:
aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventSource,AttributeValue=s3.amazonaws.com \
  --start-time 2026-10-05 --query 'Events[].[EventName,Resources[0].ResourceName]' --output text | sort | uniq -c
# Athena over s3://keepframe-trail-<AWS_ACCOUNT_ID>/AWSLogs/<AWS_ACCOUNT_ID>/CloudTrail/ :
#   SELECT eventname, json_extract_scalar(requestparameters,'$.bucketName') b, count(*) FROM cloudtrail
#   WHERE eventsource='s3.amazonaws.com' AND eventname IN ('PutObject','DeleteObject','DeleteObjects','CopyObject')
#   GROUP BY 1,2;   -- expect b = keepframe-work-<AWS_ACCOUNT_ID>-ap-south-1 only
```

Source side. The store's operations analytics (bucket × actionType) show bucket totals only. To attribute writes
to *our* key, the store's owner needs audit logs or a dedicated read-only key (finding #2). With a read-only key, a
write attempt fails with `AccessDenied`. A one-off proof is `aws s3api put-object --endpoint-url
$SOURCE_S3_ENDPOINT_URL` with that key against a non-existent key in a scratch bucket, run by the owner, **never
against the prod buckets**.


## What was not verified

- The actual permission set of the source access key (#2). Only that it cannot ListBuckets.
- Writes to the source database. Checked in code only. The query service enforces `readonly=true` server-side, but no
  database audit log was read.
- Browser behaviour of a CSP on the Next export (#14). Not tested.
- CloudTrail data events were confirmed configured and delivering. No Athena query was run.

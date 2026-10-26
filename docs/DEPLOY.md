# Deploying KeepFrame

Everything runs on Arm: Lambda `arm64`, Fargate `ARM64`, Amazon Bedrock AgentCore Runtime (ARM64 container). No Marketplace listing is used (COOL is removed). The LLM is Claude on Amazon Bedrock by default (model access must be enabled for the account in the region); any Anthropic-compatible endpoint is an optional setting. The only x86 resource is the labelled benchmark baseline (`c7i.4xlarge`). It is off by default and only appears with `-c enableX86Baseline=true`. Region `ap-south-1`. The repo holds no secret values, only the names listed below.

## Prerequisites

The local toolchain (uv 0.11.16+, Python 3.12, Node 22, Docker buildx with an `arm64builder` linux/arm64 builder, `uv sync --frozen --all-extras`, `cd cdk && npm ci`) is listed in the [README](../README.md#deploy-test-demo). Deploying additionally needs:

- AWS admin credentials in the shell (`aws sts get-caller-identity` must work). The Makefile derives `ACCOUNT` from it and `ADOPT` from whether `keepframe-work-<account>-ap-south-1` already exists (`true` only in the original hackathon account). A fresh account deploys with `adoptExisting=false`.
- The account bootstrapped for CDK in `ap-south-1` (`cd cdk && npx cdk bootstrap aws://<account>/ap-south-1`) before the first `make deploy`.
- Secret values set out of band after DataStack is created. The stack creates `keepframe/judge-token` and `keepframe/team-token`, and the optional `keepframe/anthropic` (only for the `anthropic` provider) and `keepframe/source` (only for the optional source sync), holding the literal `SET_OUT_OF_BAND`. Write the ones you use with `aws secretsmanager put-secret-value`.
- Amazon Bedrock model access for the default provider: the account must be allowed to invoke `in.anthropic.claude-sonnet-5` and `in.anthropic.claude-haiku-4-5-20251001-v1:0` (or the ids passed with `-c bedrockModelId=` / `-c bedrockReasonsModelId=`). Pick the optional provider with `-c llmProvider=anthropic`.
- CloudFront available to the account for `make ui-deploy` (new accounts need AWS verification, see UiStack below).

`make synth` without credentials: pass `ACCOUNT=000000000000` (what CI does). Without it, `make synth` stops before calling CDK with the hint `Offline synth: make synth ACCOUNT=000000000000`.

No AWS at all: `make models` downloads and SHA-256-checks every model in `config/models.lock` into `models/` (idempotent, about 47 MB; `make test` runs it first), and `make demo-local` runs the pipeline in LOCAL_MODE: a synthetic 12-photo event → the OpenCV 5 worker → the agent's `--demo` scenario with the fake model and stub tools, every approval answered by `--approve-all` and recorded as `decided_by=scripted` (never human). Output goes to `data/local/demo` (`DEMO_ROOT=<dir>` to change it; the script only wipes a directory it created).


## What is deployed now (account <AWS_ACCOUNT_ID>, 2026-10-09 10:55 UTC)

| Stack | Status | Key resources |
|---|---|---|
| NetworkStack | UPDATE_COMPLETE | VPC `vpc-0478a136078c758de` (10.42.0.0/16), public subnets `subnet-032f6f8bbce68a459`, `subnet-04281d4addd5efe06`, no NAT, S3 + DynamoDB gateway endpoints, egress-only SG `sg-0a9e749ceb932a708`, rejected-traffic flow logs |
| DataStack (`adoptExisting=true`) | UPDATE_COMPLETE | adopts bucket `keepframe-work-<AWS_ACCOUNT_ID>-ap-south-1`, tables `keepframe-decisions` / `keepframe-traces`, and the ECR repos. Creates the source-sync secret, `keepframe/anthropic`, `keepframe/judge-token`, `keepframe/team-token` and SSM `/keepframe/{work-bucket,model-provider,planner-model,reasons-model}` |
| ComputeStack | UPDATE_COMPLETE | cluster `arn:aws:ecs:ap-south-1:<AWS_ACCOUNT_ID>:cluster/keepframe` (FARGATE, FARGATE_SPOT only). Task defs `cull-worker:10` (Fargate ARM64 4 vCPU/8 GiB), `cull-sync:10` (1/2), `cull-probe:10` (0.25/0.5), all on `:fe6fd1c`. No `cull-agent` |
| AgentCoreStack | UPDATE_COMPLETE | runtime `arn:aws:bedrock-agentcore:ap-south-1:<AWS_ACCOUNT_ID>:runtime/keepframe_agent-9GtNw85LzO` (READY, version 5, `keepframe-agent:fe6fd1c`; log retention 30 d), logs `/aws/bedrock-agentcore/runtimes/keepframe_agent-9GtNw85LzO-DEFAULT`, SSM `/keepframe/agent-runtime-arn` |
| WorkflowStack | UPDATE_COMPLETE | `arn:aws:states:ap-south-1:<AWS_ACCOUNT_ID>:stateMachine:CullEvent`, Lambdas `keepframe-{validate-manifest,plan-originals,invoke-agent,agent-status,summarise,mark-run-failed}`, SNS `keepframe-alerts` |
| ApiStack | UPDATE_COMPLETE | `https://fnua4aumrl.execute-api.ap-south-1.amazonaws.com` (Lambda `keepframe-api`, arm64 image built by CDK from `services/api` at main b4f35f0: digest-pinned base, id aliasing, signed `/img/…` routes, stage throttle 100 rps / 200 burst; `CULL_AGENT_RUNTIME_ARN` = the runtime above, no ECS resume env; `/health` needs a bearer token) |
| ObservabilityStack | UPDATE_COMPLETE | dashboard [`KeepFrame`](https://ap-south-1.console.aws.amazon.com/cloudwatch/home?region=ap-south-1#dashboards/dashboard/KeepFrame), alarms `keepframe-run-failures`, `-api-errors`, `-workflow-lambda-errors` → SNS `keepframe-alarms` |
| CiStack | UPDATE_COMPLETE (OIDC trust fix deployed 2026-10-09 with b4f35f0) | OIDC provider `token.actions.githubusercontent.com`, roles `keepframe-gh-deploy` (trust accepts both the classic and the immutable OIDC subject, main/`production` only; fix e6c1a59) and `keepframe-gh-bench` (PassRole limited to the worker/probe task + execution roles) |
| UiStack | **NOT DEPLOYED** | CloudFront `AccessDenied: Your account must be verified before you can add new CloudFront resources` (2026-10-05). Run `make ui-deploy` once the account is verified. |
| UiInterimStack | **LIVE** (2026-10-08) | review UI at **https://fnua4aumrl.execute-api.ap-south-1.amazonaws.com/ui/**: Lambda `keepframe-ui-static` (arm64, Python 3.12 zip with the export inside) on routes `GET\|HEAD /`, `/ui`, `/ui/{proxy+}` of the HTTP API above; SSM `/keepframe/ui-url` |

Lifecycle on the work bucket (DataStack, CDK-owned in adopt mode, verified live 2026-10-09 09:30 UTC): `raw/`, `originals/`, `proxy/`, `sheets/`, `preview/` expire after 7 days; `sessions/` and `bench/5k/` after 30 days; incomplete multipart uploads abort after 1 day. Account-level S3 Block Public Access is on (all four flags).

### Releases on 2026-10-09

Releases 1 and 2 ran `make images-main REF=<sha>` then `make release-deploy` (or `make deploy-core` + `make deploy-api`) from a clean `git archive`. Release 3 is the first one shipped by `deploy.yml` from GitHub Actions (see CI/CD).

**Release 1: main `b6d4fb4`, core stacks, 09:15 UTC.** Task definitions `cull-worker:8`, `cull-sync:8`, `cull-probe:8` on `:main-b6d4fb4`; AgentCore runtime version 3 READY on `keepframe-agent:main-b6d4fb4`. The API was not redeployed. What it shipped:

- per-run results keys `results/<eventId>/<runId>/…`, plus the unversioned latest copy for one release;
- `image_sha` in `summary.json` and RUN# (ECS `ImageID`);
- agent token counts on RUN# (`agent_tokens_in` / `agent_tokens_out`) and agent proposal normalisation;
- the C09 duplicate rule (one judged file per md5 set) and the C08 group-eyes review rule.

| Repo | Tag | Digest |
|---|---|---|
| `keepframe` | `main-b6d4fb4` | `sha256:e7689126592eac4cd14fec57f1e49592d2f87795d6775cd3841727da799b31ec` |
| `keepframe-agent` | `main-b6d4fb4` | `sha256:082d5c1e47ce09feeb2e66a57f7ea60a220d5a6e05e282da72ffa19361ca9d9a` |
| `keepframe-sync` | `main-b6d4fb4` | `sha256:25634cd3710cebd4549f61cff29f90e8d53afce53853c69663dfbc94c294375d` |
| `keepframe-api` (pushed, not deployed) | `main-b6d4fb4` | `sha256:4f588dc325e8134779c0d28358f03067d7ce9d0f1fa37abd987e85f55810afaa` |

Smoke: `run_aws_e03_release_b6d4fb4_20261009T091345Z` (E03, no source sync + skip_originals) SUCCEEDED in 1 min 40 s; the per-run keys exist; RUN# `agent_host=agentcore`, `opencv_hal=carotene`, `agent_tokens_in=21172`, `agent_tokens_out=79`, `image_sha` = the worker digest above. The AgentCore start-up log shows `opencv_hal=carotene` and `anthropic_secret_loaded` (key names only). Its approval was answered as a team mechanism test (`lead_test`). Rule since then: release smoke runs use `skip_agent`, and the agent is checked through `mode=status` and start-up logs, not a new run.

**Release 2: main `b4f35f0`, core stacks + API, 10:16–10:20 UTC** (`make release-deploy REF=b4f35f0`; AgentCore, Compute and Api stacks updated, the rest unchanged; CiStack trust deployed alongside). CVE and digest rebuild (commit 94214af): all four base images pinned by `@sha256`, OS packages upgraded, `curl` dropped from the worker runtime stage, agent and sync bases moved from bookworm to trixie. Task definitions `:9`, AgentCore runtime version 4, API from `b4f35f0`. ECR scan before (live `main-b6d4fb4`, old bases): worker 2 CRITICAL / 3 HIGH, agent 7 HIGH, sync 5 CRITICAL / 14 HIGH, API 8 HIGH. After (`main-b4f35f0`): worker, agent and sync 1 HIGH each (zlib CVE-2026-85091, no Debian fix yet), 0 CRITICAL; API unchanged at 8 HIGH / 6 MEDIUM / 1 LOW (Amazon Linux 2023 has no fixed packages yet). The API Lambda runs the CDK image asset `sha256:3c304686…4724` (LastModified 10:16:38); `/health` returns 200 with the judge token.

| Repo | Tag | Digest |
|---|---|---|
| `keepframe` | `main-b4f35f0` | `sha256:15ef32cdd5a2d6dcdaad9fed1d6e55c523b1ec70689d9c2369db135baa3823e6` |
| `keepframe-agent` | `main-b4f35f0` | `sha256:ca526462cde692301b2875033187198ac8e00e50044e91a7e4bebf347feb6a7c` |
| `keepframe-sync` | `main-b4f35f0` | `sha256:644338c89f3594bc40ee9e699c1cdf9e0fe78bbe048c25384c3bb22c8d38f308` |
| `keepframe-api` | `main-b4f35f0` | `sha256:9535c4da98753e3664dd01ee6bf1f7e3088eabc6121661ce9970ce3c1db57f86` |

Smoke (no source sync + `skip_originals` + `skip_agent`): `run_aws_e03_release_b4f35f0_20261009T101935Z` SUCCEEDED 10:19:37 → 10:20:42; `results/<E03>/<runId>/{summary,decisions,groups,hashes}` exist; RUN# `status=succeeded`, `opencv_hal=carotene`, `image_sha` = the worker digest above, `wall_s=20.0`, 664 photos; nothing added to the approval queue. Agent check without a new run: `mode=status` on the release-1 smoke run returned 200 `paused`, and a fresh microVM at 10:18:11 logged `opencv_hal=carotene` (Custom HAL YES carotene 0.0.1) and `anthropic_secret_loaded`. The trixie build also passed the carotene / no-KleidiCV assertion.

**Release 3: main `fe6fd1c` via `deploy.yml`, 10:55 UTC** (run 37919588493). Same application code as `b4f35f0`: `git diff b4f35f0 fe6fd1c` touches only `.github/workflows/deploy.yml` and `docs/`. CI rebuilt the images on its arm64 runner and tagged them `:fe6fd1c` (and `:dev`), so the digests differ from release 2: worker `sha256:eb39fde3…f2b`, agent `sha256:c170586e…00e`, sync `sha256:5eb776b3…7f5`. Task definitions `:10`, AgentCore runtime version 5. `deploy-api` ran but left the API Lambda unchanged (LastModified 10:16:38, release 2), because its CDK image asset is built from the same `services/api` source.

Earlier deployed runs on `main-ec7cf96` (2026-10-08) keep their records: the full E03 event `run_aws_e03_agentcore_full_20261008T061112Z` (664 photos, 3 min 29 s) and the 2,442-photo E01 event `run_aws_e01_2k_20261008T061801Z` (8 min 29 s, `docs/evidence/E01_2442_agentcore.md`). They wrote only the unversioned `results/<eventId>/` files.

Release a git ref in one command (builds from `git archive <ref>`, so local changes never leak in; pushes `main-<sha>` and `dev`, no provenance or SBOM manifests, which Lambda rejects; prints digests), then roll out:

```bash
make images-main REF=main
make release-deploy REF=main    # core stacks + API from a clean `git archive`, TAG=main-<sha>
# or by hand: make deploy-core deploy-api TAG=main-<sha> SRC=<tree containing services/api>
```

Step Functions and the API's resume path start tasks by family, so ECS runs the latest ACTIVE revision. Deregister superseded revisions (`aws ecs deregister-task-definition`) so nothing can fall back to an old image. IAM grants cover every revision (`<family>:*`).

```bash
make requirements                  # re-export after uv.lock changes
make build-worker push TAG=dev     # buildx --builder arm64builder --platform linux/arm64 --load, then push :TAG and :dev
make probe                         # Fargate ARM64 one-off that prints cv2 version/file/arch
```

Task definitions use `:dev` by default (`-c imageTag=<tag>` pins a specific build).

## Agent on AgentCore Runtime

`AgentCoreStack` (docs/AGENT.md "AgentCore Runtime") creates:

- the runtime `keepframe_agent`: container `<account>.dkr.ecr.ap-south-1.amazonaws.com/keepframe-agent:<imageTag>` (same `-c imageTag` as the ECS images), protocol HTTP (`/invocations` + `/ping` on 8080), network PUBLIC, `idleRuntimeSessionTimeout` 900 s, `maxLifetime` 28800 s. Its `environmentVariables` hold names and ids only (`CULL_*`, `WORK_BUCKET`, table names, `CULL_ANTHROPIC_SECRET_ID=keepframe/anthropic`, `CULL_AGENT_HOST=agentcore`, `CULL_RUN_KIND=deployed`). AgentCore has no secret injection, so the container reads `keepframe/anthropic` itself at startup.
- the execution role: trust `bedrock-agentcore.amazonaws.com` conditioned on `aws:SourceAccount` + `aws:SourceArn`; ECR pull on `keepframe-agent`; logs on `/aws/bedrock-agentcore/runtimes/*`; X-Ray; `cloudwatch:PutMetricData` (namespaces `KeepFrame`, `bedrock-agentcore`); `GetWorkloadAccessToken*` on the default workload-identity directory; the worker grants (work bucket, both tables); `secretsmanager:GetSecretValue` on `keepframe/anthropic-??????` only.
- SSM `/keepframe/agent-runtime-arn` = the runtime ARN, read by the workflow's `invoke_agent` / `agent_status` Lambdas on every call (anything that is not an AgentCore runtime ARN fails the run). The API Lambda gets the ARN as a cross-stack value (`CULL_AGENT_RUNTIME_ARN`).

Runtime logs: `/aws/bedrock-agentcore/runtimes/<runtimeId>-DEFAULT` (stack output `AgentRuntimeLogGroup`). A new image needs a CloudFormation update of the runtime: `make release-deploy REF=<sha>` passes `imageTag=main-<sha>` to AgentCoreStack like the ECS stacks.

**No Fargate fallback.** The `cull-agent` task definition, its task role, the `RunAgentFargate` branch, the `none` value of the parameter and the API's ECS resume environment were removed on 2026-10-08 after a full real event completed on AgentCore (`run_aws_e03_agentcore_full_20261008T061112Z`, below). AgentCore Runtime is the only agent host. If the runtime is unavailable, CullEvent fails at RunAgent (MarkRunFailed) and the API's approval resume returns 502 with the approval still pending. Run the worker only with `skip_agent: true`.

**Deploy order when migrating from a stack set that predates AgentCoreStack:** the parameter used to belong to DataStack. Deploy `DataStack` first (it deletes the old parameter), then `AgentCoreStack` (it creates it again with the ARN), then the rest. `make deploy-core` does this in order (CDK dependency order: AgentCoreStack after DataStack, ApiStack after AgentCoreStack).

**COOL was removed on 2026-10-08** (Marketplace software fee): no `cull-worker-cool`, no c8g capacity provider/ASG, no `Dockerfile.cool`, no `/keepframe/cool-image-uri`. The benchmark axis is now stock-carotene wheel vs KleidiCV wheel vs source build on EC2 hosts (`bench/ec2`, docs/COOL_BENCHMARK.md).

## Running

```bash
make demo EVENT=<eventId>     # StartExecution on CullEvent (or scripts/start_run.py when present)
```

The photographer first uploads the event's photos to `s3://<work bucket>/raw/<eventId>/` (`docs/DATA.md` §7). The `CullEvent` input is `{event_id, run_id, flavor?: stock, sync_source?, skip_originals?, skip_agent?}` (`flavor` is optional; only `stock` is valid). The API's `POST /runs` creates the RUN# item and starts the execution. The steps are:

1. **Ingest** (Lambda arm64) builds `manifests/<eventId>.jsonl` from `raw/<eventId>/` when no manifest was uploaded.
2. **ValidateManifest** (Lambda arm64) checks the manifest exists in S3 and marks the run `running`.
3. **SyncSource** (optional, only with `sync_source: true`) runs `cull-sync` (Fargate ARM64, `.sync`) to import the event from an S3-compatible store (`SOURCE_S3_ENDPOINT_URL`, `SOURCE_BUCKET_*`, keys in `keepframe/source`).
4. **CullWorkerStock** runs `cull-worker` (Fargate ARM64, `.sync`).
5. **PlanOriginals** (Lambda, only with `sync_source`) writes `originals-requests/<eventId>/<runId>.txt`: members of groups of size ≥ 2 plus `needs_approval` photos that have an `original_key`. **SyncOriginals** then runs `cull-sync` with `SYNC_KINDS=originals` and `PHOTO_IDS_S3` set to that key. It is skipped with `skip_originals: true`, or when 0 candidates have originals.
6. **RunAgent** (Lambda `keepframe-invoke-agent`) reads `/keepframe/agent-runtime-arn` and calls `InvokeAgentRuntime` with `{"mode":"start","event_id","run_id","wait":false}` on session `f"keepframe-{run_id}".ljust(33,"0")` (or RUN# `agent_session_id`), and stamps RUN# `agent_host=agentcore` + `agent_session_id`. Then **WaitForAgent** (30 s) → **AgentStatus** (Lambda `keepframe-agent-status`, `{"mode":"status","run_id"}`) → **AgentDone?**: `running` loops, `paused`/`done` go to Summarise, `error` goes to MarkRunFailed. The 2 h budget is enforced by `agent_status` (`CULL_AGENT_BUDGET_S=7200`). **AgentStarted?** goes straight to Summarise if the start answer is already `paused`/`done`.
7. **Summarise** (Lambda) copies `results/<eventId>/summary.json` onto RUN#.
8. **Notify** publishes to SNS. Any failure goes through the Catch to **MarkRunFailed** → **NotifyFailed** → **Fail**.

Human approvals: when the agent hits an interrupt, it saves `sessions/<runId>.json` and sets RUN# `awaiting_approval`; the status poll reports `paused` and the execution finishes. `POST /approvals/{id}` on the API resumes it with `InvokeAgentRuntime` `{"mode":"resume","run_id","decisions"}` on the same session id (`CULL_AGENT_RUNTIME_ARN`).

Fargate on-demand vCPU quota is 8 today. That means one 4-vCPU worker plus small tasks at a time, so do not fan out runs until the raise lands.

## UI

Two hosting paths exist. **The interim path is live**; the CloudFront path is ready in code and waits for AWS to verify the account.

| | Interim (live) | CloudFront (target) |
|---|---|---|
| Stack | `UiInterimStack` (`cdk/lib/ui-interim-stack.ts`) | `UiStack` (`cdk/lib/ui-stack.ts`) |
| URL | `https://<api-id>.execute-api.ap-south-1.amazonaws.com/ui/` | `https://<id>.cloudfront.net/` |
| Files | the export, built with `basePath /ui`, inside an arm64 Lambda zip (`services/ui_static/handler.py` + `site/`) | private S3 bucket, CloudFront OAC |
| Build output | `ui/out-interim` (`make ui-build-interim`) | `ui/out` (`make ui-build`) |
| Blocker | none | `AccessDenied: Your account must be verified before you can add new CloudFront resources` (user-only, via Support) |

### Interim: the HTTP API serves the UI under /ui

```bash
make ui-build-interim     # NEXT_PUBLIC_BASE_PATH=/ui, NEXT_PUBLIC_API_BASE=$(SSM /keepframe/api-url) -> ui/out-interim
make ui-deploy-interim    # UiInterimStack: Lambda + 6 routes on the existing API, writes /keepframe/ui-url
```

The UI is served from the same origin as the API, so the browser sends no cross-origin requests and the deployment has no CORS step. Rebuild and redeploy after every `ui/` change. The handler mirrors the CloudFront behaviour:

- `/` redirects (302) and `/ui` redirects (308) to `/ui/`. Directory paths map to `index.html`. `/ui/bench` redirects (308) to `/ui/bench/`, as the trailing-slash export expects. Query strings are kept (`/ui/events/?id=…`).
- Unknown paths return the export's `404.html` with status 404. `..` segments and paths outside the bundled site return 404.
- Content types are explicit (`text/html`, `text/javascript`, `text/css`, `font/woff2`, `text/plain` for RSC payloads, …). `_next/static/*` is served `public, max-age=31536000, immutable` because the names are content-hashed. HTML and RSC payloads are `no-cache` with an ETag (304 on revalidation). Gzip is applied when the client accepts it, with a distinct ETag per representation. Every response sends `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()` and HSTS.
- Every HTML response (including 404 and 304) carries a strict Content-Security-Policy (security review S-14): `default-src 'self'; script-src 'self' 'sha256-…'…; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'`. The Next.js App Router export inlines 6–7 `self.__next_f.push(...)` hydration scripts per page. The handler hashes each page's inline scripts when it loads the file and lists exactly those hashes, so `'unsafe-inline'`/`'unsafe-eval'` are not allowed for scripts. The export has no `on*=` handlers, `style=` attributes or external hosts. Images come from the API's same-origin `/img` proxy, so `'self'` covers them.
- Only GET and HEAD are routed; the handler answers 405 to anything else.

**Why this option.** It needs no CloudFront and no bucket, so nothing has to be made public. A Lambda Function URL would add a second origin (and so a CORS step) plus a public function URL. A Lambda that streams from S3 would add an IAM read grant, an upload step and an S3 round trip per file, all for a 1.4 MB static site. Baking the export into a 256 MB arm64 zip function keeps it to one origin, no data store, and a role that can only write its own logs. The API id comes from SSM `/keepframe/api-url` (not a CloudFormation export), so `ApiStack` deploys independently. New routes go live through the stage's auto-deploy.

**Auth.** The `/ui` routes are unauthenticated on purpose (cdk-nag APIG4 acknowledged with that reason). They return the static HTML/JS/CSS/fonts a CDN would serve, and contain no data or secrets. Every data route stays on `keepframe-api`, which checks the bearer token entered in the app (judge = GET only, 403 on writes). The UI routes share the stage throttle (20 rps / burst 40) with the API.

**Limits.** Lambda concurrency in this account is 10 (raise requested) and is shared with the API and workflow Lambdas. A first page load fetches about 15 files, but responses take milliseconds and assets are cached immutably, so a few judges at once is fine. A crowd is not.

Verified 2026-10-08 (curl + headless Chromium via agent-browser): `/ui/`, `/ui/events/?id=…`, `/ui/groups/?…`, `/ui/approvals/` → 200 `text/html`. JS, CSS and woff2 assets → 200 with correct types, immutable cache and gzip. `/ui/bench` → 308. `/ui/runs/` and `/ui/nope/` → 404 page. `If-None-Match` → 304. `/health`, `/events`, `/runs` without a token → 401. Signing in with the judge token from `keepframe/judge-token` showed "Judge view, read-only" with real events. The event page loaded all 58 contact-sheet images, the group page showed its frames and survived a reload on the deep link, and the benchmark page rendered. A `POST /runs` from the page with the judge token returned 403. There were no console errors.

### CloudFront (once the account is verified)

The UI needs the API URL at build time, and CORS needs the UI URL. Both stacks own `/keepframe/ui-url`, so remove the interim stack first (`make ui-deploy` refuses while it exists):

```bash
make ui-destroy-interim         # drops the /ui routes + Lambda
make deploy-api                 # writes /keepframe/api-url
make ui-build                   # NEXT_PUBLIC_API_BASE=$(api-url) npm run build -> ui/out (no basePath)
make ui-deploy                  # UiStack: private S3 + CloudFront OAC, index.html rewrite function, 404 page
make deploy-api                 # re-run: CORS locked to /keepframe/ui-url
```

`make deploy-api` reads `/keepframe/ui-url` and passes its origin (`scheme://host`, path stripped) as `corsOrigins`. With the interim path that origin is the API itself, which is harmless because same-origin requests never trigger CORS. Then update `docs/JUDGE_ACCESS.md` step 1 and the README demo URL.

## CI/CD

> **This repository ships only `ci.yml`** (lint, type-check, tests, synth, image build; no AWS credentials). The `deploy.yml` and `bench.yml` workflows described below were used in the original development repository and are not included here; their OIDC roles trust only that repository. Deploys from this repo run by hand with `make deploy`.

- `.github/workflows/ci.yml`: every job runs on `ubuntu-24.04-arm`. **Only the arm hosted runners have proven reliable for this account.** `ubuntu-latest` (x64) jobs sat queued for 15 minutes and were then cancelled by GitHub ("The job was not acquired by Runner of type hosted", run 48a62d0), while arm jobs started within seconds. Pushes that touch only `docs/**` or `*.md` skip the image build. That check runs inside the `image` job, and a failed check means build, so a scheduling or filter problem can never skip the arm64 gate. Main runs are never cancelled, so every main commit gets a verdict. The jobs are: `uv sync --frozen`, ruff, mypy, pytest, a check that the requirements export matches the lock, `cdk synth` with cdk-nag (no AWS creds), an Arm-only grep, arm64 builds of the worker/sync/API images with the in-image `cv2 5.x + contrib + aarch64` assert, and gitleaks (pinned, sha256-verified binary). All actions are pinned by commit SHA.
- `deploy.yml` (push to main, or `workflow_dispatch`): OIDC role `keepframe-gh-deploy` (assumes only the CDK bootstrap roles plus project ECR push) → `astral-sh/setup-uv` → arm64 build and push of worker, sync and agent as `:<sha7>` + `:dev` → `make deploy-core deploy-api` → interim UI build and deploy. It runs only when the repo variable `AWS_DEPLOY_ROLE_ARN` is set and needs a `production` environment. **First green run: 37919588493 on main `fe6fd1c`, 2026-10-09 10:55 UTC** (release 3 above). Three fixes got it there:
  - **OIDC trust ("Not authorized", run 37906655604).** The repo uses `use_immutable_subject: true`, so the token subject is `repo:<owner>@<owner-id>/keepframe@<repo-id>:environment:production`. Fix e6c1a59 (CiStack, deployed 2026-10-09) accepts both subject forms, still limited to `ref:refs/heads/main` and `environment:production`, audience `sts.amazonaws.com`; `keepframe-gh-bench` gets the two `main` forms.
  - **`uv` missing on the runner (run 37917643021).** A pinned `setup-uv` step was added (f191430).
  - **Adopt flag derived before credentials (run 37918543106).** The Makefile computes `ADOPT` from `aws s3api head-bucket` at parse time, before the job had AWS credentials, so it became `adoptExisting=false` and CloudFormation refused to create the bucket, tables and ECR repos that already exist ("already exists"; nothing changed). The workflow now passes `ACCOUNT=$(aws sts get-caller-identity …)` and `ADOPT=true` explicitly (8cc3b0f).
- **Release procedure (decision 2026-10-09 10:55 UTC).** `AWS_DEPLOY_ROLE_ARN` stays **unset by default**, because with it set every push to main, including coord-only commits, would deploy. To release: set the variable to `arn:aws:iam::<AWS_ACCOUNT_ID>:role/keepframe-gh-deploy`, run `gh workflow run deploy.yml --ref main`, wait for it to finish, then unset the variable. The manual `make images-main REF=<sha>` + `make release-deploy REF=<sha>` path still works and shipped releases 1 and 2.
- **Fresh-clone coverage.** The CI runner deploys from a fresh checkout, so the README's build and deploy path is exercised end to end, but **into the same account in adopt mode**. A deploy into a fresh account (`adoptExisting=false`) has not been attempted.
- `bench.yml` (manual): OIDC role `keepframe-gh-bench`, runs a worker task definition and uploads evidence. It needs `AWS_BENCH_ROLE_ARN`.
- `.pre-commit-config.yaml` runs ruff and gitleaks. `.gitleaks.toml` allowlists only generated, gitignored output (`cdk.out/` asset hashes).

## cdk-nag

`cdk synth` runs `AwsSolutionsChecks` (cdk-nag 3.0.2) and fails on any finding that is not acknowledged. cdk-nag 3 matches acknowledgments by exact finding id, and IAM5 findings embed resource ARNs, so `cdk/lib/nag.ts` adds `appliesTo` patterns on top of the native `Validations.acknowledge()`. Every acknowledgment has a written reason next to the resource, and each is copied into the CloudFormation `Metadata.cdk_nag` of the template. The main ones:

- **IAM5**: object-level S3 grants are prefix-scoped on one bucket. `cloudwatch:PutMetricData` (namespace-conditioned), `ecr:GetAuthorizationToken` and X-Ray accept no resource. The AgentCore role's log group, workload identity and secret ARNs end in a wildcard because AgentCore/Secrets Manager append the runtime id or a random suffix after create. `InvokeAgentRuntime` grants are the exact runtime ARN plus `<arn>/*` (its endpoints).
- **APIG4**: no API Gateway authorizer. Every route is authenticated in the Lambda (judge read-only / team bearer tokens, fail closed). Stage throttling is set to 20 rps / burst 40.
- **S1**: the work bucket uses CloudTrail S3 data events instead of server access logs.
- **DDB3**: traces are reproducible from S3 and expire after 90 days.
- **SMG4**: the optional source and LLM keys rotate at the provider. API tokens rotate through the `previous` field.
- **CFR1/2/4**: no geo restriction, no WAF, default certificate (hackathon scope).
- **L1**: Python 3.12 is pinned by the plan.
- **EC23**: the shared SG has no ingress rules, only 0.0.0.0/0 egress.

## Cost notes

No NAT gateway (saves ~$32/month). Fargate tasks run per job, and AgentCore Runtime bills per session (consumption, covered by credits); an idle session ends after 15 min. The fixed monthly cost is small: secrets (4 × $0.40), the dashboard ($3), and CloudWatch Logs with 30-day retention. The design estimate of ≈ $50–100 for the competition window still holds. AWS Budgets live in `ObservabilityStack` and publish to the `keepframe-alarms` SNS topic:

- `keepframe-competition-gross`: $120/month on **gross usage (credits excluded)**, alerts at 50/80/100% actual and 100% forecast. Credits pay the bill, so a credit-inclusive budget reads $0 and never fires; this one tracks the real burn against the credit pool.
- `keepframe-net-spend-actual`: $1/month on net spend (credits included), alert at 10% actual. It fires on any charge that credits do not cover (Marketplace, tax, support). No forecast alert: the Budgets forecast ignores credits.

Both are account-wide, so they include the account's non-project resources (see `bench/results/cost/`). **E-mail: no subscriber yet. An address is still needed**; deploy with `-c budgetEmail=<address>` (for example `make deploy-core CDK_CTX+=...` or `cd cdk && npx cdk deploy ObservabilityStack -c budgetEmail=<address> --exclusively`) and confirm the SNS e-mail. Without it, budget and alarm notifications reach the topic only. The hand-made `keepframe-opencv26` budget (credit-inclusive, no notifications) predates CDK and can be deleted by hand once the address is subscribed.

Measured spend: `scripts/cost_report.py` writes `bench/results/cost/<date>.md` from Cost Explorer (Usage vs Credit by service, keepframe vs other, $ per culled event, AgentCore consumption).

## Destroy

```bash
make destroy CONFIRM=yes   # cd cdk && npx cdk destroy --all --force
```

This destroys every stack. `make destroy` refuses when `ACCOUNT` is empty (no credentials) or the `000000000000` placeholder, and refuses without `CONFIRM=yes`; only then does it call `cdk destroy --force`. It needs the same credentials as deploy. The work bucket and both tables use `RemovalPolicy.RETAIN` because they hold customer-derived data. In adopt mode they were never owned by CDK. Empty and delete them by hand when the project ends: `aws s3 rm s3://keepframe-work-…/ --recursive && aws s3 rb …`, then `aws dynamodb delete-table …`. The CloudTrail trail and its bucket belong to the account's base setup.

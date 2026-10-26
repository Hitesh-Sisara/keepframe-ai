# Architecture

KeepFrame runs entirely on AWS in `ap-south-1`. Every compute unit is Arm64/Graviton. Photographers upload an event shoot to the work bucket's `raw/<eventId>/` prefix; KeepFrame only ever reads those photos. The planner is Claude on Amazon Bedrock by default, so compute, data and inference all stay in the AWS account. Any Anthropic-compatible endpoint can be configured instead (optional); that setting is then the one call outside AWS.

![AWS architecture](architecture.png)

Diagram source: [`architecture.mmd`](architecture.mmd). Agent loop: [`agent_workflow.mmd`](agent_workflow.mmd) / [`agent_workflow.png`](agent_workflow.png).

Re-render both diagrams (needs Node 22 and a local Chrome or Chromium):

```bash
echo '{"args":["--no-sandbox"]}' > /tmp/pptr.json
PUPPETEER_SKIP_DOWNLOAD=1 PUPPETEER_EXECUTABLE_PATH="$(command -v google-chrome || command -v chromium)" \
  npx -y @mermaid-js/mermaid-cli@11 -p /tmp/pptr.json -i docs/architecture.mmd -o docs/architecture.png -w 2400 -b white
PUPPETEER_SKIP_DOWNLOAD=1 PUPPETEER_EXECUTABLE_PATH="$(command -v google-chrome || command -v chromium)" \
  npx -y @mermaid-js/mermaid-cli@11 -p /tmp/pptr.json -i docs/agent_workflow.mmd -o docs/agent_workflow.png -w 1800 -b white
# Without Chrome: curl -s https://kroki.io/mermaid/png --data-binary @docs/architecture.mmd -o docs/architecture.png
```

## Components

| # | Component | AWS service | Arm architecture | Notes |
|---|---|---|---|---|
| 1 | Ingest | Photographer upload to S3 `raw/<eventId>/` + Lambda `Ingest` | arm64 (Graviton2) | When `manifests/<eventId>.jsonl` is missing, `Ingest` builds it from the photos under `raw/<eventId>/` (optional `exif/` sidecars). A manifest uploaded with the photos is used as is |
| 2 | Optional source sync (`SyncSource`) | ECS Fargate task `cull-sync`, 1 vCPU / 2 GiB, rclone | ARM64 (Graviton2) | Only with `sync_source: true`: imports an event from any S3-compatible store (`SOURCE_S3_ENDPOINT_URL`, empty = AWS S3). GET/HEAD/LIST only on the source. Keys come from Secrets Manager `keepframe/source` |
| 3 | Work bucket | S3 `keepframe-work-<account>-<region>` | managed | Private, Block Public Access, SSE-S3, TLS only. `raw/`, `originals/`, `proxy/` expire after 7 days |
| 4 | Orchestration | Step Functions Standard `CullEvent` | managed (Lambda steps arm64) | Ingest, ValidateManifest, SyncSource (optional), CullWorkerStock, PlanOriginals/SyncOriginals (optional, with `sync_source`), RunAgent (Lambda `invoke_agent` → Wait 30 s → Lambda `agent_status` loop, 2 h budget), Summarise, Notify. Catch leads to MarkRunFailed. A paused agent ends the execution; the API resumes it |
| 5 | OpenCV 5 worker | ECS Fargate `cull-worker`, 4 vCPU / 8 GiB | ARM64 (Graviton2) | Stock `opencv-contrib-python-headless==5.0.0.93`. COOL was removed 2026-10-08 (Marketplace-billed) |
| 6 | Why not Lambda for the worker | n/a | n/a | 900 s limit and 10 GiB memory cap. A single ECS task is simple to benchmark |
| 7 | Agent | Amazon Bedrock AgentCore Runtime `keepframe_agent` (HTTP `/invocations` + `/ping`, PUBLIC network, idle 900 s / max 8 h), Strands agent + OpenCV 5 tools, image `keepframe-agent` | ARM64 container | Session per run (`keepframe-<runId>` padded to 33 chars). With the optional `anthropic` provider it reads `keepframe/anthropic` itself at start. Logs `/aws/bedrock-agentcore/runtimes/<id>-DEFAULT`. No Fargate fallback (removed 2026-10-08 after a full real event ran on AgentCore) |
| 7b | LLM | Claude on Amazon Bedrock (default, `KEEPFRAME_LLM_PROVIDER=bedrock`, Strands `BedrockModel`) | n/a (managed API) | Planner `in.anthropic.claude-sonnet-5`, reasons `in.anthropic.claude-haiku-4-5-20251001-v1:0` (India inference profiles; `KEEPFRAME_BEDROCK_MODEL_ID` / `KEEPFRAME_BEDROCK_REASONS_MODEL_ID`). Optional: any Anthropic-compatible endpoint (`anthropic` provider, `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY` from Secrets Manager, `CULL_PLANNER_MODEL` / `CULL_REASONS_MODEL` = `claude-sonnet-5` / `claude-haiku-4.5`) |
| 8 | Decision store | DynamoDB on-demand `keepframe-decisions` | managed | PITR on. GSIs: by verdict, by group, approval queue |
| 9 | Trace store | DynamoDB `keepframe-traces` + S3 `results/<eventId>/<runId>/trace.jsonl` (this run only; an unversioned `results/<eventId>/trace.jsonl` with every run's lines is kept for one release) | managed | TTL 90 days on DynamoDB items |
| 10 | API | API Gateway HTTP API + Lambda (Python 3.12, FastAPI + Mangum) | arm64 (Graviton2) | Lambda authorizer with judge token (read-only). No unauthenticated route |
| 11 | Review UI | Interim (live): Next.js static export in arm64 Lambda `keepframe-ui-static` on the HTTP API, `/ui/` (`UiInterimStack`). Target: S3 + CloudFront (OAC, `UiStack`) once AWS verifies the account | arm64 / managed | Thumbnails via presigned GETs, 15 min or less |
| 12 | Observability | CloudWatch Logs (JSON), EMF metrics, dashboard `KeepFrame`, alarms | managed | `photos_processed`, `stage_latency_ms{stage}`, `tool_calls{tool}`, `escalations`, `run_duration_s`, `opencv_flavor` |
| 13 | Secrets | Secrets Manager + SSM Parameter Store | managed | The repo holds env var names only |
| 15 | Network | 1 VPC, 2 public subnets, no NAT; S3 + DynamoDB gateway endpoints | n/a | Fargate tasks get a public IP for egress; the AgentCore runtime uses PUBLIC network mode |
| 16 | Registry | ECR `keepframe`, `keepframe-agent`, `keepframe-sync`, `keepframe-api` | linux/arm64 images only | Scan on push |
| 17 | IaC | AWS CDK v2 (TypeScript) + cdk-nag | n/a | Stacks: Network, Data, Compute, AgentCore, Workflow, Api, Ui, Observability, Ci. AgentCoreStack owns the runtime, its role and `/keepframe/agent-runtime-arn` |
| 18 | CI | GitHub Actions `ubuntu-24.04-arm` | native arm64 builds | OIDC deploy role, no long-lived keys |

x86 appears in this project only as an optional, clearly labelled benchmark baseline (`c7i.4xlarge`). It is never production compute.

## Data flow

1. **Upload.** The photographer uploads the event's photos to `raw/<eventId>/` (optionally EXIF sidecars to `exif/<eventId>/` and a manifest to `manifests/`).
2. **Ingest.** `Ingest` builds `manifests/<eventId>.jsonl` and `.meta.json` from `raw/<eventId>/` when no manifest was uploaded. Optional: with `sync_source: true` the Fargate `SyncSource` task imports the photos and EXIF sidecars from an S3-compatible store instead, and a second pass copies originals to `originals/`, but only for group candidates.
3. **Proxies and features.** The worker decodes each image once, builds 1024-px proxies (`proxy/`), and computes hashes, sharpness, exposure and face metadata.
4. **Agent.** The Strands agent (AgentCore Runtime, ARM64 container) calls Claude (on Amazon Bedrock by default) and plans tool calls over those results (see [agent_workflow.png](agent_workflow.png)), escalates uncertain items with a Strands interrupt, and writes `keep` / `reject` / `review` decisions: a `PHOTO#` overlay row in DynamoDB for each verdict it sets or a human answers, appended to the run's `results/<eventId>/<runId>/decisions.jsonl` after the worker's rows (plus the unversioned latest copy for one release). Its trace and `agent_summary.json` go to the same per-run prefix.
5. **Review.** The UI lists groups, reasons, the trace chain and pending approvals. `POST /approvals/{id}` resumes the interrupted agent.

## Arm rationale

- AgentCore Runtime requires an ARM64 container; the agent image is `linux/arm64` like every other image.
- The Arm OpenCV axis is the wheel/build, not a Marketplace image: stock wheel with the carotene HAL vs the KleidiCV-enabled wheel vs a source build on Graviton4 (docs/COOL_BENCHMARK.md; the benchmark owns the numbers). COOL is out by decision (Marketplace-billed).
- Lambda arm64 handles every short task (API, validate, summarise) at the arm64 price.

## IAM summary (least privilege)

| Role | Allows |
|---|---|
| `CullSyncTaskRole` (optional source sync) | `s3:PutObject` on `raw/*`, `originals/*`, `exif/*`; `secretsmanager:GetSecretValue` on `keepframe/source` |
| `CullWorkerTaskRole` | `s3:GetObject/PutObject/ListBucket` on the work bucket; `dynamodb:PutItem/BatchWriteItem/Query` on both tables; `cloudwatch:PutMetricData`; logs on its own group. |
| AgentCore execution role (`AgentCoreStack`) | Trust `bedrock-agentcore.amazonaws.com` with `aws:SourceAccount` + `aws:SourceArn`; ECR pull on `keepframe-agent`; logs on `/aws/bedrock-agentcore/runtimes/*`; X-Ray; `cloudwatch:PutMetricData` (namespace-conditioned); `GetWorkloadAccessToken*` on the default workload-identity directory; worker permissions; `bedrock:InvokeModel` / `bedrock:InvokeModelWithResponseStream` on the two configured inference profiles and their foundation models only; `secretsmanager:GetSecretValue` on `keepframe/anthropic` only (optional provider) |
| `CullApiLambdaRole` | `dynamodb:Query/GetItem/UpdateItem`; `s3:GetObject` on `proxy/*`, `sheets/*`, `results/*`; `states:StartExecution` on `CullEvent`; `bedrock-agentcore:InvokeAgentRuntime` on the CDK runtime only (resume an interrupt); `secretsmanager:GetSecretValue` on `keepframe/judge-token` |
| `CullStateMachineRole` | `ecs:RunTask`, `iam:PassRole` (task roles only), `lambda:InvokeFunction`, the EventBridge permissions `.sync` needs. The `invoke_agent` / `agent_status` Lambdas hold `InvokeAgentRuntime` on the CDK runtime and `ssm:GetParameter` on `/keepframe/agent-runtime-arn` |
| Execution roles | ECR pull + logs only |
| CI (GitHub OIDC) | `cdk deploy` through the CDK bootstrap roles only |

Everything uses the `keepframe-*` prefix in a dedicated AWS account.

## S3 layout

Prefixes: `manifests/`, `raw/` (7 d), `originals/` (7 d), `exif/`, `proxy/` (7 d), `sheets/`, `preview/`, `results/`, `sessions/`, `bench/`, `eval/labels/`.

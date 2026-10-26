# Agent (Agentic Vision): the Strands loop over OpenCV 5 tools

The agent is a planner (Claude Sonnet 5) over the OpenCV 5 tools. The batch worker
(`keepframe.worker.run_event`) has already grouped bursts and scored every photo deterministically; the
agent takes its `results/<eventId>/<runId>/{groups.json,decisions.jsonl}` (falling back to the unversioned
`results/<eventId>/…` copy for older runs) as starting context and works only the
items the worker could not settle (groups flagged for a human, photos left at review). For each item it calls
the vision tools itself, reads the numbers, and decides the next call. The P1–P9 policies (docs/REPORT.md §4) are in the system
prompt and also enforced in code by hooks, so a model slip cannot become a wrong verdict. Nothing is ever
deleted, and the uploaded photos are never modified.

## Architecture of the loop

```
next_work_item ─► score_sharpness / score_exposure / detect_faces / score_eyes_open / score_face_quality
      ▲                       (batch per item; each result recorded as evidence in RunContext)
      │                                  │
      │                         pick_best_in_cluster ─► make_contact_sheet ─► request_human_approval
      │                                  │                                     (Strands interrupt: run pauses,
      │                                  ▼                                      session saved, item on GSI3)
      └──────────── write_decision ◄─────┴──── human answer (POST /approvals → resume) ◄─┘
                (hook gate: P2/P3/P4/P6 block, P5 interrupt)
```

| module | role |
|---|---|
| `keepframe/agent/strands_agent.py` | `build_agent(event_id, run_id, model)`, `run_event_agent()`, `resume_with_decisions(run_id, decisions)`; work set from the worker results; session save/restore |
| `keepframe/agent/toolset.py` | Strands `@tool` wrappers (event_id/run_id injected, results recorded as evidence); backend `CULL_TOOLS=real` (`_tool_real.py`, OpenCV 5 on S3 images) or `stub` (`_tool_stubs.py`, deterministic scenario) |
| `keepframe/agent/hooks.py` | BeforeToolCall/AfterToolCall/Invocation hooks: caps, retries, policy gates, approval interrupts, trace, EMF |
| `keepframe/agent/policies.py` | pure P1–P9 policy checks over the recorded evidence; numbers and rules shared with the worker (`keepframe/worker/policies.py`, `config/thresholds.yaml`) |
| `keepframe/agent/system_prompt.py` | policies, autonomy boundaries and output contract for the model |
| `keepframe/agent/models.py` | provider switch `KEEPFRAME_LLM_PROVIDER=bedrock\|anthropic\|fake` (Bedrock default), Secrets Manager bootstrap for the optional `anthropic` provider |
| `keepframe/agent/fake_model.py` | deterministic scripted planner (a Strands `Model`), zero network |
| `keepframe/agent/budget.py`, `reasons.py` | `get_run_budget`; ≤140-char reason text (template, optional Haiku rewrite `CULL_REASONS_LLM=1`) |
| `keepframe/tools/{decision,approval}.py` | `write_decision` (keep\|reject\|review only, ≥1 code, ≤140 chars, DynamoDB + decisions.jsonl; no delete path) and `request_human_approval` (Strands interrupt) |
| `keepframe/store/{dynamodb,traces,tables}.py` | the only writer of `keepframe-decisions` / `keepframe-traces`; trace lines; table creator |
| `keepframe/metrics/emf.py` | EMF: photos_processed, stage_latency_ms{stage}, tool_calls{tool}, escalations, run_duration_s, opencv_flavor |
| `services/agent_runtime/` | `app.py` (BedrockAgentCoreApp: `/invocations`, `/ping`, port 8080; runs on AgentCore Runtime and as the Fargate fallback), `Dockerfile.agent` (arm64), `requirements-agent.txt` |

Analysis tools take `photo_ids: list` (one call per tool per item) instead of per-photo
signature. With the per-group cap of 25 calls, a 4-frame group would otherwise need about 26 calls just for P1.
Batching keeps a whole group to about 10 calls and keeps LLM turns affordable.

## How policies are enforced in code

| policy | enforcement (hooks.py → policies.py) |
|---|---|
| P1 group first | `pick_best_in_cluster` is cancelled until every member has sharpness + faces (+ eyes when owed); `score_eyes_open`/`score_face_quality` need `detect_faces` first. Singletons start on the saliency ROI. |
| P2 borderline → faces/original | `write_decision` is cancelled while `worker.policies.next_sharpness_step` still owes a step (faces ROI when faces exist, then `variant="original"` when still borderline). A blur reject also needs final sharpness `blurry`. |
| P3 eyes per face | `EYES_CLOSED` reject is cancelled unless `worker.policies.eyes_defect` says the MAIN subject (largest face ≥ 40 px) is closed with p ≥ 0.8; ≥3 faces and non-main faces closed (≥2, or ≥1/3 of them) → review with `REVIEW_POLICY_GROUP_EYES`. Dark + small faces → eye state is skipped (the tool returns `skipped`). Null eye probabilities mean unknown, never closed. |
| P4 never reject a whole group | a write leaving every member rejected is cancelled |
| P5 low confidence → human | `decided_by=agent` reject with confidence < 0.8, or a group whose pick_best margin < 0.05 / tie / all-failed / top-scored frame defective → the BeforeToolCall hook calls `event.interrupt(...)`. The run pauses, the item goes on the approval queue, and after the answer the hook rewrites the write to the human verdicts with `decided_by=human`, confidence 1.0. The model can also ask first with `request_human_approval` (the usual path). |
| P6 artistic intent | `BLUR_MOTION` reject with ExposureTime ≥ 1/30 s, or clip_lo ≥ 30% at reception hours → cancelled, must be review + `REVIEW_ARTISTIC_INTENT` |
| P7 budget | `score_composition` / `enhance_keeper` are cancelled under 20% remaining (`get_run_budget`) |
| P8 never delete | `write_decision` validates `verdict ∈ keep\|reject\|review`; the store has no delete method |
| P9 explain | ≥1 reason code (fixed vocabulary, `DUPLICATE_OF:<id>` checked), reason_text ≤140 chars, templated from the tool numbers when omitted |
| attribution | answered photos get `human_source` = how the answer arrived: `ui` (API/DynamoDB, ECS `--resume`), `file` (`--resume <file>`), `http` (`/invocations` resume), `scripted` (auto-approver, `--approve-all`, `--demo`), `lead_test` (labeller `lead-mechanism-test`). `decided_by="human"` only for ui/file/http; scripted answers are `decided_by="scripted"`, so no metric counts them as human agreement. The proposal's confidence is kept (not forced to 1.0). The model cannot pick the attribution: the toolset stamps it from the resume source, and the agent never writes `worker`. |
| caps | ≤25 calls per group, ≤1.5×N calls per event, ≤60 min working wall clock (human wait excluded), ≤3 retries per failing tool+args (transient errors are retried by the hook; validation errors are returned to the model). On a cap the call is cancelled, the item is escalated (approval queue + `escalation` trace line), and the next item starts. Six consecutive cancelled or failed calls stop the run (a planner repeating a failing `write_decision` is escalated, not looped). Each invocation runs in chunks of `CULL_TURNS_PER_CHUNK` (80) model turns, so a long run never hits Python's recursion limit inside Strands. |

A cancelled call returns the policy text to the model as the tool result, so the model reads why and
complies (for example, it re-scores on the face ROI). This is visible in the trace as a `tool_result` with
`status: cancelled` followed by the corrected call.

## Trace schema

One JSON line per step, written to DynamoDB `keepframe-traces` (pk `RUN#<runId>`, sk = seq zero-padded to 6,
`ttl` = now + 90 d), to `results/<eventId>/<runId>/trace.jsonl` (this run only; the unversioned
`results/<eventId>/trace.jsonl` copy keeps every run's lines for one release), and to stdout (`{"log":"trace",...}`, unless
`CULL_TRACE_SINKS` excludes it). Seq continues across pause/resume.

| type | fields |
|---|---|
| `run_header` | event_id, provider, model, host (agentcore\|fargate\|local), run_kind, reasons_model, thresholds_sha, tools, n_photos, n_items, opencv_version, cv2_file |
| `model_reasoning` | text: the assistant text before its tool call(s), once per model message; hook notes are `[hook:<kind>] ...` |
| `tool_call` | tool, args, model |
| `tool_result` | tool, result (what the model saw), latency_ms, opencv (`cv2.__version__`), cv2_file, status (`cancelled`/`error` only when not ok) |
| `interrupt` | text, pending [{interrupt_id, name, items[{group_id\|photo_id, proposed, reason, confidence, contact_sheet_key}]}] |
| `interrupt_resumed` | human_source (ui\|file\|http\|scripted\|lead_test), decisions: the answers per interrupt |
| `escalation` | text, item_id (cap hit) |
| `error` | tool, text (a transient failure being retried) |
| `run_end` | summary {items, decided, verdict_counts, escalated, unfinished, tool_calls, model_calls, escalations, wall_s, stopped} |

Every line carries `run_id, seq, ts, type`. DynamoDB items additionally carry `pk, sk, ttl, event_id`.

## Run locally with the fake model (no network, no AWS)

```bash
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -e '.[agent,dev]' \
  'boto3==1.43.108' 'botocore==1.43.108'   # see requirements-agent.txt for why boto3 differs
# stub tools + scripted planner, all outputs under ./data/local (LOCAL_MODE: WORK_BUCKET unset)
KEEPFRAME_LLM_PROVIDER=fake python -m keepframe.agent.run --demo --run-id run_demo1        # pauses on g_412
echo '{"decisions":[{"group_id":"g_412","human_verdict":{"keep":["p_8831"],"reject":["p_8830","p_8832","p_8833"]},"note":"8831 has the better expression"}]}' > d.json
KEEPFRAME_LLM_PROVIDER=fake python -m keepframe.agent.run --demo --run-id run_demo1 --resume d.json
# trace: data/local/results/ev_demo/trace.jsonl ; items: data/local/dynamodb/keepframe-decisions.jsonl
# (the API's LOCAL_MODE reads the same files, so the UI shows the run)
```

Tests: `CULL_METRICS=off .venv/bin/python -m pytest -q tests/unit/test_policies.py tests/unit/test_agent_loop.py tests/unit/test_agent_runtime.py`.

Real tools, still with the fake planner: `CULL_TOOLS=real WORK_BUCKET=keepframe-work-<AWS_ACCOUNT_ID>-ap-south-1
CULL_MODELS_DIR=$PWD/models` (models via `python scripts/fetch_models.py`), after the worker has written
`results/<eventId>/<runId>/groups.json` (pass the same `--run-id` to both).

## Model provider

`KEEPFRAME_LLM_PROVIDER` picks the provider (`CULL_MODEL_PROVIDER` is still accepted as a legacy alias). The
default is Claude on Amazon Bedrock; any Anthropic-compatible endpoint is an optional alternative.

| `KEEPFRAME_LLM_PROVIDER` | what it uses | config |
|---|---|---|
| `bedrock` (default) | Strands `BedrockModel` (bedrock-runtime ConverseStream), IAM credentials | `KEEPFRAME_BEDROCK_MODEL_ID` (default `in.anthropic.claude-sonnet-5`, the India inference profile for ap-south-1 / ap-south-2) and `KEEPFRAME_BEDROCK_REASONS_MODEL_ID` (default `in.anthropic.claude-haiku-4-5-20251001-v1:0`); region `AWS_REGION` |
| `anthropic` (optional) | Strands `AnthropicModel`, official `anthropic` SDK, any Anthropic-compatible endpoint | `ANTHROPIC_BASE_URL` (default `https://api.anthropic.com`) and `ANTHROPIC_API_KEY`; models `CULL_PLANNER_MODEL` / `CULL_REASONS_MODEL` |
| `fake` | `FakeModel` | none |

On AWS the AgentCore execution role may call `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on
the two configured inference profiles and their foundation models only. CDK context `llmProvider` (default
`bedrock`), `bedrockModelId` and `bedrockReasonsModelId` set the runtime environment.

Secrets (optional `anthropic` provider only): the key lives in Secrets Manager `keepframe/anthropic` (JSON keys `ANTHROPIC_API_KEY`,
`ANTHROPIC_BASE_URL`). ECS injects both as container secrets. AgentCore Runtime has no secret injection, so the
runtime app and the CLI call `models.bootstrap_anthropic_secret()` once at startup: if `ANTHROPIC_API_KEY` is
unset and `CULL_ANTHROPIC_SECRET_ID` is set, they read the secret (region `AWS_REGION`) into the process
environment. Values already in the environment win. Only the secret id and key names are logged. With
`anthropic` and neither available, startup fails with `RuntimeError: KEEPFRAME_LLM_PROVIDER=anthropic needs
ANTHROPIC_API_KEY or CULL_ANTHROPIC_SECRET_ID ...`.

Models for `anthropic`: `CULL_PLANNER_MODEL` (default `claude-sonnet-5`) and `CULL_REASONS_MODEL` (default `claude-haiku-4.5`).
The planner runs with `thinking: {"type":"disabled"}` and never forces `tool_choice` (some compatible endpoints ignore it).
With `anthropic` 1.x there is no `temperature` kwarg on `messages.stream()`, so none is sent.

```bash
set -a; . ~/.config/keepframe/.env; set +a        # outside the repo; provides AWS_* (and ANTHROPIC_* for the optional provider)
CULL_TOOLS=real WORK_BUCKET=keepframe-work-<AWS_ACCOUNT_ID>-ap-south-1 \
  python -m keepframe.agent.run --event-id <eventId> --run-id run_<id>
python -m keepframe.agent.run --run-id run_<id> --resume            # answers from DynamoDB (after the UI)
KEEPFRAME_LLM_PROVIDER=anthropic CULL_ANTHROPIC_SECRET_ID=keepframe/anthropic python -m keepframe.agent.run ...   # optional provider, key from Secrets Manager
```

## Deploy

### Image (one image for AgentCore Runtime and the Fargate fallback)

```bash
docker buildx build --builder arm64builder --platform linux/arm64 \
  -f services/agent_runtime/Dockerfile.agent \
  -t <AWS_ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/keepframe-agent:$(git rev-parse --short HEAD) --push .
```

Default CMD `python /app/app.py`: the HTTP service on port 8080 (`POST /invocations`, `GET /ping`). The same
image runs the batch CLI with a command override (`python -m keepframe.agent.run ...`).

### AgentCore Runtime (where the agent runs)

Infra's CDK `AgentCoreStack` creates the runtime (ARM64 container from `keepframe-agent`, HTTP protocol,
PUBLIC network mode, idle timeout 900 s, max lifetime 8 h), its execution role and the SSM parameter
`/keepframe/agent-runtime-arn`. Runtime env carries names and ids only (`CULL_*`, `WORK_BUCKET`,
`CULL_ANTHROPIC_SECRET_ID`, `CULL_AGENT_HOST=agentcore`), never secret values. The Step Functions `RunAgent`
step invokes it through a Lambda (`mode=start`, `wait:false`), then polls `mode=status` every 30 s. The API's
approval resume sends `mode=resume` to the same runtime.

Invoke manually:

```bash
ARN=$(aws ssm get-parameter --name /keepframe/agent-runtime-arn --query Parameter.Value --output text)
SID=keepframe-run_<id>-0000000000000000      # >= 33 chars; the API/workflow use agent_resume.session_id_for()
aws bedrock-agentcore invoke-agent-runtime --region ap-south-1 \
  --agent-runtime-arn "$ARN" --runtime-session-id "$SID" \
  --content-type application/json --accept application/json \
  --cli-binary-format raw-in-base64-out \
  --payload '{"mode":"start","event_id":"<eventId>","run_id":"run_<id>","wait":false}' out.json
cat out.json        # {"status": "running", "run_id": ..., "event_id": ...}
```

Without `--cli-binary-format raw-in-base64-out` the CLI base64-encodes the payload, the container receives an
empty body, and the runtime answers HTTP 400 "Invalid JSON".

Payload modes (`/invocations`, same on the local HTTP service):

| mode | payload | reply |
|---|---|---|
| `start` | `{"mode":"start","event_id","run_id","wait"?:false,"max_items"?}` | `running` (or `paused`/`done` with `wait:true`) |
| `status` | `{"mode":"status","run_id"}` | `running` / `paused` + `pending_approvals` / `done` + `summary` / `error` (in-process state, else the RUN# item) |
| `resume` | `{"mode":"resume","run_id","decisions":[...],"wait"?:false}` | `running` (or the next `paused`/`done`) / `error` |

Decision schema (one entry per answered approval; the API resolves approve/override before sending):

```json
{"group_id": "g_412", "human_verdict": {"keep": ["p_8832"], "reject": ["p_8830", "p_8833"], "review": ["p_8831"]}, "note": "optional"}
{"photo_id": "p_8840", "human_verdict": "keep" , "note": "optional"}
```

The entry is identified by `group_id`, `photo_id`, `id` or `interrupt_id`. `human_verdict` is required: a
string `keep|reject|review` for a photo, or a keep/reject/review map of member ids for a group (at least one
keep, no photo under two verdicts, members of that group only). A malformed entry is rejected before any run
thread starts, even with `wait:false`, and the run stays paused so the person can resend:

```json
{"status": "error", "error": "invalid decisions: decision 0 (group_id=g_412): human_verdict is required (got keys ['action', 'group_id']); ..."}
```

Hosting stamp: RUN# gets `agent_host` = `agentcore` | `fargate` | `local` on every start and resume, and the
`run_header` trace line gets `host`. bedrock-agentcore 1.24.0 sets no environment variable of its own, so
detection is `CULL_AGENT_HOST` (infra sets `agentcore`; the runtime app also sets it when a request carries the
`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header), then `CULL_INSTANCE_TYPE=agentcore-*`, then ECS metadata
(`fargate`), else `local`. `run_kind` is unchanged:
it is set once (by the worker on deployed runs) and is `deployed` on AgentCore too.

Logs: CloudWatch log group `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT` (the stdout trace lines, the
`anthropic_secret_loaded` line with the secret id only, and tracebacks).

### Fargate ARM64 (fallback for one more release)

The `cull-agent` task definition stays as the documented fallback (`/keepframe/agent-runtime-arn = none`
selects it). Batch task: `python -m keepframe.agent.run --event-id <id> --run-id <id>` /
`--run-id <id> --resume`. As an HTTP service **it has no authentication of its own**: run it in private
subnets with ingress only from the API Lambda security group, never behind a public load balancer. On
AgentCore the same service sits behind IAM SigV4 (`InvokeAgentRuntime`).

## Evidence: real run (real event, real OpenCV 5 tools, Claude Sonnet 5 via the Anthropic-compatible provider setting)

Run `E09-agent-141910Z` on 2026-10-05: a real 661-photo Corporate event already synced to
the work bucket, `CULL_MODEL_PROVIDER=anthropic` (`claude-sonnet-5` through the Anthropic-compatible provider setting), `CULL_TOOLS=real`
(OpenCV 5.0.0 on the real proxies, face models), x86_64 dev box. The worker flagged 12 items (11 groups,
1 photo). The agent finished all 12: 24 photos decided, 10 human approvals, 102 tool calls, 72 model calls,
534 s working wall clock (human wait excluded), no caps hit, 280 trace lines with gap-free seq across 10
pause/resume cycles. Each resume was a new process that restored `sessions/<run_id>.json`. **The approvals in this run were scripted**:
a script approved every proposal exactly as the agent proposed it. No person reviewed the frames. The run's items
are labelled `decided_by="scripted"`, `human_source="scripted"` in DynamoDB and decisions.jsonl (relabelled
after the run, when `human_source` was introduced). The trace lines below predate the field, so
`interrupt_resumed` has no `human_source` here. Ids are shortened (`E` = event, `p_xxxxxx` = first 6 hex of the photo id); results are trimmed to the
fields that drive the decision. The full lines are in `results/<eventId>/trace.jsonl` and DynamoDB.

Moment 2 (main-subject eyes closed → compare within the group) is seq 11 → 15. Moment 3 (small margin / tie →
human with a contact sheet) is seq 16 → 18 → 20 → interrupt 21, resumed at 22. Hook enforcement is seq 101 → 104:
P1 cancels an eye-state call that came before face detection, and the model corrects itself.

```jsonl
{"seq":1,"ts":"2026-10-05T14:19:33.555Z","type":"tool_call","tool":"next_work_item","args":{},"model":"claude-sonnet-5","run_id":"run_agent_E"}
{"seq":2,"ts":"2026-10-05T14:19:33.758Z","type":"tool_result","tool":"next_work_item","result":{"item_id":"g_E_5","kind":"group","why":"worker flagged the group for a human check (margin 1.000)","items_left":11},"latency_ms":0.89,"opencv":"5.0.0","cv2_file":".../site-packages/cv2/__init__.py","run_id":"run_agent_E"}
{"seq":3,"ts":"2026-10-05T14:19:39.314Z","type":"model_reasoning","text":"Group with 4 known faces each, worker split keep/reject with low confidence (0.5) on the keep. Let me score sharpness, exposure, faces, eyes, and face quality on the group.","run_id":"run_agent_E"}
{"seq":4,"ts":"2026-10-05T14:19:39.520Z","type":"tool_call","tool":"score_sharpness","args":{"photo_ids":["p_6abea9","p_76adde"],"roi":"full"},"model":"claude-sonnet-5","run_id":"run_agent_E"}
{"seq":5,"ts":"2026-10-05T14:19:42.390Z","type":"tool_result","tool":"score_sharpness","result":{"per_photo":[{"photo_id":"p_6abea9","lap_var":10056.179,"label":"sharp","confidence":0.975,"roi_used":"full"},{"photo_id":"p_76adde","lap_var":10056.179,"label":"sharp","confidence":0.975,"roi_used":"full"}]},"latency_ms":2668.05,"opencv":"5.0.0","cv2_file":".../site-packages/cv2/__init__.py","run_id":"run_agent_E"}
{"seq":6,"ts":"2026-10-05T14:19:42.597Z","type":"tool_call","tool":"score_exposure","args":{"photo_ids":["p_6abea9","p_76adde"]},"model":"claude-sonnet-5","run_id":"run_agent_E"}
{"seq":7,"ts":"2026-10-05T14:19:43.012Z","type":"tool_result","tool":"score_exposure","result":{"per_photo":[{"photo_id":"p_6abea9","mean_L":76.002,"label":"ok","confidence":0.95},{"photo_id":"p_76adde","mean_L":76.002,"label":"ok","confidence":0.95}]},"latency_ms":210.0,"opencv":"5.0.0","cv2_file":".../site-packages/cv2/__init__.py","run_id":"run_agent_E"}
{"seq":8,"ts":"2026-10-05T14:19:43.217Z","type":"tool_call","tool":"detect_faces","args":{"photo_ids":["p_6abea9","p_76adde"]},"model":"claude-sonnet-5","run_id":"run_agent_E"}
{"seq":9,"ts":"2026-10-05T14:19:43.766Z","type":"tool_result","tool":"detect_faces","result":{"per_photo":[{"photo_id":"p_6abea9","n_faces":4,"main_face_px":234},{"photo_id":"p_76adde","n_faces":4,"main_face_px":234}]},"latency_ms":341.34,"opencv":"5.0.0","cv2_file":".../site-packages/cv2/__init__.py","run_id":"run_agent_E"}
{"seq":10,"ts":"2026-10-05T14:19:43.975Z","type":"tool_call","tool":"score_eyes_open","args":{"photo_ids":["p_6abea9","p_76adde"]},"model":"claude-sonnet-5","run_id":"run_agent_E"}
{"seq":11,"ts":"2026-10-05T14:19:44.414Z","type":"tool_result","tool":"score_eyes_open","result":{"per_photo":[{"photo_id":"p_6abea9","any_main_subject_closed":true,"main_subject_face_id":"f0","confidence":0.898,"per_face":[{"face_id":"f0","left_open_p":0.0611,"right_open_p":0.0372,"state":"closed","is_main":true},{"face_id":"f1","left_open_p":1.0,"right_open_p":0.9699,"state":"open","is_main":false},{"face_id":"f2","left_open_p":0.9988,"right_open_p":0.1717,"state":"partial","is_main":false}]},{"photo_id":"p_76adde","any_main_subject_closed":true,"main_subject_face_id":"f0","confidence":0.898,"per_face":[{"face_id":"f0","left_open_p":0.0611,"right_open_p":0.0372,"state":"closed","is_main":true},{"face_id":"f1","left_open_p":1.0,"right_open_p":0.9699,"state":"open","is_main":false},{"face_id":"f2","left_open_p":0.9988,"right_open_p":0.1717,"state":"partial","is_main":false}]}]},"latency_ms":236.87,"opencv":"5.0.0","cv2_file":".../site-packages/cv2/__init__.py","run_id":"run_agent_E"}
{"seq":12,"ts":"2026-10-05T14:19:44.624Z","type":"tool_call","tool":"score_face_quality","args":{"photo_ids":["p_6abea9","p_76adde"]},"model":"claude-sonnet-5","run_id":"run_agent_E"}
{"seq":13,"ts":"2026-10-05T14:19:45.459Z","type":"tool_result","tool":"score_face_quality","result":{"per_photo":[{"photo_id":"p_6abea9","main_subject_face_id":"f0","per_face":[{"face_id":"f0","fiqa":0.7344},{"face_id":"f1","fiqa":0.8026},{"face_id":"f2","fiqa":0.7141}]},{"photo_id":"p_76adde","main_subject_face_id":"f0","per_face":[{"face_id":"f0","fiqa":0.7344},{"face_id":"f1","fiqa":0.8026},{"face_id":"f2","fiqa":0.7141}]}]},"latency_ms":632.12,"opencv":"5.0.0","cv2_file":".../site-packages/cv2/__init__.py","run_id":"run_agent_E"}
{"seq":14,"ts":"2026-10-05T14:19:49.857Z","type":"model_reasoning","text":"Both photos are identical in every metric (likely true duplicates/burst pair) with main subject (234px, p=0.04-0.06) eyes closed in both. Per P4, every frame fails on main subject eyes closed, so I keep the best-scoring one and send to human review.","run_id":"run_agent_E"}
{"seq":15,"ts":"2026-10-05T14:19:50.059Z","type":"tool_call","tool":"pick_best_in_cluster","args":{"group_id":"g_E_5"},"model":"claude-sonnet-5","run_id":"run_agent_E"}
{"seq":16,"ts":"2026-10-05T14:19:50.262Z","type":"tool_result","tool":"pick_best_in_cluster","result":{"best_photo_id":"p_6abea9","tie":true,"margin":0.0,"ranking":[{"photo_id":"p_6abea9","score":0.8037},{"photo_id":"p_76adde","score":0.8037}]},"latency_ms":1.35,"opencv":"5.0.0","cv2_file":".../site-packages/cv2/__init__.py","run_id":"run_agent_E"}
{"seq":17,"ts":"2026-10-05T14:19:54.154Z","type":"model_reasoning","text":"Tie=true, margin=0 \u2014 both P4 (every frame fails, main subject eyes closed) and P5 (tie) trigger human review. I'll make a contact sheet and escalate.","run_id":"run_agent_E"}
{"seq":18,"ts":"2026-10-05T14:19:54.356Z","type":"tool_call","tool":"make_contact_sheet","args":{"group_id":"g_E_5"},"model":"claude-sonnet-5","run_id":"run_agent_E"}
{"seq":19,"ts":"2026-10-05T14:19:55.937Z","type":"tool_result","tool":"make_contact_sheet","result":{"key":"sheets/E/g_E_5.jpg","n_tiles":2,"width":768,"height":320},"latency_ms":1379.55,"opencv":"5.0.0","cv2_file":".../site-packages/cv2/__init__.py","run_id":"run_agent_E"}
{"seq":20,"ts":"2026-10-05T14:20:01.599Z","type":"tool_call","tool":"request_human_approval","args":{"items":[{"group_id":"g_E_5","proposed":{"keep":["p_6abea9"],"reject":[],"review":["p_76adde"]},"reason":"Both frames identical (tie, margin 0.0); main subject face f0 (234px) eyes closed in both (p=0.04/0.06). Keeping best-of-group per P4; other frame is duplicate with closed eyes.","confidence":0.5,"contact_sheet_key":"sheets/E/g_E_5.jpg"}]},"model":"claude-sonnet-5","run_id":"run_agent_E"}
{"seq":21,"ts":"2026-10-05T14:20:03.824Z","type":"interrupt","text":"run paused for 1 approval item(s)","pending":[{"interrupt_id":"v1:tool_call:toolu_bdrk_01GPBNiEn8pVjcUDudVRcStb:p_4841a8","name":"keepframe-approval:74db7aa81d96","items":[{"group_id":"g_E_5","photo_id":null,"proposed":{"keep":["p_6abea9"],"reject":[],"review":["p_76adde"]},"reason":"Both frames identical (tie, margin 0.0); main subject face f0 (234px) eyes closed in both (p=0.04/0.06). Keeping best-of-group per P4; other frame is duplicate with closed eyes.","confidence":0.5,"contact_sheet_key":"sheets/E/g_E_5.jpg"}],"kind":"keepframe_approval"}],"run_id":"run_agent_E"}
{"seq":22,"ts":"2026-10-05T14:23:41.813Z","type":"interrupt_resumed","decisions":[[{"id":"g_E_5","group_id":"g_E_5","photo_id":null,"interrupt_id":"v1:tool_call:toolu_bdrk_01GPBNiEn8pVjcUDudVRcStb:p_4841a8","human_verdict":{"keep":["p_6abea9"],"reject":[],"review":["p_76adde"]},"note":"approved as proposed (reviewer)"}]],"run_id":"run_agent_E"}
{"seq":23,"ts":"2026-10-05T14:23:42.251Z","type":"tool_result","tool":"request_human_approval","result":{"decisions":[{"id":"g_E_5","group_id":"g_E_5","photo_id":null,"interrupt_id":"v1:tool_call:toolu_bdrk_01GPBNiEn8pVjcUDudVRcStb:p_4841a8","human_verdict":{"keep":["p_6abea9"],"reject":[],"review":["p_76adde"]},"note":"approved as proposed (reviewer)"}]},"latency_ms":2.25,"opencv":"5.0.0","cv2_file":".../site-packages/cv2/__init__.py","run_id":"run_agent_E"}
...
{"seq":101,"ts":"2026-10-05T14:26:21.042Z","type":"tool_call","tool":"score_eyes_open","args":{"photo_ids":["p_72829e","p_fc912a"]},"model":"claude-sonnet-5","run_id":"run_agent_E"}
{"seq":102,"ts":"2026-10-05T14:26:21.247Z","type":"model_reasoning","text":"[hook:policy_P1] Call detect_faces(photo_ids=['p_72829e', 'p_fc912a']) before score_eyes_open.","hook":"policy_P1","run_id":"run_agent_E"}
{"seq":103,"ts":"2026-10-05T14:26:21.458Z","type":"tool_result","tool":"score_eyes_open","result":{"text":"Policy hook: Call detect_faces(photo_ids=['p_72829e', 'p_fc912a']) before score_eyes_open."},"latency_ms":210.45,"status":"cancelled","opencv":"5.0.0","cv2_file":".../site-packages/cv2/__init__.py","run_id":"run_agent_E"}
{"seq":104,"ts":"2026-10-05T14:26:21.666Z","type":"tool_call","tool":"detect_faces","args":{"photo_ids":["p_72829e","p_fc912a"]},"model":"claude-sonnet-5","run_id":"run_agent_E"}
{"seq":105,"ts":"2026-10-05T14:26:24.907Z","type":"tool_result","tool":"detect_faces","result":{"per_photo":[{"photo_id":"p_72829e","n_faces":3,"main_face_px":290},{"photo_id":"p_fc912a","n_faces":3,"main_face_px":290}]},"latency_ms":3032.03,"opencv":"5.0.0","cv2_file":".../site-packages/cv2/__init__.py","run_id":"run_agent_E"}
...
{"seq":279,"ts":"2026-10-05T14:33:02.714Z","type":"run_end","text":"run finished: 24 decided, 0 escalated","summary":{"items":12,"decided":24,"verdict_counts":{"keep":11,"reject":0,"review":13},"escalated":[],"unfinished":[],"tool_calls":102,"model_calls":72,"escalations":10,"wall_s":533.72,"stopped":null,"final_text":"All 12 queued items done: 24 photos decided across 11 groups + 1 singleton, with 5 group pairs escalated to human review for low-margin/eye-state calls (10 escalated decisions total). No items remaining.\n"},"run_id":"run_agent_E"}
```

**Moment 1 (borderline → face ROI re-score) did not happen in this real run.** All 12 synced events appear
pre-culled. A 120-face-photo sample per event found 0 to 2 full-frame `borderline` frames per event, and those were
sharp on the saliency ROI that P1 prescribes for singletons, so the policy correctly did not escalate. Its
lap_var values (390 to 2615) were labelled borderline by a disagreeing FFT/BRISQUE vote, not by the lap_var band. The
moment is shown with the deterministic stub tools and the scripted FakeModel. The same hooks and policies run,
and `tests/unit/test_agent_loop.py` asserts the shape. Run: `python -m keepframe.agent.run --demo --run-id run_demo1`
(see "Run locally"). Synthetic ids:

```jsonl
{"run_id":"run_demo1","seq":5,"ts":"2026-10-05T14:10:40.990Z","type":"tool_call","tool":"score_sharpness","args":{"photo_ids":["p_8830","p_8831","p_8832","p_8833"],"roi":"full"},"model":"fake-planner-v1"}
{"run_id":"run_demo1","seq":6,"ts":"2026-10-05T14:10:40.997Z","type":"tool_result","tool":"score_sharpness","result":{"per_photo":[{"photo_id":"p_8830","lap_var":126.0,"label":"sharp","confidence":0.6,"roi_used":"full"},{"photo_id":"p_8831","lap_var":74.2,"label":"borderline","confidence":0.55,"roi_used":"full"},{"photo_id":"p_8832","lap_var":131.0,"label":"sharp","confidence":0.62,"roi_used":"full"},{"photo_id":"p_8833","lap_var":240.0,"label":"sharp","confidence":0.95,"roi_used":"full"}]},"latency_ms":1.62,"opencv":"5.0.0"}
{"run_id":"run_demo1","seq":10,"ts":"2026-10-05T14:10:41.018Z","type":"model_reasoning","text":"Detect faces: p_8831 not sharp on the full frame and the manifest lists faces; find them so the soft background is not mistaken for blur."}
{"run_id":"run_demo1","seq":11,"ts":"2026-10-05T14:10:41.020Z","type":"tool_call","tool":"detect_faces","args":{"photo_ids":["p_8830","p_8831","p_8832","p_8833"]},"model":"fake-planner-v1"}
{"run_id":"run_demo1","seq":12,"ts":"2026-10-05T14:10:41.026Z","type":"tool_result","tool":"detect_faces","result":{"per_photo":[{"photo_id":"p_8830","n_faces":2,"main_face_px":180},{"photo_id":"p_8831","n_faces":2,"main_face_px":212},{"photo_id":"p_8832","n_faces":2,"main_face_px":205},{"photo_id":"p_8833","n_faces":2,"main_face_px":220}]},"latency_ms":2.24,"opencv":"5.0.0"}
{"run_id":"run_demo1","seq":13,"ts":"2026-10-05T14:10:41.043Z","type":"model_reasoning","text":"Sharpness is borderline on the full frame for p_8831 (lap_var 74.2) and detect_faces found 2 face(s) (main face 212 px). Policy 2: re-score on the face ROI before labelling."}
{"run_id":"run_demo1","seq":14,"ts":"2026-10-05T14:10:41.045Z","type":"tool_call","tool":"score_sharpness","args":{"photo_ids":["p_8830","p_8831","p_8832","p_8833"],"roi":"faces"},"model":"fake-planner-v1"}
{"run_id":"run_demo1","seq":15,"ts":"2026-10-05T14:10:41.049Z","type":"tool_result","tool":"score_sharpness","result":{"per_photo":[{"photo_id":"p_8830","lap_var":58.0,"label":"blurry","confidence":0.86,"roi_used":"faces"},{"photo_id":"p_8831","lap_var":188.9,"label":"sharp","confidence":0.92,"roi_used":"faces"},{"photo_id":"p_8832","lap_var":160.0,"label":"sharp","confidence":0.8,"roi_used":"faces"},{"photo_id":"p_8833","lap_var":205.0,"label":"sharp","confidence":0.93,"roi_used":"faces"}]},"latency_ms":1.96,"opencv":"5.0.0"}
{"run_id":"run_demo1","seq":16,"ts":"2026-10-05T14:10:41.060Z","type":"model_reasoning","text":"Faces found in the group; Policy 3: eye state per face, main subject first."}
{"run_id":"run_demo1","seq":17,"ts":"2026-10-05T14:10:41.062Z","type":"tool_call","tool":"score_eyes_open","args":{"photo_ids":["p_8830","p_8831","p_8832","p_8833"]},"model":"fake-planner-v1"}
{"run_id":"run_demo1","seq":18,"ts":"2026-10-05T14:10:41.067Z","type":"tool_result","tool":"score_eyes_open","result":{"per_photo":[{"photo_id":"p_8830","any_main_subject_closed":false,"confidence":0.9},{"photo_id":"p_8831","any_main_subject_closed":false,"confidence":0.9},{"photo_id":"p_8832","any_main_subject_closed":false,"confidence":0.9},{"photo_id":"p_8833","any_main_subject_closed":true,"confidence":0.9}]},"latency_ms":2.26,"opencv":"5.0.0"}
{"run_id":"run_demo1","seq":19,"ts":"2026-10-05T14:10:41.078Z","type":"model_reasoning","text":"Main subject f0 has eyes closed in p_8833 (open p 0.06/0.04); group has 4 frames. Policy 1/3: compare within the group rather than reject this frame outright. Face quality next, then rank."}
{"run_id":"run_demo1","seq":20,"ts":"2026-10-05T14:10:41.080Z","type":"tool_call","tool":"score_face_quality","args":{"photo_ids":["p_8830","p_8831","p_8832","p_8833"]},"model":"fake-planner-v1"}
{"run_id":"run_demo1","seq":21,"ts":"2026-10-05T14:10:41.084Z","type":"tool_result","tool":"score_face_quality","result":{"per_photo":[{"photo_id":"p_8830"},{"photo_id":"p_8831"},{"photo_id":"p_8832"},{"photo_id":"p_8833"}]},"latency_ms":1.37,"opencv":"5.0.0"}
{"run_id":"run_demo1","seq":22,"ts":"2026-10-05T14:10:41.094Z","type":"model_reasoning","text":"All frames scored. Rank the group."}
{"run_id":"run_demo1","seq":23,"ts":"2026-10-05T14:10:41.097Z","type":"tool_call","tool":"pick_best_in_cluster","args":{"group_id":"g_412"},"model":"fake-planner-v1"}
{"run_id":"run_demo1","seq":24,"ts":"2026-10-05T14:10:41.101Z","type":"tool_result","tool":"pick_best_in_cluster","result":{"best_photo_id":"p_8833","tie":true,"margin":0.0005,"ranking":[{"photo_id":"p_8833","score":0.781},{"photo_id":"p_8832","score":0.7805},{"photo_id":"p_8831","score":0.7653},{"photo_id":"p_8830","score":0.5251}]},"latency_ms":1.87,"opencv":"5.0.0"}
{"run_id":"run_demo1","seq":25,"ts":"2026-10-05T14:10:41.113Z","type":"model_reasoning","text":"Margin 0.00 < 0.05 (Policy 5); top-scored p_8833 has a defect. Make a contact sheet for the reviewer."}
{"run_id":"run_demo1","seq":26,"ts":"2026-10-05T14:10:41.115Z","type":"tool_call","tool":"make_contact_sheet","args":{"group_id":"g_412"},"model":"fake-planner-v1"}
{"run_id":"run_demo1","seq":27,"ts":"2026-10-05T14:10:41.118Z","type":"tool_result","tool":"make_contact_sheet","result":{"key":"sheets/ev_demo/g_412.jpg","n_tiles":4,"width":1024,"height":300},"latency_ms":0.93,"opencv":"5.0.0"}
{"run_id":"run_demo1","seq":28,"ts":"2026-10-05T14:10:41.131Z","type":"model_reasoning","text":"Escalate p_8833 vs p_8832 with the contact sheet; proposed keeper p_8832."}
{"run_id":"run_demo1","seq":29,"ts":"2026-10-05T14:10:41.133Z","type":"tool_call","tool":"request_human_approval","args":{"items":[{"group_id":"g_412","proposed":{"keep":["p_8832"],"reject":["p_8830"],"review":["p_8833","p_8831"]},"reason":"Top two frames within 0.00; main subject's eyes closed in p_8833","confidence":0.6,"contact_sheet_key":"sheets/ev_demo/g_412.jpg"}]},"model":"fake-planner-v1"}
{"run_id":"run_demo1","seq":30,"ts":"2026-10-05T14:10:41.150Z","type":"interrupt","text":"run paused for 1 approval item(s)","pending":[{"interrupt_id":"v1:tool_call:tooluse_fake_ad3af5_0010:8a96bf70-0883-5df0-b2f4-54c2929ba4e8","name":"keepframe-approval:e529a4c663f1","items":[{"group_id":"g_412","photo_id":null,"proposed":{"keep":["p_8832"],"reject":["p_8830"],"review":["p_8833","p_8831"]},"reason":"Top two frames within 0.00; main subject's eyes closed in p_8833","confidence":0.6,"contact_sheet_key":"sheets/ev_demo/g_412.jpg"}],"kind":"keepframe_approval"}]}
{"run_id":"run_demo1","seq":31,"ts":"2026-10-05T14:10:43.303Z","type":"interrupt_resumed","decisions":[[{"group_id":"g_412","human_verdict":{"keep":["p_8831"],"reject":["p_8830","p_8832","p_8833"]},"note":"8831 has the better expression"}]]}
{"run_id":"run_demo1","seq":32,"ts":"2026-10-05T14:10:43.317Z","type":"tool_result","tool":"request_human_approval","result":{"decisions":[{"group_id":"g_412","human_verdict":{"keep":["p_8831"],"reject":["p_8830","p_8832","p_8833"]},"note":"8831 has the better expression","id":"g_412"}]},"latency_ms":4.21,"opencv":"5.0.0"}
{"run_id":"run_demo1","seq":33,"ts":"2026-10-05T14:10:43.331Z","type":"model_reasoning","text":"Human answered: 8831 has the better expression. Write their verdicts as decided_by=human."}
{"run_id":"run_demo1","seq":34,"ts":"2026-10-05T14:10:43.333Z","type":"tool_call","tool":"write_decision","args":{"decisions":[{"photo_id":"p_8831","verdict":"keep","reason_codes":["KEEP_BEST_OF_GROUP"],"confidence":1.0,"group_id":"g_412","decided_by":"human"},{"photo_id":"p_8830","verdict":"reject","reason_codes":["BURST_NOT_BEST"],"confidence":1.0,"group_id":"g_412","decided_by":"human"},{"photo_id":"p_8832","verdict":"reject","reason_codes":["BURST_NOT_BEST"],"confidence":1.0,"group_id":"g_412","decided_by":"human"},{"photo_id":"p_8833","verdict":"reject","reason_codes":["BURST_NOT_BEST"],"confidence":1.0,"group_id":"g_412","decided_by":"human"}]},"model":"fake-planner-v1"}
{"run_id":"run_demo1","seq":35,"ts":"2026-10-05T14:10:43.347Z","type":"tool_result","tool":"write_decision","result":{"written":4,"verdict_counts":{"keep":1,"reject":3,"review":0}},"latency_ms":8.94,"opencv":"5.0.0"}
```

Seq 6 → 10/13 → 14 → 15: `p_8831` is borderline on the full frame (lap_var 74.2) with 2 faces, so the agent
detects faces and its next sharpness call is `roi="faces"`, which comes back sharp (188.9). Seq 18 → 19 → 23: the
main subject is closed in `p_8833`, so the frames are compared within the group (pick_best). Seq 24 → 25 → 29:
margin 0.0005 < 0.05 leads to the contact sheet and `request_human_approval`, then interrupt 30. Seq 31 → 34: the
human answer is written with `decided_by=human`. Exposure lines 7 to 9 are omitted.


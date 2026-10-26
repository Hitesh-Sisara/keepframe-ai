# Evidence: a 2,442-photo real event culled end to end on the deployed stack (agent on AgentCore Runtime)

Event: **E01 (Ceremony, 2,442 photos, one camera body; 2,174 have an original in the source store)**. This is the only one of the 12
selected events with at least 2,000 photos (the next largest is E02 Ceremony at 1,850). Infra's own full run on the
same release was E03 (`run_aws_e03_agentcore_full_20261008T061112Z`), so this run does not duplicate it.

- Execution: `arn:aws:states:ap-south-1:<AWS_ACCOUNT_ID>:execution:CullEvent:run_aws_e01_2k_20261008T061801Z`
- Input: `{"event_id": "<E01>", "run_id": "run_aws_e01_2k_20261008T061801Z", "flavor": "stock"}`. No skip flags, so the
  full pipeline ran: sync, worker, originals, agent.
- Release: worker task def `cull-worker:7` and the AgentCore runtime `keepframe_agent-9GtNw85LzO` version 2 (READY),
  both on `main-ec7cf96`. `cull-sync:7` is on `main-ec7cf96` too. The API image is from aa9ed21.
- Result: **SUCCEEDED**, 2026-10-08 06:18:03.567 to 06:26:32.191 UTC, **508.6 s (8 min 29 s) total pipeline wall time**.

## Step Functions wall time per step (`get-execution-history`)

| Step | Wall (s) | Notes |
|---|---:|---|
| ValidateManifest | 1.1 | manifest_rows 2,442 = n_photos_meta 2,442 |
| Source sync (cull-sync, Fargate ARM64; the step is now `SyncSource`) | 119.4 | processed 2,442 keys, exif 2,174 keys. The raw/exif objects were already in S3 from the 2026-10-05 export (LastModified unchanged), so this pass listed and checked them and rewrote nothing |
| CullWorkerStock (cull-worker, Fargate ARM64 4 vCPU) | 177.3 | 134.1 s in the container plus about 43 s of Fargate scheduling and image pull |
| PlanOriginals (Lambda arm64) | 2.5 | 1,075 candidates, 1,006 requested (69 have no original in the source store) |
| SyncOriginals (cull-sync) | 144.4 | 1,006 originals copied source → S3, 14.9 GB (object LastModified 06:23:35 to 06:25:01, about 175 MB/s once copying) |
| RunAgent (Lambda → InvokeAgentRuntime) | 1.8 | `via: agentcore`, `status: running` |
| WaitForAgent + AgentStatus, 2 loops | 61.2 | poll 1 `running` (30 s); poll 2 `paused`, `pending_approvals: 1` (61 s) |
| Summarise + Notify | 0.8 | Summarise returned status `awaiting_approval` with verdict counts |

Worker throughput: **18.2 img/s** in the container (2,442 / 134.138 s; RUN# `worker_images_per_s` 18.205). Over the
whole step including task start it is 13.8 img/s (2,442 / 177.3 s). Smaller events on the same release ran faster,
for example E03 (664 photos) at 30.0 img/s in 22.1 s. This event is not cache-warm: proxies were recomputed for
2,322 of 2,442 photos (`decode_full` n=2,322) and 1,019 frames went through face analysis.

## DynamoDB RUN# (`keepframe-decisions`, pk `EVENT#<E01>`, sk `RUN#run_aws_e01_2k_20261008T061801Z`)

| Field | Value |
|---|---|
| status | `awaiting_approval` (paused_at 06:26:15.528Z) |
| run_kind | `deployed` |
| agent_host | `agentcore` (agent_session_id `keepframe-run_aws_e01_2k_20261008T061801Z`) |
| opencv_version / opencv_hal | 5.0.0 / `carotene` (cv2 from `/usr/local/lib/python3.12/site-packages`) |
| instance_type | `fargate-arm64-4vcpu` |
| llm_provider / planner / reasons | `anthropic` (Anthropic-compatible provider setting) / claude-sonnet-5 / claude-haiku-4.5 |
| worker_started_at / worker_ended_at | 06:20:22.096Z / 06:22:36.235Z |
| worker_wall_s / worker_images_per_s / worker_status | 134.138 / 18.205 / ok |
| agent_started_at / agent_wall_s | 06:25:53.227Z / 22.13 s from start to pause |
| tool_call_count (agent_tool_calls) | 6 |
| agent_model_calls | not a RUN# field. The trace has 4 `model_reasoning` turns, 7 `tool_call` entries and 6 `tool_result` entries. The 7th call, `request_human_approval`, became the interrupt, which is why RUN# counts 6 |
| worker_escalations / agent_escalations | 239 / 1 |
| verdict_counts | keep 1,784 / reject 455 / review 203 |
| n_photos / photos_processed / n_groups / agent_photos | 2,442 / 2,442 / 417 / 642 |
| originals_candidates / originals_requested | 1,075 / 1,006 |

## Worker summary.json (S3 `results/<E01>/summary.json`)

- Status `ok`, **0 failures**, 4 worker processes, faces available. 417 groups and 1,373 singletons.
- Reason codes: KEEP_SINGLETON 1,367; KEEP_BEST_OF_GROUP 417; BURST_NOT_BEST 561; REVIEW_LOW_CONFIDENCE 198;
  EYES_CLOSED 126; DUPLICATE_OF 91; REVIEW_POLICY_GROUP_EYES 38; LOW_CONTRAST 3; EXPOSURE_UNDER 2; EXPOSURE_OVER 1.
- Stage wall times: analyse 82.9 s, proxies+hashes 38.5 s, ingest 9.2 s, decide 1.6 s, list 1.0 s,
  prefetch_groups 0.7 s, write 0.1 s.
- Per-image p50 / p95 (ms): full decode 36.6 / 51.8, exposure 18.9 / 22.5, sharpness 20.3 / 56.8, faces 30.6 / 121.9,
  face quality 52.1 / 196.9, eyes 10.8 / 36.1, saliency 34.1 / 35.0, hashes 4.8 / 5.5.
- S3 I/O: 4,677 GETs (1.02 GB), 2,559 PUTs (0.44 GB), 5 LISTs, 0 HEADs.

## Agent on AgentCore Runtime

- Log group `/aws/bedrock-agentcore/runtimes/keepframe_agent-9GtNw85LzO-DEFAULT`: **35 lines for this run** during
  06:25–06:26 UTC. They break down as 19 trace lines (run_header, 4 model_reasoning, 7 tool_call, 6 tool_result,
  1 interrupt), 13 EMF metric lines (6 tool_calls, 6 stage_latency_ms, 1 escalations) and 9 runtime log lines.
  Three more lines appeared later (06:33 on 10-08, 06:06 and 06:09 on 10-09). These are the read-only status checks
  from `GET /runs/<id>/agent` during evidence collection, not agent work.
- Agent path: next_work_item → score_sharpness → score_exposure → detect_faces (0 faces) → pick_best_in_cluster
  (tie=true, margin 0.0133) → make_contact_sheet → request_human_approval. P5 fired (margin < 0.05), so the agent
  paused for a person after 22 s.
- **Pending approval: still unanswered** (checked 2026-10-09 06:09 UTC). The GROUP# item has `approval_status=pending`
  and no human_decision. It was last updated 2026-10-08 06:26:15 UTC. RUN# is still `awaiting_approval`, and the API
  approvals queue lists it with no answer, human_source or labeller. Reason shown: "Near-tied scores (0.75 vs 0.7367,
  margin 0.0133), both sharp … pick_best_in_cluster tie=true", agent confidence 0.65. It was not answered, and it is
  left for a real reviewer.

## Deployed API view (team token from Secrets Manager `keepframe/team-token`, not printed)

- `GET /runs/run_aws_e01_2k_20261008T061801Z` → **200**: status awaiting_approval, run_kind deployed,
  agent_host agentcore, verdict_counts 1,784 / 455 / 203, escalations {worker 239, agent 1}, tool_call_count 6,
  photos_processed 2,442, n_groups 417, worker_wall_s 134.138, agent_wall_s 22.13, event block "Event 1 · <category>".
- `GET /runs/<id>/agent` → **200**: path `agentcore`, live `{status: paused}` (checked 2026-10-09 06:09 UTC).
- `GET /events/<E01>/groups?run_id=<id>` → **200**: source `merged`, total 417 groups (24 per page with a cursor),
  event_verdict_counts 1,784 / 455 / 203, matching RUN# and summary.json.
- `GET /approvals` → **200**: 5 pending items across all runs, 1 of them from this run.

## Caveats (stated honestly)

1. **Per-photo decisions are not in DynamoDB.** For this run DynamoDB holds the RUN# item and 1 GROUP# item (the
   pending approval). It holds **0 PHOTO# rows**. All 2,442 verdicts are in S3 `results/<E01>/decisions.jsonl`
   (3.9 MB), and the API merges that file with any DynamoDB overlay. PHOTO# rows are written only by the agent's
   `write_decision` and by human answers (`keepframe/store/dynamodb.py`). The E07 deployed run has the same shape.
   If the checklist line "decisions in DynamoDB" means every per-photo verdict, the worker does not meet it today.
   What it does meet: run status, verdict counts, timings and the approval queue are in DynamoDB.
2. RUN# `wall_s` (134.1 s) is the worker's wall time, not the pipeline's. The pipeline took 508.6 s, from the Step
   Functions execution.
3. summary.json, decisions.jsonl and groups.json are written to `results/<E01>/` without a run_id path segment. The
   next run of E01 will overwrite them. This note copies the figures out of them.
4. summary.json `image_sha` is null, so the image is identified only by its tag (`main-ec7cf96`).
5. The source sync rewrote nothing because raw/exif were already in S3. A cold first sync of 2,442 processed images
   (1.0 GB) was not measured in this run. SyncOriginals (14.9 GB) was a real cold copy.

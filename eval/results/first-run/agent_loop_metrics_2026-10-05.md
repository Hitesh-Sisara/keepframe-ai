# Evaluation metrics

Generated 2026-10-05T16:18:43+00:00 from 2 event(s); 0 with human labels, 0 scored photos. Definitions: docs/EVALUATION.md and the compute_metrics.py docstring.

## Per-reason precision / recall / F1

no labels yet (eval/labels/<eventId>.csv missing).

## Overall reject

no labels yet (eval/labels/<eventId>.csv missing).

## Duplicate clusters vs human groups

no labels yet (eval/labels/<eventId>.csv missing).

## Best of burst

no labels yet (eval/labels/<eventId>.csv missing).

## Inter-annotator agreement (Cohen's kappa)

no labels yet (eval/labels/<eventId>.csv missing).

## Agent loop (from trace.jsonl)

`scripted` answers come from a scripted reviewer (demo/CI) and are excluded from every human-agreement figure.

| event | run | run status | tool calls | per event photo | per agent photo | pauses | % escalated | pending | human agreed | scripted excluded | pivots | tool latency p50 / p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E03 | run_aws_e03_20261005T155605Z | awaiting_approval | 9 | 0.01 | n/a | 1 | 0.30 | 2 | n/a (no real human answers) | 0 | 1 | 102.10 / 276.55 |
| E09 | E09-agent-141910Z | finished | 115 | 0.17 | 4.79 | 10 | 3.18 | 0 | n/a (no real human answers) | 21 | 20 | 234.19 / 3787.81 |

### E03 details

- model claude-sonnet-5 (anthropic); 23 trace lines; 21 work items; run wall n/a s; model calls n/a
- photos: 664 in event, 0 decided by the agent loop, 2 escalated in 1 approval item(s) (n/a % of agent-decided photos)
- run status: awaiting_approval; human source of escalated photos: pending 2 (pending = escalated, not answered yet)
- pivots by policy: Policy 5 1; policy hooks fired: none
- tool calls: 9 counted in the trace, 8 excluding next_work_item (run summary reports n/a)
- tool calls by tool: detect_faces 1, make_contact_sheet 1, next_work_item 1, pick_best_in_cluster 1, request_human_approval 1, score_exposure 1, score_eyes_open 1, score_face_quality 1, score_sharpness 1
- tool results not ok: 0; loop-guard triggers: 0; stopped: None

| tool | n | p50 ms | p95 ms |
|---|---|---|---|
| detect_faces | 1 | 189.22 | 189.22 |
| make_contact_sheet | 1 | 94.19 | 94.19 |
| next_work_item | 1 | 0.45 | 0.45 |
| pick_best_in_cluster | 1 | 0.62 | 0.62 |
| score_exposure | 1 | 110.02 | 110.02 |
| score_eyes_open | 1 | 93.29 | 93.29 |
| score_face_quality | 1 | 151.31 | 151.31 |
| score_sharpness | 1 | 323.58 | 323.58 |

### E09 details

- model claude-sonnet-5 (anthropic); 280 trace lines; 12 work items; run wall 533.72 s; model calls 72
- photos: 661 in event, 24 decided by the agent loop, 21 escalated in 10 approval item(s) (87.50 % of agent-decided photos)
- run status: finished; human source of escalated photos: scripted 21 (pending = escalated, not answered yet)
- pivots by policy: Policy 3 10, Policy 5 10; policy hooks fired: policy_P1 1
- tool calls: 115 counted in the trace, 102 excluding next_work_item (run summary reports 102)
- tool calls by tool: next_work_item 13, detect_faces 12, score_exposure 12, score_eyes_open 12, score_sharpness 12, write_decision 12, pick_best_in_cluster 11, score_face_quality 11, make_contact_sheet 10, request_human_approval 10
- tool results not ok: 1; loop-guard triggers: 0; stopped: None

| tool | n | p50 ms | p95 ms |
|---|---|---|---|
| detect_faces | 12 | 3162.74 | 3514.89 |
| make_contact_sheet | 10 | 1387.43 | 1464.83 |
| next_work_item | 13 | 1.20 | 4.56 |
| pick_best_in_cluster | 11 | 1.58 | 2.68 |
| request_human_approval | 10 | 1.46 | 3.65 |
| score_exposure | 12 | 18.34 | 2135.66 |
| score_eyes_open | 12 | 213.81 | 270.71 |
| score_face_quality | 11 | 285.63 | 650.65 |
| score_sharpness | 12 | 1062.45 | 2869.81 |
| write_decision | 12 | 3789.22 | 4225.13 |

## Run counts (decisions.jsonl) and group shape (groups.json)

| event | photos | keep / reject / review | decided by | human source | needs approval | groups | singletons | size histogram | ties | margin < 0.05 |
|---|---|---|---|---|---|---|---|---|---|---|
| E03 | 664 | 633 / 8 / 23 | worker 664 | none | 23 | 27 | 607 | 2 25, 3 1, 4 1 | 7 | 10 |
| E09 | 661 | 609 / 39 / 13 | worker 637, scripted 21, agent 3 | scripted 21 | 0 | 50 | 560 | 2 49, 3 1 | 1 | 7 |

## Robustness

No second run (results/<eventId>.run2/) yet.

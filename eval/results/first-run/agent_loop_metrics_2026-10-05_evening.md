# Evaluation metrics, agent loop (no labels yet), 2026-10-05 evening

Labelling source for E07: the complete post-fix run E07-aws-184515Z (1,000/1,000 photos). Event and run ids anonymised.

Generated 2026-10-05T19:12:41+00:00 from 3 event(s); 0 with human labels, 0 scored photos. Definitions: docs/EVALUATION.md and the compute_metrics.py docstring.

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

Only answers with a recorded human source (ui, file, http) count as human. `scripted` answers (demo/CI reviewer) and answers with no recorded source are counted separately and excluded from every human-agreement figure.

| event | run | run status | tool calls | per event photo | per agent photo | pauses | % escalated | pending | human agreed | scripted / unknown excluded | pivots | tool latency p50 / p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E07 | E07-aws-184515Z | awaiting_approval | 9 | 0.01 | n/a | 1 | 0.90 | 9 | n/a (no real human answers) | 0 / 0 | 2 | 277.63 / 1136.89 |
| E03 | E03-aws-155605Z | awaiting_approval | 19 | 0.03 | 9.50 | 2 | 0.90 | 4 | n/a (no real human answers) | 0 / 2 | 2 | 102.10 / 514.93 |
| E09 | E09-agent-141910Z | finished | 115 | 0.17 | 4.79 | 10 | 3.18 | 0 | n/a (no real human answers) | 21 / 0 | 20 | 234.19 / 3787.81 |

### E07 details

- model claude-sonnet-5 (anthropic); 24 trace lines; 98 work items; run wall n/a s; model calls n/a
- photos: 1000 in event, 0 decided by the agent loop, 9 escalated in 1 approval item(s) (n/a % of agent-decided photos)
- trace.jsonl holds 2 runs; metrics are for E07-aws-184515Z (the run in summary.json); ignored: E07-aws-170642Z(partial)
- run status: awaiting_approval; human source of escalated photos: pending 9 (pending = escalated, not answered yet)
- pivots by policy: Policy 3 1, Policy 5 1; policy hooks fired: none
- tool calls: 9 counted in the trace, 8 excluding next_work_item (run summary reports n/a)
- tool calls by tool: detect_faces 1, make_contact_sheet 1, next_work_item 1, pick_best_in_cluster 1, request_human_approval 1, score_exposure 1, score_eyes_open 1, score_face_quality 1, score_sharpness 1
- tool results not ok: 0; loop-guard triggers: 0; stopped: None

| tool | n | p50 ms | p95 ms |
|---|---|---|---|
| detect_faces | 1 | 1174.14 | 1174.14 |
| make_contact_sheet | 1 | 101.48 | 101.48 |
| next_work_item | 1 | 0.52 | 0.52 |
| pick_best_in_cluster | 1 | 0.90 | 0.90 |
| score_exposure | 1 | 181.85 | 181.85 |
| score_eyes_open | 1 | 373.42 | 373.42 |
| score_face_quality | 1 | 611.04 | 611.04 |
| score_sharpness | 1 | 1067.70 | 1067.70 |

### E03 details

- model claude-sonnet-5 (anthropic); 48 trace lines; 21 work items; run wall n/a s; model calls n/a
- photos: 664 in event, 2 decided by the agent loop, 6 escalated in 2 approval item(s) (100.00 % of agent-decided photos)
- run status: awaiting_approval; human source of escalated photos: pending 4, unknown 2 (pending = escalated, not answered yet)
- pivots by policy: Policy 5 2; policy hooks fired: none
- tool calls: 19 counted in the trace, 17 excluding next_work_item (run summary reports n/a)
- tool calls by tool: detect_faces 2, make_contact_sheet 2, next_work_item 2, pick_best_in_cluster 2, request_human_approval 2, score_exposure 2, score_eyes_open 2, score_face_quality 2, score_sharpness 2, write_decision 1
- tool results not ok: 0; loop-guard triggers: 0; stopped: None

| tool | n | p50 ms | p95 ms |
|---|---|---|---|
| detect_faces | 2 | 399.35 | 588.46 |
| make_contact_sheet | 2 | 92.70 | 94.04 |
| next_work_item | 2 | 0.44 | 0.45 |
| pick_best_in_cluster | 2 | 0.62 | 0.62 |
| request_human_approval | 1 | 0.54 | 0.54 |
| score_exposure | 2 | 304.13 | 478.84 |
| score_eyes_open | 2 | 109.41 | 123.92 |
| score_face_quality | 2 | 172.63 | 191.82 |
| score_sharpness | 2 | 192.94 | 310.52 |
| write_decision | 1 | 243.31 | 243.31 |

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
| E07 | 1000 | 301 / 624 / 75 | worker 1000 | none | 211 | 171 | 132 | 2 66, 3 38, 4 23, 5 7, 6 8, 7 3, 8 1, 9 8, 10 1, 11 2, 12 2, 13 2, 15 2, 17 1, 18 2, 23 1, 24 1, 29 1, 30 1, 75 1 | 40 | 107 |
| E03 | 664 | 633 / 8 / 23 | worker 662, human 2 | none | 21 | 27 | 607 | 2 25, 3 1, 4 1 | 7 | 10 |
| E09 | 661 | 609 / 39 / 13 | worker 637, scripted 21, agent 3 | scripted 21 | 0 | 50 | 560 | 2 49, 3 1 | 1 | 7 |

## Robustness

No second run (results/<eventId>.run2/) yet.

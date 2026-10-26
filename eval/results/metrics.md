# Evaluation metrics

Generated 2026-10-09T07:34:12+00:00 by `eval/compute_metrics.py` from 4 event(s). Photographer-labelled photos: 0. Definitions: docs/EVALUATION.md and the compute_metrics.py docstring. Events are E-index aliases; run ids are `<event>-<kind>-<UTC timestamp>`.

Honesty rules applied: only `labeller=photographer` rows are ground truth. Team labels are reported separately as "team labels (not photographer)". Answers from the scripted reviewer and the team's mechanism tests (`lead_test`) are counted but never enter a human-agreement figure.

## Label status

| event | label rows by class |
|---|---|
| E03 | photographer 0, team 0 (not photographer), unclassified 0 |
| E01 | photographer 0, team 0 (not photographer), unclassified 0 |
| E07 | photographer 0, team 0 (not photographer), unclassified 0 |
| E09 | photographer 0, team 0 (not photographer), unclassified 0 |

## Per-reason precision / recall / F1

| reason | support (n positives) | precision | recall | F1 |
|---|---|---|---|---|
| BLUR_MOTION | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) |
| BLUR_FOCUS | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) |
| EYES_CLOSED | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) |
| DUPLICATE | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) |
| EXPOSURE_UNDER | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) |
| EXPOSURE_OVER | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) |
| LOW_CONTRAST | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) |
| NO_SUBJECT | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) | no photographer labels yet (n=0) |

## Overall reject precision / recall

no photographer labels yet (n=0).

## Duplicate clusters vs human groups (purity / completeness)

no photographer labels yet (n=0).

## Best of burst (top-1 / top-2)

no photographer labels yet (n=0).

## Inter-annotator agreement (Cohen's kappa)

no photographer labels yet (n=0).

## Escalated photos: human agreed with the agent's proposal

no photographer labels yet (n=0).

## Team labels (not photographer)

No team labels yet (n=0). `eval/sample_for_labelling.py --labeller team` prepares them.

## Agent runs (label-independent, every run in each event's trace)

Pivots use the API's own rule set (`services/api/app/pivots.py`), so they match `GET /runs/{id}/trace` with limit=500. Active s = agent working time from trace timestamps (time waiting for an answer excluded). Answer sources: `pending` = escalated, not answered; `scripted` = auto-approver; `lead_test` = the team's mechanism tests; `ui`/`file`/`http` = a person.

| event | run | host | status | tool calls | model calls | pauses / resumes | escalated photos | answer sources | pivots (by policy) | active s | tool p50 / p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| E03 | E03-aws-20261005T155605Z | fargate-arm64-4vcpu | awaiting_approval | 38 | n/a | 3 / 2 | 8 | lead_test 6, pending 2 | 5 (Policy 5 3, Policy 3 2) | 136.90 | 91.21 / 469.55 |
| E03 | E03-agentcore-20261008T050441Z | agentcore | awaiting_approval | 19 | n/a | 2 / 1 | 6 | lead_test 2, pending 4 | 2 (Policy 5 2) | 61.50 | 125.81 / 497.68 |
| E03 | E03-agentcore-20261008T061112Z | agentcore | awaiting_approval | 9 | n/a | 1 / 0 | 2 | pending 2 | 1 (Policy 5 1) | 23.50 | 121.56 / 300.49 |
| E03 | E03-agentcore-20261008T062729Z (results) | agentcore | awaiting_approval | 9 | n/a | 1 / 0 | 2 | pending 2 | 1 (Policy 5 1) | 24.50 | 119.97 / 292.64 |
| E01 | E01-aws-20261008T061801Z (results) | agentcore | awaiting_approval | 7 | n/a | 1 / 0 | 2 | pending 2 | 1 (Policy 5 1) | 22.30 | 113.29 / 656.76 |
| E07 | E07-aws-20261005T170642Z | fargate-arm64-4vcpu | awaiting_approval | 9 | n/a | 1 / 0 | 9 | pending 9 | 2 (Policy 3 1, Policy 5 1) | 49.80 | 287.16 / 967.92 |
| E07 | E07-aws-20261005T184515Z (results) | fargate-arm64-4vcpu | awaiting_approval | 9 | n/a | 1 / 0 | 9 | pending 9 | 2 (Policy 3 1, Policy 5 1) | 49.10 | 277.63 / 1136.89 |
| E09 | E09-agent-20261005T141910Z (results) | local | finished | 115 | 72 | 10 / 10 | 21 | scripted 21 | 20 (Policy 3 10, Policy 5 10) | 564.70 | 234.19 / 3787.81 |

Totals over 8 agent runs: 215 tool calls, 20 pauses, 13 resumes, 34 pivots (Policy 3 14, Policy 5 20). Model tokens are not recorded in trace.jsonl; tokens and $ come from the determinism runs below.

## Run counts (decisions.jsonl) and group shape (groups.json)

| event | photos | keep / reject / review | decided by | answer source on rows | needs approval | groups | singletons | group-size histogram | ties | margin < 0.05 |
|---|---|---|---|---|---|---|---|---|---|---|
| E03 | 664 | 633 / 22 / 9 | worker 664 | none | 20 | 27 | 607 | 2 25, 3 1, 4 1 | 4 | 10 |
| E01 | 2442 | 1784 / 455 / 203 | worker 2442 | none | 483 | 417 | 1373 | 2 302, 3 48, 4 45, 5 11, 6 4, 7 1, 8 4, 9 1, 14 1 | 88 | 218 |
| E07 | 1000 | 301 / 624 / 75 | worker 1000 | none | 211 | 171 | 132 | 2 66, 3 38, 4 23, 5 7, 6 8, 7 3, 8 1, 9 8, 10 1, 11 2, 12 2, 13 2, 15 2, 17 1, 18 2, 23 1, 24 1, 29 1, 30 1, 75 1 | 40 | 107 |
| E09 | 661 | 609 / 39 / 13 | worker 637, scripted 21, agent 3 | scripted 21 | 0 | 50 | 560 | 2 49, 3 1 | 1 | 7 |

Figures: `verdicts_by_event.png`, `group_sizes.png`, `confusion_<REASON>.png` (empty until labels).

## Robustness: agent determinism

E09 run twice with claude-sonnet-5 (approvals: scripted approve-as-proposed (not human)): **83.33 % identical final verdicts** over 24 agent-loop photos; identical reason sets 58.33 %; identical proposals on photos escalated in both 80.95 %; same tool-call sequence: False. Source: `eval/results/determinism.md`.

| run | tool calls | model cycles | input tokens | output tokens | cycles with 0 output tokens | $ at list price |
|---|---|---|---|---|---|---|
| A | 100 | 89 | 1,161,137 | 1,233 | 57 | 2.33 |
| B | 100 | 91 | 1,175,201 | 1,159 | 62 | 2.36 |

$ per event (mean of the two runs, Anthropic list price, no prompt caching): 2.35. The endpoint reported 0 output tokens on 119 of 180 cycles (tool-only turns), so output tokens and $ are lower bounds; input tokens dominate the cost.

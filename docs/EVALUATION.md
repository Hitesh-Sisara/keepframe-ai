# Evaluation

The method is final. Label-independent numbers are measured and committed now (§3.1); label-dependent ones
read "no photographer labels yet (n=0)" until a photographer labels the sample, then fill in by re-running
one command (§3.3). Every number quoted here must match a committed file under `eval/results/` or
`bench/results/`. Write "TBD", never a guess.

## 1. Ground truth

| item | value |
|---|---|
| Events | 3 planned: E07 Engagement (primary, burst-heavy), E03 Ceremony, E09 Birthday (see characteristics below). The design called for 5 events; the other events in the set have almost no multi-frame bursts. |
| Labelled photos | target 1,300: E07 700 (whole bursts only), E03 300 and E09 300 (all their bursts plus random singletons) |
| Sampling | Whole agent groups (bursts, never cut) plus random singletons per event, `eval/sample_for_labelling.py --seed 20261005` (default plan `E07=700:1.0 E03=300:0.3 E09=300:0.2`); regenerated after the core burst-policy fix |
| Labellers | None yet. Ground truth = rows with `labeller=photographer[:<initials>]` only. Team members may label too (`labeller=team[:<initials>]`, file `<eventId>.team.csv`); those are reported only as "team labels (not photographer)" with their own κ vs the worker and the agent, never as ground truth. Lead mechanism-test answers are dropped. |
| Overlap for agreement | ≈ 20 % of photos, whole sheets, labelled independently |
| Guide | `eval/LABEL_GUIDE.md` |
| Storage | `eval/labels/<eventId>.csv` (photographer) and `<eventId>.team.csv` (team), gitignored. Todo files: `eval/sample_for_labelling.py --labeller photographer` → `<eventId>.todo.csv`, `--labeller team:<initials>` → `<eventId>.team.todo.csv` (same 1,300 photos, same seed). `--stamp <csv> --labeller ...` re-stamps a CSV exported from the `/label` page. Only aggregate metrics are committed. |

Inter-annotator agreement (Cohen's κ on the overlap, verdict keep/reject): no photographer labels yet
(n=0). Per-reason κ for codes both labellers used: no photographer labels yet (n=0). Read the agent's scores against this ceiling: an agent cannot be
meaningfully "more accurate" than two humans agree with each other.

### Dataset characteristics

Current table with EXIF availability and the 2,442-photo E01 run: `eval/results/dataset.md` (2026-10-09). The
2026-10-05 table below is kept for the labelling sample's provenance.

#### 2026-10-05 table

From each event's `results/<eventId>/groups.json` and the existing face metadata in
`manifests/<eventId>.jsonl` (frsInfo boxes; `no_faces` counts as 0). Produced by
`eval/dataset_characteristics.py` → `eval/results/dataset_characteristics.{json,md}` (aggregates only).
Source runs: E07 = the complete post-fix run `run_aws_e07_20261005T184515Z` (1,000 / 1,000 photos, 0
failures), E03 = `run_aws_e03_20261005T155605Z`, E09 = the 12:48 UTC worker run.

| | E07 (Engagement) | E03 (Ceremony) | E09 (Birthday) |
|---|---|---|---|
| photos (processed) | 1,000 (1,000) | 664 (664) | 661 (661) |
| bursts (multi-photo groups) | 171 | 27 | 50 |
| photos in bursts | 868 (86.8 % of processed) | 57 (8.6 % of processed) | 101 (15.3 % of processed) |
| singletons | 132 | 607 | 560 |
| bursts of size 2 / 3-5 / 6-10 / 11-20 / 21+ | 66 / 68 / 21 / 11 / 5 | 25 / 2 / 0 / 0 / 0 | 49 / 1 / 0 / 0 / 0 |
| bursts with ≥ 3 frames | 105 | 2 | 1 |
| largest / median burst | 75 / 3 | 4 / 2 | 3 / 2.0 |
| exact duplicates | 184 | 0 | 36 |
| faces per frame, median (mean) | 2.0 (2.22) | 2.0 (3.03) | 3 (3.93) |
| frames with 0 / 1 / 2 / 3-4 / 5+ faces (%) | 1.8 / 6.9 / 82.0 / 3.5 / 5.8 | 8.0 / 28.8 / 26.4 / 15.8 / 21.1 | 11.2 / 7.6 / 18.3 / 30.0 / 33.0 |
| cameras | 1 | 2 | 2 |

How to read it:

- **E07 is the only burst-heavy event.** 87 % of its frames are in 171 multi-frame bursts, 105 of
  them with ≥ 3 frames and five with more than 20 (largest 75). It is mostly two-face couple portraits
  (82 % of frames have exactly 2 faces). So best-of-burst agreement, the duplicate-cluster metrics and
  eye-state within a burst are measurable on E07, and E07 is the primary labelling event.
- **E09 and E03 are mostly singletons.** 15 % and 9 % of frames are in bursts, almost all of size 2
  (only one and two bursts have 3+ frames). With size-2 bursts, best-of-burst top-1 vs top-2 is
  degenerate (top-2 is always 100 %) and there are too few groups for a stable rate. On these events
  best-of-burst is reported for completeness but should not be read as a result. They are labelled for
  per-photo reasons (blur, exposure, eyes; group photos with 3+ faces).
- Exact duplicates (same md5) are counted separately and are already members of their bursts.
- The earlier E07 run `run_aws_e07_20261005T170642Z` is **partial** (83 frames failed with
  `NoSuchKey` although their objects were present; worker defect, since fixed) and its verdicts came
  from the burst-policy defect. It is not used for any number here.

### Labelling sample (generated 2026-10-05 from the runs above)

`eval/sample_for_labelling.py` (seed 20261005, default plan): **1,300 photos**.

| event | photos | in whole bursts | singletons | kappa overlap (2nd labeller) | contact-sheet pages |
|---|---|---|---|---|---|
| E07 Engagement | 700 | 699 in 130 bursts (largest 75, 30, 29) | 1 | 140 | 136 |
| E03 Ceremony | 300 | 57 in 27 bursts (all of them) | 243 | 61 | 48 |
| E09 Birthday | 300 | 59 in 29 bursts | 241 | 60 | 50 |

Labeller files (gitignored, contain photo ids / customer imagery, never committed):
`eval/labels/<eventId>.todo.csv` and `eval/contact_sheets/<eventId>/*.jpg`; counts in
`eval/labels/sample_plan.json`.

## 2. Metrics and definitions

Produced by `eval/compute_metrics.py` → `eval/results/metrics.json`, `metrics.md`,
`groups_purity.csv` (gitignored: group ids), `agent_metrics.csv`. Plots by `eval/make_confusions.py`
→ `eval/results/confusion_<REASON>.png`, `reason_prf.png`.

| metric | definition |
|---|---|
| Per-reason P/R/F1 | For each reason family, agent code present vs human code present on the same photo. `DUPLICATE_OF:<id>` and `BURST_NOT_BEST` form one DUPLICATE family. Micro-averaged over events. |
| Reject P/R | Agent `reject` vs human `reject`. Agent `review` counts as not-reject; the review rate, and how humans labelled reviewed photos, are reported separately. |
| Cluster purity | Σ over agent groups of the largest overlap with one human group, divided by N. Inverse purity is reported alongside. |
| Cluster completeness | Fraction of human groups (size ≥ 2) whose members all fall in one agent group. |
| Duplicate retrieval P/R/MAP | imagededup-style: for each photo, ground truth = other members of its human group, retrieved = other members of its agent group. Implemented in `compute_metrics.py` (the imagededup package pulls in torch, so it is not a dependency). Same definitions as `imagededup.evaluation.evaluate(..., metric='all')`. |
| Best-of-burst | Human groups with exactly one marked best: top-1 = agent best equals it; top-2 = it is in the first two of the agent ranking. |
| Agent loop | From `trace.jsonl`: tool calls per photo and per tool, % photos escalated via `request_human_approval`, % of escalated proposals the human accepted, tool latency mean/p95, model tokens, loop-guard triggers. |
| Robustness | Same event run twice on the same flavour (`results/<eventId>.run2/`): % identical verdicts and % identical reason sets. |
| Time saved | `eval/time_study_template.md`: minutes per 1,000 frames manual vs assisted, % of frames needing a human look. |

Labels are optional: an event with `results/<eventId>/` but no photographer labels still gets every
label-independent metric (per-run agent table, run counts, group-size histogram, determinism, plots), and
every label-dependent cell reads "no photographer labels yet (n=0)".
Pivots are counted with the API's own rule set (`services/api/app/pivots.py`), so they match
`GET /runs/{id}/trace`. Escalations answered by a scripted reviewer (`human_source: scripted`) are counted
separately and never enter a human-agreement figure.

Primary labeller = the photographer with the most rows per event. Unlabelled (todo) rows are ignored.
Answer sources: S3 `trace.jsonl` keeps the source the API stamped at answer time; the team later relabelled
the two E03 mechanism-test answers in DynamoDB only. `compute_metrics.py` therefore reads a read-only export
of the RUN# items (`eval/results/raw/mirror/runs.json`) and lets `RUN#.resume_decisions` win: both E03 runs
count as `lead_test`, never as a person. Committed outputs use E-aliases and alias-rewritten run ids, and the
script refuses to write if a raw id slips through.
`compute_metrics.py` is unit-tested on a hand-worked synthetic fixture (`tests/unit/test_eval_metrics.py`).

## 3. Results

### 3.1 Label-independent (measured 2026-10-09)

`eval/results/metrics.md` (tables), `agent_metrics.csv` (one row per agent run, plus the two determinism
runs with tokens and $), `verdicts_by_event.png`, `group_sizes.png`, `determinism.md`, `dataset.md`.
Inputs: the latest `results/<eventId>/` in the work bucket for E01, E03, E07, E09 and a read-only RUN#
export.

| measure | value | source |
|---|---|---|
| Agent runs in the traces | 8 (E03 4, E07 2, E01 1, E09 1); 7 deployed (4 on AgentCore Runtime, 3 on Fargate), 1 local | metrics.md "Agent runs" |
| Tool calls / pauses / resumes | 215 / 20 / 13 | metrics.md totals |
| Vision-driven pivots (API rule set) | 34: Policy 3 14, Policy 5 20 (E09 local run alone: 20) | metrics.md |
| Escalation answers by source | 59 escalated photos: scripted 21 (E09), lead_test 8 (E03), pending 30. **0 answered by a person** | metrics.md |
| Agent active time per deployed run | 22-137 s (waiting for an answer excluded) | metrics.md "active s" |
| Verdicts, latest run per event | E01 1,784 / 455 / 203 of 2,442; E03 633 / 22 / 9; E07 301 / 624 / 75; E09 609 / 39 / 13 (keep / reject / review) | metrics.md, verdicts_by_event.png |
| Worker groups | E01 417, E03 27, E07 171, E09 50; largest 14 / 4 / 75 / 3 | metrics.md, group_sizes.png |
| Agent determinism (E09 run twice) | **83.3 % identical final verdicts** (20 of 24 agent-loop photos); reason sets 58.3 %; proposals on photos escalated in both 81.0 %; tool sequence differs | determinism.md |
| Tokens and $ per event (E09, 12 work items) | ≈ 1.17 M input / ≈ 1.2 k output tokens per run; **$2.33 / $2.36** at Anthropic list price for claude-sonnet-5, no prompt caching | determinism.md |

Notes on the determinism run: both repetitions ran locally against the work bucket with DynamoDB in
LOCAL_MODE (no writes to the live tables or bucket), on the same worker output and code, with a scripted
approve-as-proposed reviewer. The 4 differing photos are all escalated ones: a different proposal, or one run
escalating a photo the other settled. The endpoint reported 0 output tokens on 119 of 180 model cycles
(tool-only turns), so output tokens and $ are lower bounds; input is ≈ 99.5 % of the list-price cost, and
prompt caching (not enabled) is the obvious lever.

### 3.2 Before labelling: what can and cannot be quoted

No escalation has been answered by a person on any event: E09's 21 answers came from the scripted
reviewer, E03's 8 from the team's mechanism tests, the rest are pending. Human agreement is "no
photographer labels yet (n=0)" everywhere and must not be quoted. Team labels, once they exist, are quoted
only as "team labels (not photographer)".

### 3.3 Label-dependent (fills in when a photographer labels)

```
.venv/bin/python eval/compute_metrics.py          # metrics.md/json, agent_metrics.csv, all PNGs
```

Then paste the tables from `eval/results/metrics.md` and embed `reason_prf.png` plus the REJECT,
BLUR_FOCUS, EYES_CLOSED and DUPLICATE confusion matrices (they already render with n=0 cells).

| headline | value |
|---|---|
| Reject precision / recall | no photographer labels yet (n=0) |
| Review rate (latest runs, all photos) | E01 8.3 %, E03 1.4 %, E07 7.5 %, E09 2.0 % (label-independent) |
| Cluster purity / completeness | no photographer labels yet (n=0) |
| Best-of-burst top-1 / top-2 | no photographer labels yet (n=0) |
| κ between labellers | no photographer labels yet (n=0) |
| n positives per reason | no photographer labels yet (n=0) |
| Photos escalated / human agreed | 59 escalated across 8 runs / no photographer answers yet (n=0) |
| Re-run identical verdicts | 83.3 % (E09, 24 agent-loop photos, scripted approvals) |
| Minutes per 1,000 frames, manual → assisted | TBD (time study) |

## 4. Performance and cost

The benchmark compares open-source OpenCV 5.0.0 builds on AWS Graviton4 (c8g), on the same 5,000 event
photos with the same per-stage method: the carotene-only PyPI wheel against the PyPI wheel that adds Arm
KleidiCV 26.03, plus our own source builds. KleidiCV is the open-source (Apache-2.0) Arm kernel library
that COOL (Cloud-Optimized OpenCV, an AWS Marketplace product) is built on. We do not run COOL, we did
not subscribe to it, and we claim nothing about its performance. Method, numbers and caveats:
`docs/COOL_BENCHMARK.md` §1-§3; raw tables: `bench/results/2026-10-08/results.md`.

## 5. Failure cases

At least 15 real examples, blurred, in `docs/FAILURE_CASES.md` , drawn from labeller notes
(`unsure`, `artistic`, intentional eyes closed) and from the largest-disagreement cells of the
confusion matrices.

## 6. Limitations

At minimum:

- No photographer labels yet: every accuracy figure is n=0. Team labels, if added, are not ground truth.
- No escalation has a person's answer; scripted and team-test answers are excluded from agreement.
- Determinism is measured on one event (E09, 24 agent-loop photos) with scripted approvals; a different
  answer at a pause can change what the agent does next, so real reviewers may widen the spread.
- Token counts come from the Anthropic-compatible endpoint the runs used, which reports 0 output tokens on tool-only turns; $ uses
  Anthropic list prices, not the endpoint's bill.
- Model tokens are not written to `trace.jsonl`, so deployed runs have no token figure; only the local
  determinism runs do.
- E03's `decisions.jsonl` was overwritten by later worker-only runs, so the earlier agent runs' decisions are
  only in DynamoDB; their trace-level metrics stand.
- 4 events, 3 categories (ceremony, engagement, birthday); single-photographer time study; any reason with
  support < 30 has a noisy F1.

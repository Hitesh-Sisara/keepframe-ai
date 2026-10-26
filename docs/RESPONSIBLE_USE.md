# Responsible use

What KeepFrame does with photos, faces and decisions, and what it will never do. The competition rules let judges reject entries that use media without rights or consent, create privacy or surveillance risk without safeguards, or misrepresent "capabilities, results, benchmarks, or the role of human review". This document is how we meet that bar.

## Consent and data basis

- KeepFrame processes only events that photographers upload themselves (an event shoot under `raw/<eventId>/` in the work bucket). The analysis is a quality check on the photographer's own deliverables.
- The evaluation and benchmarks use real event photos, used with permission, read-only.
- Uploaded photos are **read-only** for KeepFrame: nothing is modified, moved or deleted. The optional source sync reads with GET/HEAD/LIST only (`docs/SECURITY_REVIEW.md` #10). The key it was run with is not yet proven read-only at the source store (review #2); a dedicated object-read-only key for the four buckets, expiring after judging, has been requested. Any write-back would be a separate change that the user approves explicitly later.
- Results live only in this project's own S3 bucket and DynamoDB tables, in a dedicated AWS account, under the `keepframe-*` prefix.

## Storage and retention

| Data | Where | Protection | Retention |
|---|---|---|---|
| Processed images, originals, 1024-px proxies (`raw/`, `originals/`, `proxy/`) | S3 work bucket | Private, Block Public Access on, SSE-S3, TLS only, CloudTrail data events | **Expire after 7 days** (S3 lifecycle) |
| Benchmark copy: 5,000 real processed images (`bench/5k/`) | S3 work bucket | Same | **Expire after 30 days** (S3 lifecycle since 2026-10-09; keeps benchmark reruns possible until about 2026-10-25) |
| Contact sheets, enhancement previews, results incl. every per-photo verdict (`sheets/`, `preview/`, `results/<eventId>/<runId>/decisions.jsonl`) | S3 work bucket | Same | `sheets/` (contact sheets, faces): **expire after 7 days** (S3 lifecycle since 2026-10-09). `preview/` and `results/`: kept for the competition window with **no expiry rule**; deleted by hand afterwards. `make destroy` retains the bucket (`docs/DEPLOY.md`) |
| Run records, approvals, agent/human decision overrides (`keepframe-decisions`) | DynamoDB | IAM-scoped, PITR | Kept for the competition window, then deleted by hand (`make destroy` retains the table) |
| Agent traces (`keepframe-traces`) | DynamoDB + `results/<eventId>/<runId>/trace.jsonl` | IAM-scoped | **TTL 90 days** on DynamoDB items |

- The UI never gets a public object URL. Every thumbnail is a **presigned GET valid for 15 minutes or less**, issued by the authenticated API.
- Every API route needs a credential. The judge token (stored in Secrets Manager, never in the repo) is **read-only**: no judge route can change a decision.

## What is sent to the LLM endpoint

The agent's planner is Claude on Amazon Bedrock by default, so requests stay in the project's AWS account. The agent itself runs in the same account on Amazon Bedrock AgentCore Runtime. Optionally, any Anthropic-compatible endpoint can be configured instead; that is then the only place data leaves the account. The evaluation runs used that optional setting. What the model receives, either way:

- **Tool results:** JSON scores, labels, reason codes, and synthetic photo and group ids from the manifest (`p_…`, `g_…`).
- **Contact sheets only when a visual tie-break is needed:** 512-px contact sheets built with OpenCV, **at most 10 per event**. **Full-resolution images and originals are never sent.**
- **No customer identifiers:** no photographer, couple or guest names, event names, user ids, channel ids or brand ids, and no EXIF beyond the numeric fields the policies use (for example exposure time).

Bedrock calls use the agent's IAM role, with no API key. For the optional provider the API key lives in Secrets Manager (`keepframe/anthropic`) and is read only by the agent's execution role: the AgentCore runtime fetches it once at startup, so the value never appears in runtime configuration, env files or logs. Each trace line records which model was used.

## Face data

- Face boxes, landmarks, eye state and face quality are computed **transiently** in memory from the pixels.
- We store **only scores and bounding boxes**. **No face embeddings are stored**: not in DynamoDB, not in S3, not in logs. SFace features are only computed if optional identity grouping is switched on, and even then they stay in memory for that run.
- When the manifest already carries face boxes, we reuse them to skip detection. We never compute or store face embeddings.
- There is no identification, no search by face, and no linking of faces across events.

## Human control

- **No auto-delete.** No delete tool exists. `write_decision` accepts only `keep`, `reject` or `review`. A reject is a flag on a copy, never an action on the photographer's photos.
- Every reject is **reversible** in the UI. Overrides are recorded with `decided_by=human` and a note.
- Uncertain cases go to a human: low-confidence rejects, close rankings, closed eyes in group photos, possible artistic intent, and any group where every frame fails. The agent pauses until the human answers.
- The agent's **confidence and reason** are always displayed next to its verdict.
- Hard caps stop runaway loops: tool calls per group and per event, wall-clock time, and retries. Hitting a cap escalates to a human. It never retries forever.

## No misrepresentation

- The report and video separate **measured** results (each with its evidence file under `eval/results/` or `bench/results/`) from **estimates** (labelled as estimates, with the assumptions stated).
- We say exactly which steps the agent decides alone and which a human confirmed. Demo decisions show `decided_by` on screen.
- The Arm benchmark reports what was measured, with method and baselines, and is not rounded up. It names the exact OpenCV build per row (carotene-HAL wheel, KleidiCV wheel or source build) and its compiler, and it reports negative results as measured: KleidiCV did not speed up our pipeline, so the product ships the carotene build. We did not run COOL and claim nothing about it.
- Any x86 figure is labelled "x86 baseline, not part of the product".

## What appears publicly

The video, report, Devpost page (publicly visible, including during public voting), screenshots and the repository contain only:

- **team-owned or consented events**, or images whose faces have been **blurred** with `blur_faces_for_publication` (`cv2.GaussianBlur` on YuNet boxes) before export;
- **no** customer names, event names, user ids or channel ids;
- **synthetic ids** in trace excerpts (`run_…`, `p_…`, `g_…`).

Test fixtures are synthetic images generated at test time, plus five synthetic (machine-generated) face images (ODC-By 1.0, `tests/fixtures/faces/LICENCES.md`). No customer photo is ever committed to git. The local demo uses a synthetic event (`keepframe.worker.synth`); no team-owned photo event is bundled yet.

## Open findings from the security review

`docs/SECURITY_REVIEW.md` (2026-10-09) found three High items. Their status as of 2026-10-09 08:15 UTC:

- **Real ids reached the judge view (#1). Fixed live 2026-10-09 08:05 UTC.** The judge API now returns only aliases (`ev_NN`, `ph_<HMAC>`, `g_ev_NN_<n>`), scrubs every judge response including trace arguments and errors, refuses raw source ids for the judge token, and streams images through signed `/img/…` routes instead of presigned S3 links. Verified with the judge token after the release: 0 uuid-shaped strings in `/events`, groups, `/runs` and a 500-line trace; a raw-id path returns 404; a contact sheet streams as `image/jpeg` with no redirect. Residual: anyone who already holds a real id pair may still reach the image wherever its owner publishes it, which is outside this project.
- **Source key scope unproven (#2).** See "Consent and data basis" above. Our code only reads; the key's own permissions are unverified until the dedicated read-only key replaces it.
- **Retention wider than documented (#3).** **Fixed live 2026-10-09** for the pixels the review named: `sheets/` now expires after 7 days and `bench/5k/` after 30 (table above). `preview/` (enhancement previews of keepers) still has no expiry rule and is deleted by hand after judging.

## Secrets

The repository contains environment variable **names** only. Values live in AWS Secrets Manager / SSM, and locally in an untracked file outside the repo. `.gitignore` excludes `.env*`, keys and data. A gitleaks scan runs in pre-commit and CI.

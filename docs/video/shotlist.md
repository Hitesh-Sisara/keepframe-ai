# Video shooting script (5:00 or less, unlisted YouTube)

The rules require the video to show **the team, the application working, its architecture, and its principal results**. Hard limit 5:00. **Planned length: 290 s (4:50)**, leaving 10 s of slack. Shot seconds are budgets: if a shot runs long, cut voice-over, not the slack.

Recording rules:

- **Real data on screen: none by default.** The only real-data screens allowed are listed in the next table, each with its condition. Everything else is recorded on the synthetic event (`python -m keepframe.worker.synth`, or `ev_synth_infra` on AWS) or the scripted agent scenario (`ev_demo`). There is no team-owned photo event yet; if one is shot and consented, it may replace the synthetic event and this table is updated first.
- **Contact sheets and thumbnails from real events: never unblurred.** A real contact sheet or thumbnail appears only as an export passed through `blur_faces_for_publication` (`cv2.GaussianBlur` on YuNet boxes). The live UI does not blur, so real-event UI pages are recorded cropped to text panels.
- Show no customer names, event names, user ids, channel ids or source event UUIDs. Before recording, check the browser tab title, URL bar, terminal prompt, Step Functions execution input, CloudWatch log lines and AWS console breadcrumbs.
- Record at 1920×1080, browser zoom 110%, light theme. Hide bookmarks and extensions. Use a fresh AWS console session showing the account alias only, region ap-south-1.
- The real 661-photo run is shown only with the caption **"Approvals were scripted in this run"**. Moment 1 is captioned **"Synthetic test scenario"**. Remove either caption only once a real labeller pass exists.
- Every number spoken or shown comes from `eval/results/metrics.md` or `bench/results/<date>/results.md`. If a number is not measured yet, cut the line. Estimates are spoken as "we estimate".
- Voice-over is about 140 words/min (2.3 words/s). Word counts per shot are in brackets; each fits inside its seconds.
- The judge token never appears on screen.

Allowed real-data screens (everything not listed here is synthetic):

| Shot | Screen | Condition |
|---|---|---|
| 6, 7 | Group page of the real 661-photo run (E09) | Crop to the trace panel and score table: text and synthetic ids only, no thumbnails, no contact sheet. Caption "Approvals were scripted in this run" |
| 12 | CloudWatch dashboard KeepFrame | Aggregate metrics only. Hide any widget or legend that shows an event id |
| 13 | CloudWatch log lines | Only the `5.0.0 … aarch64` probe line and the `opencv_build` line (no ids in either). Crop everything else |
| 14 | UI Bench page | Aggregate timings, no images |
| 16 | Failure cards | Text card plus a thumbnail only if it is a `blur_faces_for_publication` export; otherwise text only |

Type: **Cam** = team on camera, **Screen** = screen recording, **Slide** = static slide or full-screen image.

| # | Seconds (cum.) | Type | Screen (exact) | Voice-over |
|---|---|---|---|---|
| 1 | 10 (0:10) | Cam | Team on camera, lower-third KeepFrame, member names and roles | "Hi, we're KeepFrame. I'm Hitesh, and I build software for event photographers. This is KeepFrame." [17] |
| 2 | 15 (0:25) | Slide | "4,000 frames → about 3 hours of culling (estimate)" over a collage of **synthetic** frames | "An event photographer shoots thousands of frames, then spends hours throwing out blurred shots, closed eyes and duplicate bursts. They already upload those photos to the cloud. We moved culling to where the pixels are." [32] |
| 3 | 12 (0:37) | Screen | Review UI **Runs** → **New run** → synthetic event → **Start** | "In the review UI I pick an event and start a run. Nothing is ever deleted. The agent only flags." [20] |
| 4 | 18 (0:55) | Screen | Step Functions `CullEvent` execution on the synthetic event turning green: Ingest → ValidateManifest → CullWorker → RunAgent (invoke → wait → status) | "The photographer's upload lands in a private S3 bucket. An OpenCV 5 worker on Fargate ARM64 hashes, clusters and scores every frame, then hands off to the agent." [29] |
| 5 | 22 (1:17) | Screen | Group detail, **synthetic scenario** (caption "Synthetic test scenario"): `score_sharpness roi=full → borderline`, then `roi=faces → sharp` | "First, a synthetic test case, because our real galleries arrive pre-culled. Full-frame sharpness comes back borderline, but there are faces, so the agent re-scores the face region instead of rejecting a bokeh portrait." [33] |
| 6 | 18 (1:35) | Screen | **Real 661-photo run**, trace panel only (caption "Real event, approvals were scripted"): `score_eyes_open → f1 closed, main subject open`, then `pick_best_in_cluster` | "On a real event, eye-state finds a secondary face with closed eyes. The main subject is fine, so instead of rejecting the frame it compares the whole burst." [28] |
| 7 | 15 (1:50) | Screen | Same trace panel: `margin < 0.05` → `request_human_approval` → `interrupt_resumed` (caption stays) | "The top two frames are within three hundredths. Too close to call, so the agent pauses and asks the photographer." [20] |
| 8 | 18 (2:08) | Screen | **Live, synthetic scenario:** Approvals page with the pending card and its synthetic contact sheet; a team member clicks **Override → keep p_…**, note "better expression", **Submit**; trace shows `interrupt_resumed` then `write_decision decided_by=human` | "Here I answer one live. I pick the frame with the better expression. The agent resumes on AgentCore and records it as a human decision." [25] |
| 9 | 18 (2:26) | Screen | Synthetic event overview: keep / reject / review chips, reason chips (`BLUR_MOTION`, `EYES_CLOSED`, `BURST_NOT_BEST`), confidence on hover | "Every verdict carries a reason and a confidence. Rejects are reversible flags, and uncertain frames wait for review." [18] |
| 10 | 12 (2:38) | Screen | Synthetic keeper → **Preview** toggle: original vs CLAHE + gray-world WB | "For keepers it can generate an OpenCV enhancement preview. The original is never touched." [14] |
| 11 | 30 (3:08) | Slide | Full-screen `docs/architecture.png`; cursor traces photographer upload → S3 → Step Functions → Fargate ARM64 worker → Lambda invoke/status → AgentCore Runtime agent → Claude on Bedrock → DynamoDB → API Gateway + Lambda → UI host (as deployed; see infra-ui) | "Everything runs on AWS Graviton in Mumbai. Fargate ARM64 for the worker, a Strands agent on Amazon Bedrock AgentCore Runtime that calls Claude on Amazon Bedrock, DynamoDB for decisions and traces, and Lambda arm64 behind the UI. The uploaded photos are read-only." [44] |
| 12 | 12 (3:20) | Screen | CloudWatch dashboard KeepFrame: throughput, p50/p95 per stage, escalation %, verdict mix | "CloudWatch shows per-stage latency, escalation rate and verdict mix for every run." [12] |
| 13 | 18 (3:38) | Screen | CloudWatch `/keepframe/probe` and `/keepframe/worker`: `5.0.0 … aarch64` and the `opencv_build` line with contrib modules and `opencv_hal` | "Every container proves what it runs: OpenCV 5.0.0 on aarch64. We did not run COOL, a paid Marketplace build, and claim nothing about it." [24] |
| 14 | 25 (4:03) | Screen | UI **Bench**: c8g.xlarge vs c7g.xlarge, per-stage p50/p95, images/s, $/1k; then the 2026-10-08 carotene vs KleidiCV rows | "On the same 5,000 real photos, Graviton4 runs the OpenCV pipeline at 28.4 images a second, 1.12 times Graviton3, for about a tenth of a cent per thousand images. Arm's KleidiCV kernels don't speed up our operators, so we ship the faster build." [43] |
| 15 | 22 (4:25) | Slide | Label-independent results from `eval/results/metrics.md` and `determinism.md`: 4,767 photos / 4 events; 215 tool calls, 34 vision-driven pivots; 83.3 % identical verdicts on an E09 re-run; ≈ $2.33–2.36 per event at list price (lower bound). Footer: "No photographer labels yet (n=0): precision, recall and time saved are not measured." Replace with label metrics only once `metrics.md` has photographer rows | "On four real events, 4,767 photos, the agent made 215 tool calls and changed its plan from a vision result 34 times. Run twice, 83 percent of its final verdicts matched. That cost about two dollars thirty-five per event at list price." [42] |
| 16 | 15 (4:40) | Slide | Two failure cards from `docs/FAILURE_CASES.md` (text, or blurred export): artistic dance-floor blur; sleeping baby with closed eyes | "It still gets things wrong. Intentional motion blur and a sleeping baby look like failures, so we route those to a human." [22] |
| 17 | 10 (4:50) | Slide | "about 11,000 photographer-hours of manual culling a year for one mid-size photo service (estimate)" + repo / report links | "We estimate eleven thousand hours of culling a year for one mid-size photo service that this can shorten. Thanks for watching." [18] |

**Total: 290 s (4:50).** By type: Cam 10 s, Screen 188 s, Slide 92 s. Voice-over 443 words ≈ 3:10 of speech; the rest is on-screen action.

## Before upload

- [ ] Exported runtime 5:00 or less.
- [ ] The team appears on camera (shot 1).
- [ ] The app is shown working: shots 3–10.
- [ ] The architecture is shown: shot 11.
- [ ] Principal results are shown: shots 14–15, numbers matching the evidence files; label metrics only once photographer labels exist.
- [ ] Real data appears only in the screens allowed above. No unblurred real face, contact sheet or thumbnail; no customer name, event name, event UUID, channel id or judge token (scrub frame by frame at 0.25×).
- [ ] Every estimate is spoken as an estimate.
- [ ] Uploaded unlisted. The link opens in a private window. The link is pasted into Devpost, README.md and REPORT.md.

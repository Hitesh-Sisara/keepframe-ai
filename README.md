# KeepFrame (repo: `keepframe-ai`)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Agentic photo culling for event photographers, built on **OpenCV 5** and **AWS Graviton (Arm only)** for the OpenCV AI Competition 2026. A photographer uploads an event shoot (2k–10k frames) to S3, and KeepFrame does the following:

- groups bursts and near-duplicates;
- scores sharpness, exposure, faces and eyes with OpenCV 5;
- picks the best frame per group and explains every verdict;
- asks a human to confirm borderline cases.

The output is a keep / reject / review manifest plus a review UI. **Nothing is ever deleted.**

**Try it:** [Explore the demo](https://fnua4aumrl.execute-api.ap-south-1.amazonaws.com/ui/) (live review desk).

Team: KeepFrame: Hitesh Sisara (founder).

## Judge path (about 2 minutes)

1. **Open the live review desk:** https://fnua4aumrl.execute-api.ap-south-1.amazonaws.com/ui/ (served by the HTTP API under `/ui` until CloudFront is enabled for the account; `docs/DEPLOY.md`). The read-only judge token is in the Devpost submission's private "testing instructions" field, never in this repo. Paste it into "Access token" ([`docs/JUDGE_ACCESS.md`](docs/JUDGE_ACCESS.md)).
2. **Watch the video** (5:00 or less, unlisted): https://www.youtube.com/watch?v=lAsRbTtC7Kc.
3. **Read the report:** [`docs/REPORT.md`](docs/REPORT.md) (the PDF is built by `make report-pdf`, 12 pages or fewer, not committed).
4. **Check the architecture and the security review:** [`docs/architecture.png`](docs/architecture.png), the agent loop [`docs/agent_workflow.png`](docs/agent_workflow.png), and the security & privacy review [`docs/SECURITY_REVIEW.md`](docs/SECURITY_REVIEW.md) (open High items: [`docs/RESPONSIBLE_USE.md`](docs/RESPONSIBLE_USE.md#open-findings-from-the-security-review)). OpenCV 5 proof and model licences are [below](#opencv-5-proof).
5. **Run it locally without AWS or an LLM key:** `uv sync --frozen --all-extras && make models demo-local` ([Local demo](#local-demo)) culls a synthetic event with OpenCV 5, then runs the agent with `KEEPFRAME_LLM_PROVIDER=fake`; approvals are answered by a script and labelled `scripted`.
6. **Build, test, deploy:** `make test` locally, then `make deploy` and `make demo EVENT=<eventId>` on AWS ([Deploy, test, demo](#deploy-test-demo)). A live screen-share is available on request.

More evidence: evaluation [`docs/EVALUATION.md`](docs/EVALUATION.md), [`eval/results/`](eval/results/) · failure cases [`docs/FAILURE_CASES.md`](docs/FAILURE_CASES.md) · Arm benchmark [`docs/COOL_BENCHMARK.md`](docs/COOL_BENCHMARK.md) (file name is historical; COOL is not used), [`bench/results/`](bench/results/) · responsible use [`docs/RESPONSIBLE_USE.md`](docs/RESPONSIBLE_USE.md).

![Architecture](docs/architecture.png)

## Deploy, test, demo

Verified from a fresh clone on x86_64 Linux (2026-10-08). Tool versions below are the ones CI and that run used.

Prerequisites for the local targets (`test`, `build`, `report-pdf`, `synth`, local demo, UI build):

- Python 3.12 and `uv` 0.11.16 or newer (`uv sync` installs the pinned Python).
- Node 22 (`cdk/package.json` requires `>=22 <23`) and npm 10.
- Docker with buildx and a builder that can produce `linux/arm64`. On an x86 host that means QEMU binfmt (`docker run --privileged --rm tonistiigi/binfmt --install arm64`) and a `docker-container` builder named `arm64builder` (`docker buildx create --name arm64builder --driver docker-container --platform linux/arm64,linux/amd64`), or pass `BUILDER=<name>`. There is no x86 image target. Under QEMU a full `make build` took 20 minutes, most of it the agent image. It also retags the local `:dev` images.
- For `make report-pdf`: either `pandoc` plus `wkhtmltopdf`, or Google Chrome/Chromium on `PATH` (the script then runs `npx md-to-pdf@5`, which needs network access).
- For `make secrets`: `gitleaks` 8.30.x on `PATH`.

The cloud targets (`deploy`, `demo`, `bench`, `destroy`, `probe`) also need:

- AWS admin credentials in your shell, region `ap-south-1`. The Makefile derives `ACCOUNT` from `aws sts get-caller-identity`. Without credentials it is empty: `make synth` then stops and asks for `ACCOUNT=000000000000`, and `make destroy` refuses to run.
- Model access to Claude on Amazon Bedrock in the region (default provider; the India inference profile `in.anthropic.claude-sonnet-5`), or optionally any Anthropic-compatible endpoint with its key in Secrets Manager `keepframe/anthropic`. See [Configure the LLM](#configure-the-llm).
- Amazon Bedrock AgentCore Runtime available in the region (hosts the agent).
- c8g vCPU quota of 16 or more (only for `make bench`).

Local, no AWS account needed:

```bash
uv sync --frozen --all-extras  # pinned Python deps (uv.lock); make test/lint also run with --all-extras
make models                    # sha256-checked ONNX/YAML models from config/models.lock into models/ (idempotent, ~47 MB)
make test                      # runs make models and npm ci for cdk/ first, then pytest + CDK assertion tests, ~2 min
make synth ACCOUNT=000000000000   # cdk synth + cdk-nag; the placeholder account is how CI synthesises without credentials
make build                     # 4 arm64 images (worker, sync, agent, api) via buildx --builder arm64builder
make report-pdf                # docs/REPORT.md -> docs/REPORT.pdf (fails above 12 pages; the PDF is not committed)
(cd ui && npm ci && npm run build)   # static review UI -> ui/out (set NEXT_PUBLIC_API_BASE to point it at an API)
```

### Local demo

No AWS account, no LLM key, no customer data (LOCAL_MODE: no `WORK_BUCKET`, files under `CULL_LOCAL_ROOT`; synthetic images, deterministic fake model, no network). `make demo-local` runs the three steps below with `CULL_LOCAL_ROOT=data/local/demo` (gitignored, recreated each run; `DEMO_ROOT=<dir>` to change it) and prints where the results are. By hand, after `uv sync --frozen --all-extras` and `make models`:

```bash
export CULL_LOCAL_ROOT=/tmp/cull-local OPENCV_FORCE_DNN_ENGINE=3 KEEPFRAME_LLM_PROVIDER=fake
uv run --frozen python -m keepframe.worker.synth --root "$CULL_LOCAL_ROOT" --event-id ev_synth   # 12 synthetic photos
uv run --frozen python -m keepframe.worker.run_event --event-id ev_synth --local-root "$CULL_LOCAL_ROOT"
CULL_TOOLS=stub uv run --frozen python -m keepframe.agent.run --demo --run-id run_demo --approve-all   # scripted agent loop with one human pause, auto-approved
```

Results land in `$CULL_LOCAL_ROOT/results/<eventId>/<runId>/` (plus an unversioned latest copy in `results/<eventId>/`) and `$CULL_LOCAL_ROOT/dynamodb/`. The worker culls 12 synthetic frames in 2 groups. The agent's scripted scenario (`results/ev_demo/run_demo/trace.jsonl`) shows the vision-driven pivots and one approval pause, answered by the script and recorded as `decided_by=scripted`. `agent.run --event-id ev_synth` with real tools decides nothing on the synthetic event (its work queue is empty), so `--demo` is the one that shows the loop. Verified from fresh clones on 2026-10-08 (repro, docs).

On AWS (credentials loaded):

```bash
make deploy                    # all stacks (ap-south-1), arm64 images, API, UI; agent on AgentCore Runtime (ARM64), the only agent host
make demo EVENT=<eventId>      # StartExecution on the CullEvent state machine; the event's photos must already be in s3://<work bucket>/raw/<eventId>/
make bench                     # OpenCV 5 Arm benchmark on a self-terminating c8g host (stock OpenCV 5, no COOL)
make destroy CONFIRM=yes       # cdk destroy --all --force; refuses without CONFIRM=yes or without credentials. Work bucket and tables are retained, see docs/DEPLOY.md
```

There is no bundled `samples/` event and no `EVENT=sample` shortcut: `make demo` needs an event whose photos a photographer has uploaded to `raw/<eventId>/` in the work bucket (the `Ingest` step builds the manifest when none is there). Use the local demo above to run the pipeline without AWS.

## Configure the LLM

The agent's planner and reason-writer are Claude models. The default provider is **Claude on Amazon Bedrock** (Strands `BedrockModel`, IAM credentials, no API key). Any Anthropic-compatible endpoint is an optional alternative (official `anthropic` SDK via Strands `AnthropicModel`). Only environment variable **names** are shown here. CDK picks the provider with context `llmProvider` (default `bedrock`), `bedrockModelId` and `bedrockReasonsModelId`.

```bash
# KEEPFRAME_LLM_PROVIDER unset (or "bedrock"): Claude on Amazon Bedrock, credentials from the IAM role
export KEEPFRAME_BEDROCK_MODEL_ID=in.anthropic.claude-sonnet-5                          # default planner (India inference profile)
export KEEPFRAME_BEDROCK_REASONS_MODEL_ID=in.anthropic.claude-haiku-4-5-20251001-v1:0   # default reason-writer
```

Optional: any Anthropic-compatible endpoint. On AgentCore the runtime reads the key once at startup from Secrets Manager (`CULL_ANTHROPIC_SECRET_ID=keepframe/anthropic`); locally it comes from your untracked env file.

```bash
export KEEPFRAME_LLM_PROVIDER=anthropic
export ANTHROPIC_BASE_URL=...        # endpoint URL
export ANTHROPIC_API_KEY=...         # from Secrets Manager / untracked env file, never committed
export CULL_PLANNER_MODEL=claude-sonnet-5      # default
export CULL_REASONS_MODEL=claude-haiku-4.5     # default
```

`KEEPFRAME_LLM_PROVIDER=fake` selects a deterministic scripted model for tests (`CULL_MODEL_PROVIDER` is still accepted as a legacy alias).

With the default `bedrock` provider every model call stays inside the AWS account (the AgentCore execution role may call `bedrock:InvokeModel` / `InvokeModelWithResponseStream` on the two configured inference profiles only). The optional `anthropic` provider is the one setting that sends requests outside AWS.

The model receives tool results and, when a tie-break needs it, 512-px contact sheets (at most 10 per event). It never receives full images or customer identifiers.

Secrets: this repo holds **environment variable names only**. Values live in AWS Secrets Manager / SSM, and locally in an untracked file outside the repo. A gitleaks scan runs in pre-commit and CI.

Uploaded photos are **read-only** for this project: nothing under `raw/`, `exif/` or `originals/` is modified or deleted. Results live only in this project's S3 bucket and DynamoDB tables.

## OpenCV 5 proof

- Pinned to `opencv-contrib-python-headless==5.0.0.93` in [`pyproject.toml`](pyproject.toml). `opencv-python` is never installed alongside it.
- The image build fails unless `cv2.__version__` starts with `5.` and the contrib modules `img_hash`, `quality`, `saliency` and `xphoto` are present.
- Every process logs `cv2.__version__`, `cv2.__file__` and the head of `cv2.getBuildInformation()` to CloudWatch at start. Every run records `opencv_version` and `cv2_file` in its trace header and `summary.json`.
- OpenCV 5 APIs used: `cv2.img_hash`, `cv2.quality` (BRISQUE, SSIM), `cv2.saliency`, `cv2.FaceDetectorYN` (YuNet), `cv2.FaceRecognizerSF` (SFace alignment), `cv2.dnn` (new engine, ONNX), `cv2.Laplacian`/`cv2.Sobel`, `cv2.calcHist`, `cv2.createCLAHE`, `cv2.xphoto` white balance, `cv2.fastNlMeansDenoisingColored`, ORB + `cv2.estimateAffinePartial2D` (USAC).
- Arm build: the image installs the SHA-256-pinned `manylinux_2_28` aarch64 wheel (`Custom HAL: carotene`) with `scripts/install_opencv_wheel.sh`, which asserts the HAL at build time. Every run records `opencv_hal`.
- We measured Arm KleidiCV (Apache-2.0; the open-source kernels COOL is built on) and it does not speed up this pipeline. With the compiler held constant it is 0.998× end to end, and every stage is within ±1.1% (CLAHE 1.011×). The product worker is about 6% slower on the KleidiCV wheel, with identical verdicts. KleidiCV is active (7× on INTER_LINEAR resize in a single-op probe), but the operators we use are not on its accelerated path. So we ship the faster carotene build. Details: [`docs/REPORT.md`](docs/REPORT.md) §6, [`bench/results/2026-10-08/results.md`](bench/results/2026-10-08/results.md).
- COOL (Cloud-Optimized OpenCV): we did not run it and claim nothing about it. It carries an AWS Marketplace software fee, and this project spends AWS credits only.

Logged output from the deployed stack (CloudWatch, 2026-10-05; details in [`docs/DEPLOY.md`](docs/DEPLOY.md)):

```
# ECS task cull-probe on Fargate ARM64 (cpuArchitecture=arm64)
5.0.0 /usr/local/lib/python3.12/site-packages/cv2/__init__.py aarch64
# cull-worker start-up line (excerpt)
{"event":"opencv_build","opencv_version":"5.0.0","cv2_file":"/usr/local/lib/python3.12/site-packages/cv2/__init__.py","contrib":["img_hash","quality","saliency","xphoto"],"dnn_engine":"3"}
```

## Models and licences

| Model | Use | Source | Licence |
|---|---|---|---|
| YuNet `face_detection_yunet_2023mar.onnx` | face detection (`cv2.FaceDetectorYN`) | opencv_zoo | MIT |
| SFace `face_recognition_sface_2021dec.onnx` | face alignment for quality crops (`cv2.FaceRecognizerSF`) | opencv_zoo | Apache-2.0 |
| eDifFIQA(T) | face image quality (`cv2.dnn`) | opencv_zoo | CC-BY-4.0 |
| BRISQUE model / range YAML | no-reference quality (`cv2.quality`) | opencv_contrib | Apache-2.0 |
| OCEC-M `ocec_m.onnx` | eye state, open / closed (`cv2.dnn`) | PINTO0309/OCEC | MIT; trained on MichalMlodawski/closed-open-eyes (ODC-By 1.0, synthetic (machine-generated) faces) |

All model files are downloaded at image build time and checked against SHA-256 pins in [`config/models.lock`](config/models.lock). Model files are not committed. NIMA is not used (no licence-clean, verified ONNX). Attributions: [`models/README.md`](models/README.md); face test fixtures: [`tests/fixtures/faces/LICENCES.md`](tests/fixtures/faces/LICENCES.md).

## Repository layout

`keepframe/` (tools, agent, worker, store) · `services/` (API, agent runtime, sync) · `cdk/` (infrastructure, TypeScript) · `ui/` (Next.js review UI) · `bench/` · `eval/` · `config/` · `tests/` · `docs/`.

## Licence

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

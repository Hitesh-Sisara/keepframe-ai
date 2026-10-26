# KeepFrame — build, deploy, test. Every image is linux/arm64 (Graviton); x86 only as the labelled bench baseline.
# Credentials: `set -a; . ~/.config/keepframe/.env; set +a` first (outside the repo). See docs/DEPLOY.md.
SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

AWS_REGION      ?= ap-south-1
ACCOUNT         ?= $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null)
ACCOUNT         := $(strip $(ACCOUNT))
REGISTRY        := $(ACCOUNT).dkr.ecr.$(AWS_REGION).amazonaws.com
TAG             ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
BUILDER         ?= arm64builder
PLATFORM        := linux/arm64
BUILDX          := docker buildx build --builder $(BUILDER) --platform $(PLATFORM)
# true when the hand-made day-1 bucket/tables/ECR repos exist (this account); false in a fresh account
ADOPT           ?= $(shell aws s3api head-bucket --bucket keepframe-work-$(ACCOUNT)-$(AWS_REGION) >/dev/null 2>&1 && echo true || echo false)
CDK             := cd cdk && npx cdk
# Source tree for image assets (API Lambda, UI). Default: this checkout. Releases pass SRC=<git-archive dir>.
SRC             ?= $(CURDIR)
CDK_CTX         := -c adoptExisting=$(ADOPT) -c imageTag=$(TAG) -c srcRoot=$(abspath $(SRC))
EVENT           ?=
CONFIRM         ?=
# Model files from config/models.lock (sha256-checked). The tests and the worker read $$CULL_MODELS_DIR.
MODELS_DIR      ?= models
# make demo-local writes here (gitignored data/). Wiped and recreated on each run.
DEMO_ROOT       ?= data/local/demo
DATE            ?= $(shell date -u +%Y-%m-%d)

export AWS_REGION
export CDK_DEFAULT_REGION := $(AWS_REGION)
# Only when known: an empty export would override a CDK_DEFAULT_ACCOUNT already in the environment.
ifneq ($(ACCOUNT),)
export CDK_DEFAULT_ACCOUNT := $(ACCOUNT)
endif

.PHONY: help
help: ## List targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-20s %s\n", $$1, $$2}'

# ---------------------------------------------------------------- dependencies
.PHONY: requirements
requirements: ## Export requirements.txt from uv.lock, hash-pinned
	uv export --frozen --no-dev --no-emit-project --no-header -o requirements.txt >/dev/null

cdk/node_modules: cdk/package-lock.json
	cd cdk && npm ci --no-audit --no-fund
	@touch $@

.PHONY: models
models: ## Download + sha256-verify config/models.lock into MODELS_DIR=models/ (idempotent, ~47 MB)
	uv run --frozen python scripts/fetch_models.py --lock config/models.lock --out $(MODELS_DIR)

# ---------------------------------------------------------------- images (arm64 only)
.PHONY: build build-worker build-sync build-agent build-api
build: build-worker build-sync build-agent build-api ## Build all arm64 images (buildx builder BUILDER=arm64builder, linux/arm64; QEMU binfmt on x86)

build-worker: requirements ## Stock worker image (OpenCV 5 wheel), asserts cv2 5.x at build
	$(BUILDX) -t keepframe:$(TAG) -t keepframe:dev --load .

build-sync: ## optional SyncSource image (S3-compatible source -> S3, rclone)
	$(BUILDX) -f Dockerfile.sync -t keepframe-sync:$(TAG) -t keepframe-sync:dev --load .

build-agent: requirements ## Agent image (services/agent_runtime/Dockerfile.agent; worker image as fallback)
	if [[ -f services/agent_runtime/Dockerfile.agent ]]; then \
	  $(BUILDX) -f services/agent_runtime/Dockerfile.agent -t keepframe-agent:$(TAG) -t keepframe-agent:dev --load . ; \
	else docker tag keepframe:$(TAG) keepframe-agent:$(TAG) && docker tag keepframe:$(TAG) keepframe-agent:dev; fi

build-api: ## API Lambda image (services/api/Dockerfile, public.ecr.aws/lambda/python:3.12-arm64)
	$(BUILDX) -f services/api/Dockerfile -t keepframe-api:$(TAG) --load .

.PHONY: ecr-login push
ecr-login: ## docker login to this account's ECR registry
	aws ecr get-login-password --region $(AWS_REGION) | docker login --username AWS --password-stdin $(REGISTRY)

push: ecr-login ## Push worker/sync/agent images as :$(TAG) and :dev
	for img in keepframe keepframe-sync keepframe-agent; do \
	  for t in $(TAG) dev; do docker tag $$img:$(TAG) $(REGISTRY)/$$img:$$t && docker push -q $(REGISTRY)/$$img:$$t; done; done

# ---------------------------------------------------------------- release images from a git ref
REF ?= main
.PHONY: images-main
images-main: ## Build+push worker/agent/sync/api from a clean `git archive $(REF)`, tag main-<sha> (+dev), print digests
	SHA=$$(git rev-parse --short $(REF)); SRC=$$(mktemp -d); trap 'rm -rf "$$SRC"' EXIT; \
	git archive $(REF) | tar -x -C "$$SRC"; \
	aws ecr get-login-password --region $(AWS_REGION) | docker login --username AWS --password-stdin $(REGISTRY) >/dev/null; \
	build() { repo=$$1; shift; \
	  $(BUILDX) --provenance=false --sbom=false "$$@" -t $(REGISTRY)/$$repo:main-$$SHA -t $(REGISTRY)/$$repo:dev --push "$$SRC" >/tmp/images-main-$$repo.log 2>&1 \
	    || { echo "build failed: $$repo (see /tmp/images-main-$$repo.log)" >&2; exit 1; }; \
	  printf '%-20s main-%s  %s\n' $$repo $$SHA "$$(aws ecr describe-images --repository-name $$repo --image-ids imageTag=main-$$SHA --query 'imageDetails[0].imageDigest' --output text)"; }; \
	build keepframe       -f "$$SRC/Dockerfile"; \
	build keepframe-agent -f "$$SRC/services/agent_runtime/Dockerfile.agent"; \
	build keepframe-sync  -f "$$SRC/Dockerfile.sync"; \
	build keepframe-api   -f "$$SRC/services/api/Dockerfile"; \
	echo "Images tagged main-$$SHA. Roll out: make release-deploy REF=$(REF)"

.PHONY: release-deploy
release-deploy: ## Roll out images-main output: core stacks + API built from a clean `git archive $(REF)`
	SHA=$$(git rev-parse --short $(REF)); SRCDIR=$$(mktemp -d); trap 'rm -rf "$$SRCDIR"' EXIT; \
	git archive $(REF) | tar -x -C "$$SRCDIR"; \
	$(MAKE) deploy-core deploy-api TAG=main-$$SHA SRC="$$SRCDIR"

# ---------------------------------------------------------------- deploy
.PHONY: synth deploy deploy-core deploy-api ui-build ui-deploy diff destroy
synth: cdk/node_modules ## cdk synth + cdk-nag (fails on unacknowledged findings). No AWS creds: ACCOUNT=000000000000
	@[[ -n "$${CDK_DEFAULT_ACCOUNT:-}" ]] || { echo "no AWS account (no credentials?). Offline synth: make synth ACCOUNT=000000000000" >&2; exit 2; }
	$(CDK) synth --quiet $(CDK_CTX)

diff: cdk/node_modules ## cdk diff against the deployed stacks
	$(CDK) diff $(CDK_CTX)

deploy-core: cdk/node_modules ## Network, Data, Compute, AgentCore, Workflow, Observability
	$(CDK) deploy NetworkStack DataStack ComputeStack AgentCoreStack WorkflowStack ObservabilityStack $(CDK_CTX) --require-approval never

deploy-api: cdk/node_modules ## ApiStack (Lambda image from $(SRC)/services/api); CORS locked to the UI URL once it exists
	@[[ -f "$(SRC)/services/api/Dockerfile" ]] || { echo "no $(SRC)/services/api: refusing to deploy the placeholder API (pass SRC=<tree>)" >&2; exit 2; }
	# CORS takes an origin (scheme://host); the interim UI URL carries a /ui/ path, so strip it.
	CORS=$$(aws ssm get-parameter --name /keepframe/ui-url --query Parameter.Value --output text 2>/dev/null | sed -E 's#^(https?://[^/]+).*#\1#' || true); \
	$(CDK) deploy ApiStack $(CDK_CTX) -c corsOrigins="$$CORS" --require-approval never --exclusively

ui-build: ## Build ui/out with NEXT_PUBLIC_API_BASE from SSM /keepframe/api-url
	API=$$(aws ssm get-parameter --name /keepframe/api-url --query Parameter.Value --output text); \
	cd ui && npm ci --no-audit --no-fund && NEXT_PUBLIC_API_BASE="$$API" npm run build

ui-deploy: cdk/node_modules ## UiStack (S3 + CloudFront OAC) from ui/out. Destroy UiInterimStack first (both own /keepframe/ui-url)
	@if aws cloudformation describe-stacks --stack-name UiInterimStack >/dev/null 2>&1; then \
	  echo "UiInterimStack is live and owns /keepframe/ui-url: run 'make ui-destroy-interim' first (docs/DEPLOY.md)" >&2; exit 1; fi
	$(CDK) deploy UiStack $(CDK_CTX) --require-approval never --exclusively

# Interim UI path (CloudFront blocked by account verification): the export, built with basePath /ui, baked into an
# arm64 Lambda behind the existing HTTP API. Same origin as the API, so no CORS step. See docs/DEPLOY.md.
.PHONY: ui-build-interim ui-deploy-interim ui-destroy-interim
ui-build-interim: ## Build ui/out-interim: basePath /ui, NEXT_PUBLIC_API_BASE from SSM /keepframe/api-url
	API=$$(aws ssm get-parameter --name /keepframe/api-url --query Parameter.Value --output text); \
	cd $(SRC)/ui && npm ci --no-audit --no-fund && NEXT_PUBLIC_BASE_PATH=/ui NEXT_PUBLIC_API_BASE="$$API" npm run build \
	  && rm -rf out-interim && cp -a out out-interim && rm -rf out
	@grep -q '/ui/_next/' $(SRC)/ui/out-interim/index.html && echo "ui/out-interim built for $$(aws ssm get-parameter --name /keepframe/api-url --query Parameter.Value --output text)/ui/"

ui-deploy-interim: cdk/node_modules ## UiInterimStack: GET / , /ui, /ui/{proxy+} on the HTTP API -> keepframe-ui-static (arm64)
	@if aws cloudformation describe-stacks --stack-name UiStack >/dev/null 2>&1; then \
	  echo "UiStack (CloudFront) is live and owns /keepframe/ui-url; the interim path is not needed" >&2; exit 1; fi
	@test -f $(SRC)/ui/out-interim/index.html || { echo "run 'make ui-build-interim' first" >&2; exit 1; }
	$(CDK) deploy UiInterimStack $(CDK_CTX) --require-approval never --exclusively
	@echo "UI: $$(aws ssm get-parameter --name /keepframe/ui-url --query Parameter.Value --output text)"

ui-destroy-interim: cdk/node_modules ## Remove the interim UI routes + Lambda (before make ui-deploy)
	$(CDK) destroy UiInterimStack $(CDK_CTX) --force

deploy: deploy-core push deploy-api ui-build ui-deploy ## Full deploy: stacks (creates ECR repos), images, API, UI, CORS re-lock
	$(MAKE) deploy-api
	@echo "API: $$(aws ssm get-parameter --name /keepframe/api-url --query Parameter.Value --output text)"
	@echo "UI:  $$(aws ssm get-parameter --name /keepframe/ui-url --query Parameter.Value --output text 2>/dev/null || echo not-deployed)"

deploy-ci: cdk/node_modules ## CiStack: GitHub OIDC provider + deploy/bench roles
	$(CDK) deploy CiStack $(CDK_CTX) --require-approval never --exclusively

destroy: cdk/node_modules ## Destroy all stacks: make destroy CONFIRM=yes. Work bucket + tables are RETAINED (customer data)
	@[[ -n "$(ACCOUNT)" && "$(ACCOUNT)" != 000000000000 ]] || { echo "refusing to destroy: ACCOUNT is empty or the placeholder (load AWS credentials first, see docs/DEPLOY.md)" >&2; exit 2; }
	@[[ "$(CONFIRM)" == yes ]] || { echo "refusing to destroy every stack in $(ACCOUNT)/$(AWS_REGION) without CONFIRM=yes: make destroy CONFIRM=yes" >&2; exit 2; }
	$(CDK) destroy --all $(CDK_CTX) --force
	@echo "Retained: s3://keepframe-work-$(ACCOUNT)-$(AWS_REGION), DynamoDB keepframe-decisions/-traces (see docs/DEPLOY.md)"

# ---------------------------------------------------------------- run
.PHONY: demo demo-local probe
demo-local: ## No AWS: synthetic event -> worker -> agent --demo (fake model, stub tools, scripted approvals) in DEMO_ROOT
	scripts/demo_local.sh "$(DEMO_ROOT)"

demo: ## Start a CullEvent run on AWS: make demo EVENT=<eventId> (manifest must be in the work bucket; no AWS: make demo-local)
	@[[ -n "$(EVENT)" ]] || { echo "usage: make demo EVENT=<eventId>" >&2; exit 2; }
	if [[ -f scripts/start_run.py ]]; then uv run python scripts/start_run.py --event-id "$(EVENT)"; \
	else \
	  RUN=run_$$(date -u +%Y%m%dT%H%M%S); \
	  SM=$$(aws cloudformation describe-stacks --stack-name WorkflowStack --query "Stacks[0].Outputs[?OutputKey=='StateMachineArn'].OutputValue" --output text); \
	  aws stepfunctions start-execution --state-machine-arn "$$SM" --name "$$RUN" \
	    --input "{\"event_id\":\"$(EVENT)\",\"run_id\":\"$$RUN\"}" --query executionArn --output text; fi

probe: ## One-off Fargate ARM64 task printing cv2.__version__, cv2.__file__, machine (CloudWatch /keepframe/probe)
	SUBNET=$$(aws cloudformation describe-stacks --stack-name NetworkStack --query "Stacks[0].Outputs[?OutputKey=='PublicSubnets'].OutputValue" --output text | cut -d, -f1); \
	SG=$$(aws cloudformation describe-stacks --stack-name NetworkStack --query "Stacks[0].Outputs[?OutputKey=='EgressOnlySgId'].OutputValue" --output text); \
	T=$$(aws ecs run-task --cluster keepframe --task-definition cull-probe --launch-type FARGATE --platform-version 1.4.0 \
	  --network-configuration "awsvpcConfiguration={subnets=[$$SUBNET],securityGroups=[$$SG],assignPublicIp=ENABLED}" --query 'tasks[0].taskArn' --output text); \
	aws ecs wait tasks-stopped --cluster keepframe --tasks "$$T"; \
	aws logs get-log-events --log-group-name /keepframe/probe --log-stream-name "probe/probe/$${T##*/}" --query 'events[].message' --output text

# ---------------------------------------------------------------- quality
.PHONY: test lint secrets
test: cdk/node_modules ## pytest (all extras, models fetched first) + CDK assertion tests
	$(MAKE) --no-print-directory models || echo "WARNING: model fetch failed (offline?); model-dependent tests will SKIP, count in the summary below" >&2
	CULL_MODELS_DIR="$(abspath $(MODELS_DIR))" uv run --frozen --all-extras pytest -q -rs
	cd cdk && npm test

lint: cdk/node_modules ## ruff, mypy (strict on keepframe), tsc
	uv run --frozen --all-extras ruff check .
	uv run --frozen --all-extras mypy
	cd cdk && npx tsc --noEmit -p .

secrets: ## gitleaks over the working tree and git history
	gitleaks detect --source . --redact --config .gitleaks.toml
	gitleaks detect --no-git --source . --redact --config .gitleaks.toml

# ---------------------------------------------------------------- bench + evidence
.PHONY: bench report-pdf
bench: ## Benchmark on a self-terminating c8g.4xlarge (bench/ec2/launch_bench_host.sh), then summarise
	bench/ec2/launch_bench_host.sh --label stock --instance-type c8g.4xlarge --wait
	uv run --frozen --extra bench python bench/summarize.py --upload

report-pdf: ## Render docs/REPORT.md -> docs/REPORT.pdf (pandoc if present, else md-to-pdf via npx + headless Chrome); fails if > 12 pages
	scripts/report_pdf.sh

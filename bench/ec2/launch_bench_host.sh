#!/usr/bin/env bash
# Launch ONE self-terminating EC2 benchmark host for bench/run_benchmark.py (docs/COOL_BENCHMARK.md §1).
#
#   bench/ec2/launch_bench_host.sh --label stock-kleidicv --instance-type c8g.xlarge [--ami al2023|ecs|ami-xxxx]
#        [--rows "stock-carotene=m228 stock-kleidicv=m2014"] [--wheel m228|m2014|pypi|s3://...whl]
#        [--manifest bench/manifest_5k.jsonl] [--data-prefix bench/5k/] [--dataset-name manifest_5k]
#        [--workers N] [--reps 3] [--warmup 200] [--bucket NAME] [--max-minutes 90] [--wait]
#        [--worker-event EVENT_ID] [--worker-reps 3] [--skip-stage-bench] [--src-ref main] [--dry-run]
#   bench/ec2/launch_bench_host.sh --build-src --label kleidicv-src --instance-type c8g.4xlarge [--armpl|--no-kleidicv] --wait
#        [--cv2-wheel kleidicv|carotene]   (= --wheel m2014|m228)
#
#   Rows: each `label=wheel` row gets its own venv and runs back to back on the SAME instance (so an A/B
#   between two wheels is on identical silicon). Wheels (opencv-contrib-python-headless 5.0.0.93, cp37-abi3):
#     m228  = manylinux_2_28_aarch64  (what pip picks on glibc >= 2.28; Custom HAL: carotene only)
#     m2014 = manylinux2014_aarch64   (Custom HAL: carotene + KleidiCV 26.03)
#     pypi  = let pip resolve `opencv-contrib-python-headless==5.0.0.93` (= m228 on AL2023 arm64)
#     s3://BUCKET/key.whl[#sha256=HEX] = a wheel we built (--build-src output)
#   Each wheel file is sha256-checked against the pin below before install.
#   --label X alone is one row `X=m2014` (or `X=<--wheel>`); --cv2-wheel kleidicv|carotene = --wheel m2014|m228.
#
#   --build-src: instead of benchmarking, build the 5.0.0.93 sdist (pinned sha256) with ENABLE_CONTRIB=1
#   ENABLE_HEADLESS=1 and -DWITH_KLEIDICV=ON (plus -DWITH_ARMPL=ON with --armpl, Arm Performance Libraries
#   25.07 static libs; --no-kleidicv builds the same-toolchain carotene-only control), upload the wheel + build log + getBuildInformation() to
#   s3://$BUCKET/bench/_wheels/<run_id>/, then terminate.
#
#   --worker-event also runs bench/run_worker_bench.py per row: keepframe.worker.microbench + run_event reps
#   on that event in LOCAL_MODE (manifests/, raw/, exif/, originals/ of the event synced first). The
#   keepframe package, config/ and scripts/fetch_models.py come from `git archive <src-ref>`.
#
#   The data set is s3://$BUCKET/$DATA_PREFIX<eventId>/<file>, listed by --manifest (any JSONL with
#   event_id + processed_key). Use bench/make_synthetic_set.py + `aws s3 sync` for a synthetic set.
#
# What it does:
#   1. ensures IAM role + instance profile `keepframe-bench-host` (S3 read of the work bucket's bench
#      inputs, write to bench/results, bench/_logs and bench/_wheels) and a no-ingress security group;
#   2. tars bench/ + pyproject.toml, uploads to s3://$BUCKET/bench/_bundle/<run>.tgz;
#   3. launches with InstanceInitiatedShutdownBehavior=terminate and user-data that installs uv +
#      Python 3.12 + the row's wheel, syncs the data set to local disk, runs run_benchmark.py, uploads
#      bench/results/<date>/<label>/ and the console log, then `shutdown -h now` (=> terminate). A
#      watchdog `shutdown -h +MAX` caps cost.
#   4. with --wait, polls until the instance is terminated and downloads the results.
#
# Credentials: run after `set -a; . ~/.config/keepframe/.env; set +a`. No secrets are written to
# user-data; the instance uses its role.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
LABEL=""; ITYPE="c8g.4xlarge"; AMI_KIND="al2023"; MANIFEST=""; DSNAME=""
WORKER_EVENT=""; WORKER_REPS=3; SKIP_STAGE=0; SRC_REF="main"; ROWS=""; WHEEL="m2014"; BUILD_SRC=0; ARMPL=0; KLEIDICV=ON; DRY=0; CV2_WHEEL=""
WORKERS=""; REPS=3; WARMUP=200; MAXMIN=90; WAIT=0
BUCKET="keepframe-work-${ACCOUNT}-${REGION}"
DATA_PREFIX="bench/5k/"
UV_VERSION="0.11.16"
UV_SHA256_ARM64="8c9d0f0ee98166ae6ab198747519ba6f25db29d185bd2ae5960ecebc91a5c22a"
UV_SHA256_X86_64=""   # filled lazily from the release .sha256 when an x86 baseline is launched
PROFILE="keepframe-bench-host"
SG_NAME="keepframe-bench-egress-only"
# opencv-contrib-python-headless 5.0.0.93 on PyPI (sha256 from the PyPI JSON API, checked 2026-10-08)
PYPI_FILES="https://files.pythonhosted.org/packages"
WHL_M228="$PYPI_FILES/51/78/11f0704e94ebf92f748c789cf5d430d4459ae3649263c712d81772cc4360/opencv_contrib_python_headless-5.0.0.93-cp37-abi3-manylinux_2_28_aarch64.whl"
WHL_M228_SHA="01331ae1a65a21d81310b5584900f73c3012ab4bdcf6aa26085c1870eea45da8"
WHL_M2014="$PYPI_FILES/61/14/f12a54f7e5e8783adf905bb863ef2257e41d73d44793851d318e35ee8121/opencv_contrib_python_headless-5.0.0.93-cp37-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl"
WHL_M2014_SHA="c035e12b6078b3351ad577e90a4ff4ead74155fa5470eb8df8f5b54a8ce7727f"
SDIST="$PYPI_FILES/fd/85/da534b90d99a040fbdccc6eb2c3d1c6c6d0e1f7d54732f4ce7d2e1726b29/opencv_contrib_python_headless-5.0.0.93.tar.gz"
SDIST_SHA="6a8c34de905f59b038f6ab0475e73fb0a0f345c3582ec3aeaeb1c0912e80cddb"
# Arm Performance Libraries (free download, no login) for --build-src --armpl
ARMPL_URL="https://developer.arm.com/-/cdn-downloads/permalink/Arm-Performance-Libraries/Version_25.07/arm-performance-libraries_25.07_rpm_gcc.tar"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --label) LABEL="$2"; shift 2;;
    --instance-type) ITYPE="$2"; shift 2;;
    --ami) AMI_KIND="$2"; shift 2;;
    --manifest) MANIFEST="$2"; shift 2;;
    --dataset-name) DSNAME="$2"; shift 2;;
    --workers) WORKERS="$2"; shift 2;;
    --reps) REPS="$2"; shift 2;;
    --warmup) WARMUP="$2"; shift 2;;
    --bucket) BUCKET="$2"; shift 2;;
    --data-prefix) DATA_PREFIX="$2"; shift 2;;
    --max-minutes) MAXMIN="$2"; shift 2;;
    --wait) WAIT=1; shift;;
    --worker-event) WORKER_EVENT="$2"; shift 2;;
    --worker-reps) WORKER_REPS="$2"; shift 2;;
    --skip-stage-bench) SKIP_STAGE=1; shift;;
    --src-ref) SRC_REF="$2"; shift 2;;
    --rows) ROWS="$2"; shift 2;;
    --wheel) WHEEL="$2"; shift 2;;
    --build-src) BUILD_SRC=1; shift;;
    --armpl) ARMPL=1; shift;;
    --no-kleidicv) KLEIDICV=OFF; shift;;
    --dry-run) DRY=1; shift;;
    --cv2-wheel) CV2_WHEEL="$2"; shift 2;;
    -h|--help) sed -n '2,45p' "$0"; exit 0;;
    *) echo "unknown arg $1" >&2; exit 2;;
  esac
done
case "$CV2_WHEEL" in
  "") ;; kleidicv) WHEEL=m2014;; carotene) WHEEL=m228;;
  *) echo "--cv2-wheel kleidicv|carotene" >&2; exit 2;;
esac
[[ -n "$ROWS" ]] || ROWS="${LABEL}=${WHEEL}"
[[ -n "$LABEL" ]] || LABEL="${ROWS%%=*}"
[[ "$LABEL" =~ ^[a-z0-9][a-z0-9-]{1,40}$ ]] || { echo "--label required (kebab-case)" >&2; exit 2; }
# row list -> "label|url|sha256" lines for user-data
ROW_SPECS=""
for row in $ROWS; do
  rl="${row%%=*}"; rw="${row#*=}"
  [[ "$rl" =~ ^[a-z0-9][a-z0-9-]{1,40}$ ]] || { echo "bad row label $rl" >&2; exit 2; }
  case "$rw" in
    m228)  ROW_SPECS+="$rl|$WHL_M228|$WHL_M228_SHA"$'\n';;
    m2014) ROW_SPECS+="$rl|$WHL_M2014|$WHL_M2014_SHA"$'\n';;
    pypi)  ROW_SPECS+="$rl|pypi|-"$'\n';;
    s3://*.whl\#sha256=*) ROW_SPECS+="$rl|${rw%%#*}|${rw##*=}"$'\n';;
    *) echo "row $row: wheel must be m228|m2014|pypi|s3://...whl#sha256=HEX" >&2; exit 2;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
MANIFEST="${MANIFEST:-$REPO/bench/manifest_5k.jsonl}"
[[ -f "$MANIFEST" ]] || { echo "manifest $MANIFEST not found" >&2; exit 2; }
DSNAME="${DSNAME:-$(basename "$MANIFEST" .jsonl)}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-${LABEL}"
DATE="$(date -u +%F)"

ARCH="$(aws ec2 describe-instance-types --region "$REGION" --instance-types "$ITYPE" \
  --query 'InstanceTypes[0].ProcessorInfo.SupportedArchitectures[0]' --output text)"
case "$ARCH" in arm64) UVARCH=aarch64; SSM_ARCH=arm64;; x86_64) UVARCH=x86_64; SSM_ARCH=x86_64;; *) echo "arch $ARCH?" >&2; exit 1;; esac
if [[ "$ARCH" == x86_64 && "$LABEL" != x86-* ]]; then
  echo "x86 instance types are only allowed for labels starting with x86- (Arm only; x86 is the labelled baseline)" >&2; exit 2
fi

case "$AMI_KIND" in
  al2023) AMI="$(aws ssm get-parameter --region "$REGION" --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${SSM_ARCH}" --query Parameter.Value --output text)";;
  ecs)    AMI="$(aws ssm get-parameter --region "$REGION" --name "/aws/service/ecs/optimized-ami/amazon-linux-2023/${SSM_ARCH}/recommended/image_id" --query Parameter.Value --output text)";;
  ami-*)  AMI="$AMI_KIND";;
  *) echo "--ami al2023|ecs|ami-xxxx" >&2; exit 2;;
esac
[[ "$AMI" == ami-* ]] || { echo "could not resolve AMI for $AMI_KIND" >&2; exit 1; }
ROOT_DEV="$(aws ec2 describe-images --region "$REGION" --image-ids "$AMI" --query 'Images[0].RootDeviceName' --output text)"
ROOT_GB="$(aws ec2 describe-images --region "$REGION" --image-ids "$AMI" --query 'Images[0].BlockDeviceMappings[0].Ebs.VolumeSize' --output text)"
(( ROOT_GB < 30 )) && ROOT_GB=30
echo "rows=[$ROWS] build_src=$BUILD_SRC label=$LABEL type=$ITYPE arch=$ARCH ami=$AMI ($AMI_KIND) root=$ROOT_DEV ${ROOT_GB}GiB bucket=$BUCKET run=$RUN_ID"

aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null || { echo "bucket $BUCKET not found" >&2; exit 1; }

# ---------------------------------------------------------------- IAM (idempotent)
if ! aws iam get-role --role-name "$PROFILE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$PROFILE" --description "KeepFrame benchmark host (self-terminating)" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --tags Key=project,Value=keepframe Key=owner,Value=bench >/dev/null
fi
POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Sid":"ListBucket","Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::${BUCKET}","Condition":{"StringLike":{"s3:prefix":["bench/*","manifests/*","raw/*","exif/*","originals/*"]}}},
 {"Sid":"ReadBench","Effect":"Allow","Action":"s3:GetObject","Resource":["arn:aws:s3:::${BUCKET}/bench/*","arn:aws:s3:::${BUCKET}/manifests/*","arn:aws:s3:::${BUCKET}/raw/*","arn:aws:s3:::${BUCKET}/exif/*","arn:aws:s3:::${BUCKET}/originals/*"]},
 {"Sid":"WriteResults","Effect":"Allow","Action":"s3:PutObject","Resource":["arn:aws:s3:::${BUCKET}/bench/results/*","arn:aws:s3:::${BUCKET}/bench/_logs/*","arn:aws:s3:::${BUCKET}/bench/_wheels/*"]}
]}
JSON
)
aws iam put-role-policy --role-name "$PROFILE" --policy-name bench-host --policy-document "$POLICY"
if ! aws iam get-instance-profile --instance-profile-name "$PROFILE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$PROFILE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$PROFILE" --role-name "$PROFILE"
  sleep 12   # instance-profile propagation
fi

VPC="$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
SG="$(aws ec2 describe-security-groups --region "$REGION" --filters Name=vpc-id,Values="$VPC" Name=group-name,Values="$SG_NAME" --query 'SecurityGroups[0].GroupId' --output text)"
if [[ "$SG" == None ]]; then
  SG="$(aws ec2 create-security-group --region "$REGION" --vpc-id "$VPC" --group-name "$SG_NAME" \
        --description "KeepFrame bench host: no ingress, default egress" --query GroupId --output text)"
fi
# pick a default subnet in an AZ that offers the type
AZS="$(aws ec2 describe-instance-type-offerings --region "$REGION" --location-type availability-zone \
  --filters Name=instance-type,Values="$ITYPE" --query 'InstanceTypeOfferings[].Location' --output text)"
SUBNET=""
for az in $AZS; do
  SUBNET="$(aws ec2 describe-subnets --region "$REGION" --filters Name=default-for-az,Values=true Name=availability-zone,Values="$az" --query 'Subnets[0].SubnetId' --output text)"
  [[ "$SUBNET" == subnet-* ]] && break
done
[[ "$SUBNET" == subnet-* ]] || { echo "no default subnet offering $ITYPE" >&2; exit 1; }

# ---------------------------------------------------------------- bundle
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
tar -C "$REPO" -czf "$TMP/bundle.tgz" --exclude='bench/results' --exclude='__pycache__' \
  bench/run_benchmark.py bench/run_worker_bench.py bench/summarize.py bench/make_synthetic_set.py bench/prices.yaml \
  bench/kleidicv_probe.py bench/ec2/host_run.sh scripts/install_opencv_wheel.sh pyproject.toml
if [[ -n "$WORKER_EVENT" ]]; then
  # product code at a committed ref (read-only export; never touches another checkout's working tree)
  git -C "$REPO" archive --format=tar.gz -o "$TMP/src.tgz" "$SRC_REF" keepframe config scripts/fetch_models.py
  SRC_COMMIT="$(git -C "$REPO" rev-parse "$SRC_REF")"
  aws s3 cp --only-show-errors "$TMP/src.tgz" "s3://${BUCKET}/bench/_bundle/${RUN_ID}.src.tgz"
  echo "worker source: $SRC_REF @ $SRC_COMMIT"
fi
BUNDLE_KEY="bench/_bundle/${RUN_ID}.tgz"
aws s3 cp --only-show-errors "$TMP/bundle.tgz" "s3://${BUCKET}/${BUNDLE_KEY}"
aws s3 cp --only-show-errors "$MANIFEST" "s3://${BUCKET}/bench/_bundle/${RUN_ID}.manifest.jsonl"

if [[ "$UVARCH" == x86_64 && -z "$UV_SHA256_X86_64" ]]; then
  UV_SHA256_X86_64="$(curl -sfL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz.sha256" | cut -d' ' -f1)"
fi
UV_SHA="$([[ "$UVARCH" == aarch64 ]] && echo "$UV_SHA256_ARM64" || echo "$UV_SHA256_X86_64")"

# ---------------------------------------------------------------- user-data
# A small stub: the host logic lives in bench/ec2/host_run.sh (inside the bundle), driven by host.env.
{
  printf 'REGION=%q\nBUCKET=%q\nLABEL=%q\nRUN_ID=%q\nDATE=%q\nMAXMIN=%q\n' "$REGION" "$BUCKET" "$LABEL" "$RUN_ID" "$DATE" "$MAXMIN"
  printf 'UV_VERSION=%q\nUVARCH=%q\nUV_SHA=%q\n' "$UV_VERSION" "$UVARCH" "$UV_SHA"
  printf 'DATA_PREFIX=%q\nDSNAME=%q\nWORKERS=%q\nREPS=%q\nWARMUP=%q\nSKIP_STAGE=%q\n' "$DATA_PREFIX" "$DSNAME" "$WORKERS" "$REPS" "$WARMUP" "$SKIP_STAGE"
  printf 'WORKER_EVENT=%q\nWORKER_REPS=%q\nSRC_COMMIT=%q\n' "$WORKER_EVENT" "$WORKER_REPS" "${SRC_COMMIT:-}"
  printf 'KLEIDICV=%q\n' "$KLEIDICV"
  printf 'BUILD_SRC=%q\nARMPL=%q\nSDIST=%q\nSDIST_SHA=%q\nARMPL_URL=%q\n' "$BUILD_SRC" "$ARMPL" "$SDIST" "$SDIST_SHA" "$ARMPL_URL"
  printf 'ROW_SPECS=%q\n' "$ROW_SPECS"
} > "$TMP/host.env"
cat > "$TMP/user-data.sh" <<UD
#!/bin/bash
# KeepFrame bench host ${RUN_ID}. Self-terminates; watchdog caps runtime at ${MAXMIN} min.
exec > >(tee -a /var/log/keepframe-bench.log) 2>&1
shutdown -h +${MAXMIN} "keepframe bench watchdog" || true
trap 'aws s3 cp --only-show-errors /var/log/keepframe-bench.log s3://${BUCKET}/bench/_logs/${RUN_ID}.log; sync; shutdown -h now' EXIT
export AWS_REGION=${REGION} AWS_DEFAULT_REGION=${REGION} HOME=/root
if command -v dnf >/dev/null; then dnf -y -q install tar gzip awscli-2 >/dev/null 2>&1 || true; fi
mkdir -p /opt/bench
aws s3 cp --only-show-errors s3://${BUCKET}/${BUNDLE_KEY} /tmp/bundle.tgz && tar -xzf /tmp/bundle.tgz -C /opt/bench
cat > /opt/bench/host.env <<'ENV'
$(cat "$TMP/host.env")
ENV
bash /opt/bench/bench/ec2/host_run.sh /opt/bench/host.env
UD
UD_BYTES=$(wc -c < "$TMP/user-data.sh")
(( UD_BYTES < 16000 )) || { echo "user-data too large ($UD_BYTES)" >&2; exit 1; }
if (( DRY )); then cat "$TMP/user-data.sh"; echo "dry run: not launching ($UD_BYTES bytes of user-data)"; exit 0; fi

IID="$(aws ec2 run-instances --region "$REGION" --image-id "$AMI" --instance-type "$ITYPE" --count 1 \
  --subnet-id "$SUBNET" --security-group-ids "$SG" --associate-public-ip-address \
  --iam-instance-profile Name="$PROFILE" \
  --instance-initiated-shutdown-behavior terminate \
  --metadata-options HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=2 \
  --block-device-mappings "DeviceName=${ROOT_DEV},Ebs={VolumeSize=${ROOT_GB},VolumeType=gp3,DeleteOnTermination=true}" \
  --user-data "file://$TMP/user-data.sh" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=keepframe-bench-${LABEL}},{Key=project,Value=keepframe},{Key=keepframe-bench,Value=true},{Key=run_id,Value=${RUN_ID}}]" \
                       "ResourceType=volume,Tags=[{Key=project,Value=keepframe},{Key=run_id,Value=${RUN_ID}}]" \
  --query 'Instances[0].InstanceId' --output text)"
echo "launched $IID ($ITYPE, $AMI) run_id=$RUN_ID"

if (( WAIT )); then
  START=$(date +%s)   # from launch (includes boot)
  aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
  echo "running; waiting for self-termination (watchdog ${MAXMIN} min)"
  while :; do
    ST="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" --query 'Reservations[0].Instances[0].State.Name' --output text)"
    [[ "$ST" == terminated ]] && break
    if (( $(date +%s) - START > (MAXMIN + 10) * 60 )); then
      echo "past watchdog; terminating $IID" >&2
      aws ec2 terminate-instances --region "$REGION" --instance-ids "$IID" >/dev/null
    fi
    sleep 30
  done
  echo "terminated after $(( ($(date +%s) - START) / 60 )) min"
  aws s3 cp --only-show-errors "s3://${BUCKET}/bench/_logs/${RUN_ID}.log" "$REPO/bench/results/${RUN_ID}.log" || true
  if (( BUILD_SRC )); then
    aws s3 ls "s3://${BUCKET}/bench/_wheels/${RUN_ID}/" || true
  else
    for row in $ROWS; do
      rl="${row%%=*}"
      mkdir -p "$REPO/bench/results/${DATE}/${rl}"
      for _try in 1 2 3; do  # the host's last upload can land just after the instance reports terminated
        aws s3 cp --only-show-errors --recursive "s3://${BUCKET}/bench/results/${DATE}/${rl}/" "$REPO/bench/results/${DATE}/${rl}/"
        [[ -n "$(ls -A "$REPO/bench/results/${DATE}/${rl}/")" ]] && break; sleep 20
      done
      ls -la "$REPO/bench/results/${DATE}/${rl}/" "$REPO/bench/results/${DATE}/${rl}/worker/" 2>/dev/null || true
    done
  fi
fi

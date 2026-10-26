# Benchmark hosts on EC2

`launch_bench_host.sh` starts one self-terminating instance that runs `bench/run_benchmark.py` and
uploads its results. Nothing stays running: the user-data ends with `shutdown -h now` and the
instance is launched with `InstanceInitiatedShutdownBehavior=terminate`. A `shutdown -h +MAX`
watchdog (default 90 min) caps the cost if anything hangs. With `--wait`, the script also force-
terminates after MAX + 10 min.

```bash
set -a; . ~/.config/keepframe/.env; set +a
export AWS_REGION=ap-south-1

# 2026-10-08 carotene vs KleidiCV A/B: both PyPI wheels back to back on ONE c8g.xlarge, stage + worker bench
bench/ec2/launch_bench_host.sh --rows "stock-carotene=m228 stock-kleidicv=m2014" --instance-type c8g.xlarge \
  --ami ami-0cf36b0c962e50fd7 --worker-event <eventId> --src-ref origin/main --max-minutes 80 --wait

# source build of the same sdist with -DWITH_KLEIDICV=ON (+ ArmPL); uploads the wheel to bench/_wheels/<run_id>/
bench/ec2/launch_bench_host.sh --build-src --armpl --label kleidicv-src --instance-type c8g.4xlarge --max-minutes 100 --wait
# ...then benchmark it next to a control row on one host
bench/ec2/launch_bench_host.sh --rows "kleidicv-src=s3://$BUCKET/bench/_wheels/<run_id>/<file>.whl#sha256=<hex> stock-kleidicv=m2014" \
  --instance-type c8g.xlarge --ami ami-0cf36b0c962e50fd7 --wait

# protocol size (16 workers)
bench/ec2/launch_bench_host.sh --rows "stock-carotene=m228 stock-kleidicv=m2014" --instance-type c8g.4xlarge --wait
bench/ec2/launch_bench_host.sh --label c7g-stock --instance-type c7g.4xlarge --ami al2023 --wait
bench/ec2/launch_bench_host.sh --label x86-baseline --instance-type c7i.4xlarge --ami al2023 --wait  # optional

# end-to-end product worker only (microbench + run_event x3), skipping the stage benchmark
bench/ec2/launch_bench_host.sh --label stock-kleidicv --wheel m2014 --instance-type c8g.xlarge --skip-stage-bench \
  --worker-event <eventId> --worker-reps 3 --max-minutes 45 --wait   # e.g. E09's id from data/selected_events.json

.venv/bin/python bench/summarize.py --date 2026-10-08 --upload
```

| flag | default | notes |
|---|---|---|
| `--rows` | `<label>=<wheel>` | Space-separated `label=wheel` rows run back to back on one instance, each in its own venv `/opt/venv-<label>`. Wheels: `m228` (manylinux_2_28 aarch64, Custom HAL carotene only), `m2014` (manylinux2014 aarch64, carotene + KleidiCV 26.03), `pypi` (pip resolves `==5.0.0.93`; that is `m228` on AL2023), or `s3://…whl#sha256=HEX`. Every wheel is sha256-checked before install. |
| `--label` / `--wheel` / `--cv2-wheel` | required unless `--rows` / `m2014` | One-row shorthand. x86 types are refused unless the label starts with `x86-`. |
| `--build-src` / `--armpl` | off | Build the pinned 5.0.0.93 sdist (`ENABLE_CONTRIB=1 ENABLE_HEADLESS=1`, `-DWITH_KLEIDICV=ON`; `--armpl` adds `-DWITH_ARMPL=ON` with Arm Performance Libraries 25.07 and falls back to KleidiCV-only if that fails). Output: `s3://$BUCKET/bench/_wheels/<run_id>/{*.whl,SHA256SUMS,build_info.json,build.log.gz}`. No benchmark. |
| `--instance-type` | c8g.4xlarge | Workers default to `nproc`. The instance type is read back from IMDS into env.json. |
| `--ami` | al2023 | `al2023` (SSM latest AL2023 arm64/x86_64), `ecs` (ECS-optimized AL2023) or an explicit `ami-…` (pin `ami-0cf36b0c962e50fd7` to match 2026-10-05). |
| `--manifest` / `--data-prefix` | `bench/manifest_5k.jsonl` / `bench/5k/` | Files are `s3://$BUCKET/<prefix><eventId>/<file of processed_key>`, synced to `/tmp/bench5k` before timing. The manifest is gitignored; a copy is in `data/bench/` on the data worktree. |
| `--reps` / `--warmup` / `--workers` | 3 / 200 / nproc | |
| `--bucket` | `keepframe-work-<account>-<region>` | Must exist. |
| `--worker-event` | unset | Also run `bench/run_worker_bench.py` per row: `git archive <--src-ref>` of `keepframe/ config/ scripts/fetch_models.py`, models from `config/models.lock`, the event's `manifests/ raw/ exif/ originals/` synced to `/tmp/wlocal`, LOCAL_MODE so nothing is written to `results/`. Output in `bench/results/<date>/<label>/worker/`. |
| `--worker-reps` / `--src-ref` / `--skip-stage-bench` | 3 / main / off | The source commit is printed in the host log. |
| `--dry-run` | off | Print the user-data and exit without launching. |

What it creates (idempotent, tagged `project=keepframe`):

- IAM role + instance profile `keepframe-bench-host`, inline policy `bench-host`: `s3:ListBucket` and
  `s3:GetObject` on `bench/*`, `manifests/*`, `raw/*`, `exif/*`, `originals/*`, `s3:PutObject` on `bench/results/*`, `bench/_logs/*` and `bench/_wheels/*`,. Nothing else: no SSM, no EC2 API.
- Security group `keepframe-bench-egress-only` in the default VPC: no ingress rules, default egress.
  There is no SSH key; debug through the console log (`aws ec2 get-console-output`) or
  `s3://$BUCKET/bench/_logs/<run_id>.log`.
- Public IP in a default subnet (egress to PyPI, GitHub for uv, S3, developer.arm.com for ArmPL). IMDSv2 is
  required.
- `s3://$BUCKET/bench/_bundle/<run_id>.tgz` (the bench/ scripts + pyproject) and `<run_id>.manifest.jsonl`.

User-data is a stub that unpacks the bundle and runs `bench/ec2/host_run.sh host.env`: install uv 0.11.16
(sha256-checked) and Python 3.12, then per row create `/opt/venv-<label>` with that row's OpenCV wheel plus
`numpy==2.3.3 psutil==7.2.2 pyyaml==6.0.3 boto3==1.43.108 pydantic==2.12.3`, sync the set to local disk once,
run the benchmark, upload `s3://$BUCKET/bench/results/<date>/<label>/` (raw per-image CSV excluded). `--wait`
pulls every row back into `bench/results/<date>/<label>/`. Every row's env.json records `custom_hal`
(`getBuildInformation()` "Custom HAL:" line), `kleidicv`, `armpl` and `opencv_wheel`.

Verify nothing is left behind:

```bash
aws ec2 describe-instances --filters Name=tag:keepframe-bench,Values=true \
  Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId'
aws ec2 describe-volumes --filters Name=tag:project,Values=keepframe Name=status,Values=available
```

The role, profile and security group are kept for the next run. Delete them with
`aws iam remove-role-from-instance-profile`, `delete-instance-profile`, `delete-role-policy`,
`delete-role` and `aws ec2 delete-security-group` once benchmarking is over (or when infra's CDK
takes them over).

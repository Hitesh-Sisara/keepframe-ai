# Runs on 2026-10-05

All EC2 hosts: ap-south-1, AL2023 arm64 `ami-0cf36b0c962e50fd7`, launched by
`bench/ec2/launch_bench_host.sh`, self-terminated (shutdown-behaviour terminate), no volumes left.
Times are from CloudTrail RunInstances and the user-data `exit rc` line; costs are **estimates** from
on-demand per-second billing (`bench/prices.yaml`) plus 30 GiB gp3 at $0.0912/GiB-month, prorated.

| run_id | label folder | instance | data | launched → user-data exit (UTC) | min | est. EC2 $ |
|---|---|---|---|---|---|---|
| 20261005T093932Z-stock | stock-synthetic1k | c8g.xlarge i-0ee74744f43695749 | synthetic_1k | 09:40:36 → 09:43:24 | 2.8 | 0.005 |
| 20261005T095808Z-stock | stock | c8g.xlarge i-0856497148c63add7 | manifest_5k | 09:58:55 → 10:09:16 | 10.4 | 0.019 |
| 20261005T101245Z-c7g-stock | c7g-stock | c7g.xlarge i-06879c37a1800ef20 | manifest_5k | 10:13:29 → 10:25:02 | 11.6 | 0.019 |
| 20261005T122728Z-stock | stock/worker | c8g.xlarge i-0fae221c0219dd063 | worker event (661 photos), src main@fc9238f | 12:28:15 → 12:30:54 | 2.7 | 0.005 |
| local | x86-devbox-not-a-baseline | shared 8-vCPU x86 dev box | synthetic_1k | 09:41 → 09:46 | | 0 |

Total EC2 ≈ 38.5 instance-minutes ≈ $0.07 (estimate). EBS: about 30 GiB × 0.65 h total × $0.0912/(GiB·730 h) ≈ $0.002. S3 storage/requests for
`bench/` are negligible (about 2.3 GB stored, a few thousand GETs).

Why 4-vCPU hosts: EC2 Standard On-Demand vCPU quota is 8 (L-1216C47A, raise to 64 pending); the
pre-existing t4g.micro i-0538716520bc6bd6e uses 2. The protocol host is c8g.4xlarge with 16 workers.

The dev-box row only validates the script. It shares 8 vCPUs with other agents' work (CPU 66 %), so it
is not an x86 baseline. The protocol baseline is `x86-baseline` on c7i.4xlarge.

# Benchmark results 2026-10-05

Median of reps; brackets are min-max across reps. Per-stage numbers are per image on one worker process with `cv2.setNumThreads(1)`. Images were on local disk before timing.

| label | instance | OpenCV | cv2 file | dataset (N) | workers | img/s | $/1k img | CPU % | peak RSS MB |
|---|---|---|---|---|---|---|---|---|---|
| stock | c8g.xlarge | 5.0.0 | `/opt/venv/lib/python3.12/site-packages/cv2/__init__.py` | manifest_5k (5000) | 4 | 28.43 [28.37-28.45] | 0.00105 | 100.0 | 804 |
| c7g-stock | c7g.xlarge | 5.0.0 | `/opt/venv/lib/python3.12/site-packages/cv2/__init__.py` | manifest_5k (5000) | 4 | 25.34 [25.31-25.38] | 0.00108 | 99.9 | 798 |
| stock-synthetic1k | c8g.xlarge | 5.0.0 | `/opt/venv/lib/python3.12/site-packages/cv2/__init__.py` | synthetic_1k (1000) | 4 | 28.00 [27.99-28.04] | 0.00107 | 99.8 | 670 |
| x86-devbox-not-a-baseline | non-ec2 | 5.0.0 | `/home/dev/keepframe/.venv/lib/python3.12/site-packages/cv2/__init__.py` | synthetic_1k (1000) | 4 | 11.67 [11.25-12.32] | n/a | 66.0 | 656 |

**Warning:** these hosts ran on different data sets (set_sha256 differs); compare rows only within one dataset.

## Per-stage latency (ms per image)

| stage | stock p50 | stock p95 | c7g-stock p50 | c7g-stock p95 | stock-synthetic1k p50 | stock-synthetic1k p95 | x86-devbox-not-a-baseline p50 | x86-devbox-not-a-baseline p95 |
|---|---|---|---|---|---|---|---|---|
| decode_webp | 32.198 [32.162-32.297] | 47.192 | 36.960 [36.813-36.982] | 53.592 | 25.381 [25.275-25.386] | 102.885 | 73.928 [65.730-74.967] | 203.468 |
| resize_1024_area | 12.944 [12.900-12.948] | 13.596 | 13.970 [13.955-13.985] | 14.180 | 12.706 [12.682-12.748] | 13.639 | 32.066 [31.505-33.805] | 51.471 |
| cvtcolor_gray | 0.230 [0.229-0.230] | 0.240 | 0.270 [0.270-0.271] | 0.280 | 0.226 [0.225-0.226] | 0.235 | 0.696 [0.691-0.698] | 0.915 |
| clahe | 2.538 [2.537-2.541] | 2.833 | 2.831 [2.831-2.831] | 3.097 | 2.485 [2.485-2.489] | 2.640 | 6.769 [6.602-7.399] | 10.521 |
| laplacian_sobel | 6.376 [6.371-6.402] | 7.499 | 6.117 [6.097-6.233] | 7.353 | 6.756 [6.684-6.764] | 7.712 | 26.275 [23.577-28.362] | 64.757 |
| phash_blockmean | 0.299 [0.296-0.300] | 0.318 | 0.340 [0.340-0.340] | 0.357 | 0.297 [0.296-0.298] | 0.317 | 0.929 [0.913-0.930] | 1.239 |
| yunet_1024 | 59.215 [58.839-59.373] | 62.297 | 66.551 [66.507-66.581] | 68.976 | 60.306 [60.084-60.561] | 63.254 | 105.710 [104.166-113.906] | 140.264 |
| brisque | 26.361 [26.296-26.391] | 28.466 | 29.829 [29.782-29.836] | 32.070 | 25.756 [25.682-25.837] | 30.516 | 60.956 [60.817-62.879] | 84.417 |
| opencv_total | 139.831 [139.667-140.049] | 156.336 | 156.956 [156.686-157.072] | 175.558 | 133.930 [133.722-133.999] | 215.382 | 320.734 [304.540-334.073] | 478.388 |
| read_file | 0.098 [0.096-0.099] | 0.132 | 0.102 [0.101-0.105] | 0.133 | 0.086 [0.086-0.087] | 0.241 | 2.270 [1.947-2.386] | 10.411 |
| image_total | 139.937 [139.768-140.153] | 156.449 | 157.062 [156.792-157.173] | 175.670 | 134.008 [133.809-134.082] | 215.622 | 323.149 [308.304-336.875] | 482.687 |

## Speed-up vs `stock` (p50 ratio, >1 = faster than baseline)

| stage | c7g-stock | stock-synthetic1k |
|---|---|---|
| decode_webp | 0.87x |
| resize_1024_area | 0.93x |
| cvtcolor_gray | 0.85x |
| clahe | 0.90x |
| laplacian_sobel | 1.04x |
| phash_blockmean | 0.88x |
| yunet_1024 | 0.89x |
| brisque | 0.88x |
| opencv_total | 0.89x |
| read_file | 0.96x |
| image_total | 0.89x |
| images/sec | 0.89x |

## End-to-end product worker (`keepframe.worker.run_event`, no LLM)

Cold run per rep (proxies regenerated), inputs on local disk, LOCAL_MODE. Unlike the stage table above, the worker runs faces/eyes only on grouped photos and BRISQUE only when votes disagree, as the product does.

| label | instance | photos | workers | img/s | wall s | $/1k img | CPU % | peak RSS MB | verdicts (keep/reject/review) | identical across reps |
|---|---|---|---|---|---|---|---|---|---|---|
| stock | c8g.xlarge | 661 | 4 | 29.77 [29.67-29.83] | 22.21 | 0.00102 | 97.9 | 1677 | 312/42/307 | True |

Worker stages on `stock` (per-photo p50/p95 ms; wall ms for pipeline phases)

| stage | n | p50 | p95 | phase wall ms |
|---|---|---|---|---|
| analyse | 0 | n/a | n/a | 12683.3 |
| best | 50 | 0.059 | 0.108 |  |
| cluster | 0 | n/a | n/a | 10.7 |
| contact_sheet | 10 | 10.154 | 15.169 |  |
| decide | 0 | n/a | n/a | 165.8 |
| decode_full | 661 | 27.535 | 39.595 |  |
| exposure | 661 | 3.217 | 3.529 |  |
| eyes | 91 | 10.514 | 25.857 |  |
| face_quality | 92 | 57.057 | 117.619 |  |
| faces | 92 | 31.842 | 81.087 |  |
| faces_load_full | 92 | 29.541 | 41.318 |  |
| hashes | 661 | 4.674 | 5.373 |  |
| ingest | 0 | n/a | n/a | 102.5 |
| proxies_hashes | 0 | n/a | n/a | 9188.4 |
| proxy_decode | 661 | 2.253 | 2.797 |  |
| proxy_encode | 661 | 2.647 | 2.905 |  |
| proxy_total | 661 | 45.766 | 59.006 |  |
| resize | 661 | 12.462 | 13.091 |  |
| saliency | 560 | 33.670 | 34.052 |  |
| sharpness_metrics | 661 | 15.760 | 56.344 |  |
| write | 0 | n/a | n/a | 7.6 |

Labels containing `not-a-baseline` are script-validation runs on the x86 dev box, not product numbers. `$/1k img` = (EC2 on-demand $/h + COOL fee $/h) x wall-hours / (N/1000), prices from `bench/prices.yaml`; n/a off EC2.

# Benchmark results 2026-10-08

Median of reps; brackets are min-max across reps. Per-stage numbers are per image on one worker process with `cv2.setNumThreads(1)`. Images were on local disk before timing.

| label | instance | OpenCV | Custom HAL | dataset (N) | workers | img/s | $/1k img | CPU % | peak RSS MB |
|---|---|---|---|---|---|---|---|---|---|
| stock-carotene | c8g.xlarge | 5.0.0 | YES (carotene (ver 0.0.1)) | manifest_5k (5000) | 4 | 28.47 [28.44-28.51] | 0.00105 | 100.0 | 795 |
| stock-kleidicv | c8g.xlarge | 5.0.0 | YES (carotene (ver 0.0.1) KleidiCV (ver 26.03)) | manifest_5k (5000) | 4 | 29.55 [29.51-29.55] | 0.00102 | 100.0 | 796 |
| stock-kleidicv-r2 | c8g.xlarge | 5.0.0 | YES (carotene (ver 0.0.1) KleidiCV (ver 26.03)) | manifest_5k (5000) | 4 | 29.35 [29.31-29.39] | 0.00102 | 100.0 | 790 |
| src-gcc14-carotene | c8g.xlarge | 5.0.0 | YES (carotene (ver 0.0.1)) | manifest_5k (5000) | 4 | 26.23 [26.23-26.32] | 0.00114 | 99.9 | 790 |
| src-gcc14-kleidicv | c8g.xlarge | 5.0.0 | YES (carotene (ver 0.0.1) KleidiCV (ver 26.03)) | manifest_5k (5000) | 4 | 26.19 [26.19-26.19] | 0.00114 | 100.0 | 774 |
| kleidicv-src | c8g.xlarge | 5.0.0 | YES (carotene (ver 0.0.1) KleidiCV (ver 26.03) ARMPL (ver 0.0.1)) | manifest_5k (5000) | 4 | 26.12 [26.09-26.13] | 0.00115 | 100.0 | 793 |

## Per-stage latency (ms per image)

| stage | stock-carotene p50 | stock-carotene p95 | stock-kleidicv p50 | stock-kleidicv p95 | stock-kleidicv-r2 p50 | stock-kleidicv-r2 p95 | src-gcc14-carotene p50 | src-gcc14-carotene p95 | src-gcc14-kleidicv p50 | src-gcc14-kleidicv p95 | kleidicv-src p50 | kleidicv-src p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| decode_webp | 31.674 [31.670-31.761] | 46.489 | 32.826 [32.678-33.003] | 47.798 | 32.902 [32.882-32.944] | 47.891 | 31.465 [31.454-31.555] | 46.075 | 31.537 [31.511-32.044] | 46.428 | 31.546 [31.495-31.602] | 46.138 |
| resize_1024_area | 12.937 [12.930-12.941] | 13.565 | 15.238 [15.233-15.253] | 15.761 | 15.249 [15.239-15.255] | 15.716 | 14.907 [14.897-14.935] | 15.468 | 14.911 [14.910-14.921] | 15.511 | 14.920 [14.917-14.928] | 15.440 |
| cvtcolor_gray | 0.228 [0.228-0.230] | 0.238 | 0.291 [0.290-0.291] | 0.298 | 0.294 [0.294-0.294] | 0.303 | 0.226 [0.226-0.226] | 0.235 | 0.226 [0.226-0.227] | 0.235 | 0.232 [0.232-0.232] | 0.242 |
| clahe | 2.535 [2.531-2.539] | 2.831 | 2.534 [2.532-2.540] | 2.812 | 2.537 [2.537-2.538] | 2.816 | 2.554 [2.554-2.556] | 2.845 | 2.527 [2.526-2.529] | 2.822 | 2.566 [2.564-2.567] | 2.860 |
| laplacian_sobel | 6.378 [6.335-6.424] | 7.201 | 6.553 [6.487-6.570] | 7.648 | 6.715 [6.686-6.748] | 7.865 | 6.658 [6.644-6.701] | 7.677 | 6.674 [6.580-6.679] | 7.712 | 6.777 [6.776-6.882] | 7.883 |
| phash_blockmean | 0.295 [0.295-0.299] | 0.316 | 0.437 [0.436-0.439] | 0.454 | 0.448 [0.448-0.449] | 0.467 | 0.296 [0.296-0.299] | 0.316 | 0.298 [0.297-0.298] | 0.318 | 0.333 [0.333-0.334] | 0.354 |
| yunet_1024 | 59.683 [59.616-59.728] | 62.516 | 52.719 [52.497-52.791] | 55.172 | 53.073 [52.861-53.113] | 55.389 | 69.820 [69.226-69.843] | 72.740 | 69.710 [69.147-70.071] | 72.537 | 69.857 [69.713-70.162] | 72.793 |
| brisque | 26.329 [26.307-26.352] | 28.436 | 24.254 [24.208-24.270] | 26.383 | 24.372 [24.365-24.382] | 26.432 | 26.331 [26.299-26.361] | 28.482 | 26.385 [26.362-26.522] | 28.564 | 26.470 [26.428-26.513] | 28.643 |
| opencv_total | 139.659 [139.429-139.806] | 155.896 | 134.557 [134.377-134.603] | 151.125 | 135.372 [135.129-135.397] | 151.771 | 151.501 [151.173-151.603] | 168.169 | 151.960 [151.710-152.037] | 168.419 | 152.332 [152.299-152.534] | 168.919 |
| read_file | 0.096 [0.095-0.099] | 0.131 | 0.096 [0.095-0.096] | 0.131 | 0.103 [0.101-0.104] | 0.142 | 0.097 [0.095-0.099] | 0.133 | 0.097 [0.097-0.099] | 0.132 | 0.104 [0.102-0.105] | 0.143 |
| image_total | 139.755 [139.516-139.903] | 156.034 | 134.652 [134.471-134.711] | 151.267 | 135.474 [135.230-135.507] | 151.907 | 151.604 [151.266-151.697] | 168.301 | 152.055 [151.803-152.129] | 168.557 | 152.439 [152.405-152.650] | 169.048 |

## Speed-up vs `stock-carotene` (p50 ratio, >1 = faster than baseline)

| stage | stock-kleidicv | stock-kleidicv-r2 | src-gcc14-carotene | src-gcc14-kleidicv | kleidicv-src |
|---|---|---|---|---|---|
| decode_webp | 0.96x | 0.96x | 1.01x | 1.00x | 1.00x |
| resize_1024_area | 0.85x | 0.85x | 0.87x | 0.87x | 0.87x |
| cvtcolor_gray | 0.78x | 0.78x | 1.01x | 1.01x | 0.98x |
| clahe | 1.00x | 1.00x | 0.99x | 1.00x | 0.99x |
| laplacian_sobel | 0.97x | 0.95x | 0.96x | 0.96x | 0.94x |
| phash_blockmean | 0.68x | 0.66x | 1.00x | 0.99x | 0.89x |
| yunet_1024 | 1.13x | 1.12x | 0.85x | 0.86x | 0.85x |
| brisque | 1.09x | 1.08x | 1.00x | 1.00x | 0.99x |
| opencv_total | 1.04x | 1.03x | 0.92x | 0.92x | 0.92x |
| read_file | 1.00x | 0.92x | 0.99x | 0.98x | 0.92x |
| image_total | 1.04x | 1.03x | 0.92x | 0.92x | 0.92x |
| images/sec | 1.04x | 1.03x | 0.92x | 0.92x | 0.92x |
| $/1k images | 0.96x | 0.97x | 1.09x | 1.09x | 1.09x |

## KleidiCV ON vs OFF (p50 ratio OFF/ON, >1 = KleidiCV faster)

Pairs gated on env.json `opencv_hal` (`carotene` vs `kleidicv-26.03+carotene`). C++ compiler: `src-gcc14-carotene` /usr/bin/gcc14-g++ (ver 14.2.1); `src-gcc14-kleidicv` /usr/bin/gcc14-g++ (ver 14.2.1); `stock-carotene` /opt/rh/gcc-toolset-14/root/usr/bin/c++ (ver 14.2.1); `stock-kleidicv` /opt/rh/devtoolset-10/root/usr/bin/c++ (ver 10.2.1)

| stage | `src-gcc14-kleidicv` vs `src-gcc14-carotene` | `stock-kleidicv` vs `stock-carotene` |
|---|---|---|
| decode_webp | 0.998x | 0.965x |
| resize_1024_area | 1.000x | 0.849x |
| cvtcolor_gray | 1.000x | 0.785x |
| clahe | 1.011x | 1.000x |
| laplacian_sobel | 0.998x | 0.973x |
| phash_blockmean | 0.994x | 0.675x |
| yunet_1024 | 1.002x | 1.132x |
| brisque | 0.998x | 1.086x |
| opencv_total | 0.997x | 1.038x |
| read_file | 0.996x | 0.998x |
| image_total | 0.997x | 1.038x |
| images/sec | 0.998x | 1.038x |

## End-to-end product worker (`keepframe.worker.run_event`, no LLM)

Cold run per rep (proxies regenerated), inputs on local disk, LOCAL_MODE. Unlike the stage table above, the worker runs faces/eyes only on grouped photos and BRISQUE only when votes disagree, as the product does.

| label | instance | photos | workers | img/s | wall s | $/1k img | CPU % | peak RSS MB | verdicts (keep/reject/review) | identical across reps |
|---|---|---|---|---|---|---|---|---|---|---|
| stock-carotene | c8g.xlarge | 661 | 4 | 27.45 [27.38-27.46] | 24.08 | 0.00110 | 98.3 | 1672 | 609/46/6 | True |
| stock-kleidicv | c8g.xlarge | 661 | 4 | 25.85 [25.84-25.98] | 25.57 | 0.00117 | 99.2 | 1670 | 609/46/6 | True |
| stock-kleidicv-r2 | c8g.xlarge | 661 | 4 | 25.56 [25.55-25.66] | 25.86 | 0.00119 | 99.3 | 1646 | 609/46/6 | True |
| src-gcc14-carotene | c8g.xlarge | 661 | 4 | 26.51 [26.51-26.66] | 24.94 | 0.00114 | 97.9 | 1652 | 609/46/6 | True |
| src-gcc14-kleidicv | c8g.xlarge | 661 | 4 | 26.55 [26.55-26.60] | 24.89 | 0.00114 | 98.7 | 1640 | 609/46/6 | True |
| kleidicv-src | c8g.xlarge | 661 | 4 | 26.62 [26.60-26.63] | 24.83 | 0.00114 | 98.4 | 1667 | 609/46/6 | True |

Worker speed-up vs `stock-carotene` (images/s ratio; per-stage p50 ratio, >1 = faster)

| metric | stock-kleidicv | stock-kleidicv-r2 | src-gcc14-carotene | src-gcc14-kleidicv | kleidicv-src |
|---|---|---|---|---|---|
| images/s | 0.94x | 0.93x | 0.97x | 0.97x | 0.97x |
| analyse | 0.98x | 0.96x | 0.97x | 0.97x | 0.98x |
| best | 0.98x | 0.96x | 0.98x | 1.00x | 0.94x |
| cluster | 1.00x | 0.95x | 1.01x | 0.99x | 0.95x |
| contact_sheet | 0.40x | 0.37x | 0.45x | 0.54x | 0.48x |
| decide | 0.70x | 0.68x | 0.76x | 0.75x | 0.72x |
| decode_full | 0.96x | 0.96x | 1.00x | 1.01x | 1.01x |
| exposure | 0.76x | 0.74x | 1.00x | 1.00x | 0.98x |
| eyes | 1.12x | 1.09x | 0.91x | 0.91x | 0.88x |
| face_quality | 1.20x | 1.19x | 0.84x | 0.83x | 0.83x |
| faces | 1.10x | 1.06x | 0.90x | 0.89x | 0.86x |
| faces_load_full | 0.97x | 0.97x | 1.01x | 1.01x | 1.00x |
| hashes | 0.88x | 0.87x | 1.02x | 1.04x | 1.02x |
| ingest | 1.00x | 0.97x | 1.00x | 1.01x | 0.97x |
| list | 1.00x | 0.98x | 0.99x | 0.99x | 0.97x |
| prefetch | 1.00x | 0.67x | 1.00x | 1.00x | 0.67x |
| prefetch_groups | 1.17x | 0.87x | 1.00x | 1.17x | 1.00x |
| proxies_hashes | 0.90x | 0.89x | 0.97x | 0.97x | 0.96x |
| proxy_decode | 0.92x | 0.91x | 1.00x | 1.00x | 0.99x |
| proxy_encode | 0.90x | 0.88x | 0.99x | 0.99x | 0.96x |
| proxy_total | 0.92x | 0.91x | 0.95x | 0.96x | 0.95x |
| proxy_upload_drain | n/a | n/a | n/a | n/a | n/a |
| resize | 0.84x | 0.84x | 0.85x | 0.84x | 0.84x |
| saliency | 1.18x | 1.16x | 0.99x | 0.99x | 0.99x |
| sharpness_metrics | 0.93x | 0.92x | 0.98x | 0.98x | 1.32x |
| write | 1.01x | 0.98x | 1.01x | 1.01x | 0.96x |

Worker stages on `stock-carotene` (per-photo p50/p95 ms; wall ms for pipeline phases)

| stage | n | p50 | p95 | phase wall ms |
|---|---|---|---|---|
| analyse | 0 | n/a | n/a | 14829.4 |
| best | 50 | 0.047 | 0.061 |  |
| cluster | 0 | n/a | n/a | 10.5 |
| contact_sheet | 8 | 26.192 | 36.896 |  |
| decide | 0 | n/a | n/a | 97.7 |
| decode_full | 661 | 27.167 | 40.175 |  |
| exposure | 661 | 17.136 | 20.046 |  |
| eyes | 91 | 10.162 | 24.843 |  |
| face_quality | 92 | 56.900 | 115.150 |  |
| faces | 92 | 33.180 | 78.109 |  |
| faces_load_full | 92 | 29.374 | 40.691 |  |
| hashes | 661 | 4.516 | 5.229 |  |
| ingest | 0 | n/a | n/a | 101.2 |
| list | 0 | n/a | n/a | 17.9 |
| prefetch | 0 | n/a | n/a | 0.2 |
| prefetch_groups | 0 | n/a | n/a | 0.7 |
| proxies_hashes | 0 | n/a | n/a | 8982.1 |
| proxy_decode | 661 | 2.221 | 2.761 |  |
| proxy_encode | 661 | 2.560 | 2.815 |  |
| proxy_total | 661 | 44.941 | 58.508 |  |
| proxy_upload_drain | 0 | n/a | n/a |  |
| resize | 661 | 12.398 | 12.990 |  |
| saliency | 560 | 33.623 | 34.393 |  |
| sharpness_metrics | 661 | 15.443 | 55.651 |  |
| write | 0 | n/a | n/a | 8.2 |

Worker stages on `stock-kleidicv` (per-photo p50/p95 ms; wall ms for pipeline phases)

| stage | n | p50 | p95 | phase wall ms |
|---|---|---|---|---|
| analyse | 0 | n/a | n/a | 15191.6 |
| best | 50 | 0.048 | 0.062 |  |
| cluster | 0 | n/a | n/a | 10.5 |
| contact_sheet | 8 | 65.686 | 76.003 |  |
| decide | 0 | n/a | n/a | 138.8 |
| decode_full | 661 | 28.172 | 42.218 |  |
| exposure | 661 | 22.684 | 25.625 |  |
| eyes | 91 | 9.046 | 21.665 |  |
| face_quality | 92 | 47.515 | 97.784 |  |
| faces | 92 | 30.218 | 70.737 |  |
| faces_load_full | 92 | 30.311 | 41.851 |  |
| hashes | 661 | 5.147 | 5.885 |  |
| ingest | 0 | n/a | n/a | 100.8 |
| list | 0 | n/a | n/a | 17.9 |
| prefetch | 0 | n/a | n/a | 0.2 |
| prefetch_groups | 0 | n/a | n/a | 0.6 |
| proxies_hashes | 0 | n/a | n/a | 9968.3 |
| proxy_decode | 661 | 2.424 | 2.976 |  |
| proxy_encode | 661 | 2.858 | 3.122 |  |
| proxy_total | 661 | 48.771 | 63.643 |  |
| proxy_upload_drain | 0 | n/a | n/a |  |
| resize | 661 | 14.743 | 15.236 |  |
| saliency | 560 | 28.503 | 29.868 |  |
| sharpness_metrics | 661 | 16.535 | 65.246 |  |
| write | 0 | n/a | n/a | 8.1 |

Worker stages on `stock-kleidicv-r2` (per-photo p50/p95 ms; wall ms for pipeline phases)

| stage | n | p50 | p95 | phase wall ms |
|---|---|---|---|---|
| analyse | 0 | n/a | n/a | 15405.1 |
| best | 50 | 0.049 | 0.063 |  |
| cluster | 0 | n/a | n/a | 11.1 |
| contact_sheet | 8 | 69.863 | 77.072 |  |
| decide | 0 | n/a | n/a | 143.9 |
| decode_full | 661 | 28.255 | 42.089 |  |
| exposure | 661 | 23.058 | 25.853 |  |
| eyes | 91 | 9.284 | 22.612 |  |
| face_quality | 92 | 47.959 | 101.597 |  |
| faces | 92 | 31.441 | 73.278 |  |
| faces_load_full | 92 | 30.408 | 42.164 |  |
| hashes | 661 | 5.219 | 5.932 |  |
| ingest | 0 | n/a | n/a | 104.2 |
| list | 0 | n/a | n/a | 18.2 |
| prefetch | 0 | n/a | n/a | 0.3 |
| prefetch_groups | 0 | n/a | n/a | 0.8 |
| proxies_hashes | 0 | n/a | n/a | 10056.6 |
| proxy_decode | 661 | 2.447 | 3.002 |  |
| proxy_encode | 661 | 2.925 | 3.198 |  |
| proxy_total | 661 | 49.166 | 63.838 |  |
| proxy_upload_drain | 0 | n/a | n/a |  |
| resize | 661 | 14.801 | 15.251 |  |
| saliency | 560 | 28.933 | 30.066 |  |
| sharpness_metrics | 661 | 16.794 | 65.611 |  |
| write | 0 | n/a | n/a | 8.4 |

Worker stages on `src-gcc14-carotene` (per-photo p50/p95 ms; wall ms for pipeline phases)

| stage | n | p50 | p95 | phase wall ms |
|---|---|---|---|---|
| analyse | 0 | n/a | n/a | 15357.2 |
| best | 50 | 0.048 | 0.068 |  |
| cluster | 0 | n/a | n/a | 10.4 |
| contact_sheet | 8 | 58.071 | 63.389 |  |
| decide | 0 | n/a | n/a | 128.1 |
| decode_full | 661 | 27.032 | 39.032 |  |
| exposure | 661 | 17.131 | 20.042 |  |
| eyes | 91 | 11.149 | 27.034 |  |
| face_quality | 92 | 67.864 | 137.472 |  |
| faces | 92 | 36.924 | 90.559 |  |
| faces_load_full | 92 | 29.170 | 40.271 |  |
| hashes | 661 | 4.442 | 5.146 |  |
| ingest | 0 | n/a | n/a | 100.7 |
| list | 0 | n/a | n/a | 18.0 |
| prefetch | 0 | n/a | n/a | 0.2 |
| prefetch_groups | 0 | n/a | n/a | 0.7 |
| proxies_hashes | 0 | n/a | n/a | 9252.9 |
| proxy_decode | 661 | 2.217 | 2.766 |  |
| proxy_encode | 661 | 2.585 | 2.838 |  |
| proxy_total | 661 | 47.069 | 59.765 |  |
| proxy_upload_drain | 0 | n/a | n/a |  |
| resize | 661 | 14.646 | 14.972 |  |
| saliency | 560 | 34.111 | 34.694 |  |
| sharpness_metrics | 661 | 15.704 | 55.753 |  |
| write | 0 | n/a | n/a | 8.1 |

Worker stages on `src-gcc14-kleidicv` (per-photo p50/p95 ms; wall ms for pipeline phases)

| stage | n | p50 | p95 | phase wall ms |
|---|---|---|---|---|
| analyse | 0 | n/a | n/a | 15339.6 |
| best | 50 | 0.047 | 0.060 |  |
| cluster | 0 | n/a | n/a | 10.6 |
| contact_sheet | 8 | 48.548 | 63.983 |  |
| decide | 0 | n/a | n/a | 131.0 |
| decode_full | 661 | 26.947 | 38.988 |  |
| exposure | 661 | 17.131 | 19.998 |  |
| eyes | 91 | 11.188 | 27.184 |  |
| face_quality | 92 | 68.344 | 138.474 |  |
| faces | 92 | 37.464 | 90.341 |  |
| faces_load_full | 92 | 29.144 | 40.524 |  |
| hashes | 661 | 4.345 | 5.087 |  |
| ingest | 0 | n/a | n/a | 99.9 |
| list | 0 | n/a | n/a | 18.0 |
| prefetch | 0 | n/a | n/a | 0.2 |
| prefetch_groups | 0 | n/a | n/a | 0.6 |
| proxies_hashes | 0 | n/a | n/a | 9217.1 |
| proxy_decode | 661 | 2.213 | 2.771 |  |
| proxy_encode | 661 | 2.597 | 2.838 |  |
| proxy_total | 661 | 47.038 | 59.661 |  |
| proxy_upload_drain | 0 | n/a | n/a |  |
| resize | 661 | 14.709 | 14.977 |  |
| saliency | 560 | 33.922 | 34.535 |  |
| sharpness_metrics | 661 | 15.705 | 55.789 |  |
| write | 0 | n/a | n/a | 8.1 |

Worker stages on `kleidicv-src` (per-photo p50/p95 ms; wall ms for pipeline phases)

| stage | n | p50 | p95 | phase wall ms |
|---|---|---|---|---|
| analyse | 0 | n/a | n/a | 15146.1 |
| best | 50 | 0.050 | 0.066 |  |
| cluster | 0 | n/a | n/a | 11.1 |
| contact_sheet | 8 | 54.913 | 64.222 |  |
| decide | 0 | n/a | n/a | 136.2 |
| decode_full | 661 | 26.955 | 39.083 |  |
| exposure | 661 | 17.479 | 20.339 |  |
| eyes | 91 | 11.517 | 27.728 |  |
| face_quality | 92 | 68.433 | 137.770 |  |
| faces | 92 | 38.494 | 90.666 |  |
| faces_load_full | 92 | 29.296 | 40.381 |  |
| hashes | 661 | 4.427 | 5.153 |  |
| ingest | 0 | n/a | n/a | 103.8 |
| list | 0 | n/a | n/a | 18.4 |
| prefetch | 0 | n/a | n/a | 0.3 |
| prefetch_groups | 0 | n/a | n/a | 0.7 |
| proxies_hashes | 0 | n/a | n/a | 9351.2 |
| proxy_decode | 661 | 2.240 | 2.790 |  |
| proxy_encode | 661 | 2.667 | 2.936 |  |
| proxy_total | 661 | 47.307 | 59.908 |  |
| proxy_upload_drain | 0 | n/a | n/a |  |
| resize | 661 | 14.825 | 15.154 |  |
| saliency | 560 | 34.009 | 35.054 |  |
| sharpness_metrics | 661 | 11.680 | 57.173 |  |
| write | 0 | n/a | n/a | 8.5 |

## Single-op probe (`bench/kleidicv_probe.py`, one thread, synthetic 1536x1024 frame, p50 ms)

`ours` = calls the stage benchmark makes; `hal-path` = u8 calls the OpenCV 5 KleidiCV HAL implements.

| op | kind | probe-src-gcc14-carotene | probe-src-gcc14-kleidicv | probe-stock-carotene | probe-stock-kleidicv |
|---|---|---|---|---|---|
| resize_area_bgr_to_1024 | ours | 6.794 | 6.811 | 5.934 | 6.823 |
| cvtcolor_bgr2gray_1024 | ours | 0.208 | 0.208 | 0.210 | 0.277 |
| clahe_1024 | ours | 2.358 | 2.347 | 2.366 | 2.360 |
| laplacian_64f_1024 | ours | 1.442 | 1.438 | 1.078 | 1.399 |
| sobel_64f_1024 | ours | 1.329 | 1.321 | 1.145 | 1.317 |
| phash_1024 | ours | 0.014 | 0.014 | 0.015 | 0.018 |
| resize_linear_bgr_half | hal-path | 0.847 | 0.572 | 0.840 | 0.579 |
| resize_linear_gray_half | hal-path | 0.222 | 0.032 | 0.207 | 0.033 |
| cvtcolor_bgr2gray_full | hal-path | 0.459 | 0.459 | 0.459 | 0.615 |
| cvtcolor_bgr2rgb_full | hal-path | 0.167 | 0.157 | 0.169 | 0.159 |
| gaussian_3x3_gray | hal-path | 0.412 | 0.281 | 0.464 | 0.261 |
| gaussian_5x5_gray | hal-path | 0.776 | 0.417 | 0.766 | 0.468 |
| sobel_16s_gray | hal-path | 0.438 | 0.428 | 0.442 | 0.524 |
| threshold_binary_gray | hal-path | 0.048 | 0.049 | 0.046 | 0.043 |
| erode_3x3_gray | hal-path | 0.184 | 0.184 | 0.184 | 0.185 |
| median_5_gray | hal-path | 2.457 | 2.306 | 2.638 | 2.346 |
| minmaxloc_gray | hal-path | 0.111 | 0.100 | 0.112 | 0.345 |
| flip_bgr | hal-path | 0.241 | 0.243 | 0.246 | 0.237 |
| transpose_gray | hal-path | 0.137 | 0.153 | 0.132 | 0.156 |
| exp_f32 | hal-path | 1.696 | 0.929 | 1.703 | 0.942 |
| multiply_u8 | hal-path | 0.524 | 0.524 | 0.530 | 0.785 |

Custom HAL per probe: `probe-src-gcc14-carotene` = YES (carotene (ver 0.0.1)); `probe-src-gcc14-kleidicv` = YES (carotene (ver 0.0.1) KleidiCV (ver 26.03)); `probe-stock-carotene` = YES (carotene (ver 0.0.1)); `probe-stock-kleidicv` = YES (carotene (ver 0.0.1) KleidiCV (ver 26.03))

Labels containing `not-a-baseline` are script-validation runs on the x86 dev box, not product numbers. `$/1k img` = EC2 on-demand $/h x wall-hours / (N/1000), prices from `bench/prices.yaml`; n/a off EC2. Every row is open-source OpenCV (no software fee).

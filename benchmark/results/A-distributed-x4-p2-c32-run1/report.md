# A-distributed-x4-p2-c32-run1

- scenario: A-scaling
- total time: 401.7 sec (seed phase 82.4 sec + crawl phase 319.3 sec)
- URLs: 31000 / expected 31000 (coverage 100.0%)
- throughput: 77.17 URLs/sec (77.27 requests/sec), crawl phase only 97.08 URLs/sec
- requests: pages 31040, robots 8854, sitemap 1000, undefined 0
- error rate: URL 0.1% (40 URLs), request 0.3%, retried URLs 40
- unexpected failures: 0
- server latency: p50 11 ms, p95 22 ms, max 2006 ms
- mock overhead: p50 1.4 ms, p95 10.8 ms
- Crawl-delay: 0 violations in 995 domains (tolerance 50 ms, min margin +0.032 sec, excluded 5 domains, retry pairs 40)
- resources: CPU avg 52% / max 77%, process CPU (1 core=100%) avg 50% / max 72%, RSS max 110 MB (8 processes: UniversityComparison-worker-1__etl_resource_log_20261018_085549_b0ccce1e506e, UniversityComparison-worker-1__etl_resource_log_20261018_085550_38ead0e827f9, UniversityComparison-worker-2__etl_resource_log_20261018_085555_ca6c50a42543, UniversityComparison-worker-2__etl_resource_log_20261018_085555_dbf1d360e4d1, UniversityComparison-worker-3__etl_resource_log_20261018_085558_88b6a894b0e6, UniversityComparison-worker-3__etl_resource_log_20261018_085558_efecc7c965e5, UniversityComparison-worker-4__etl_resource_log_20261018_085553_91edcdf33fef, UniversityComparison-worker-4__etl_resource_log_20261018_085553_bcb0b154e767)

## Workers

| worker | page requests | share |
| --- | ---: | ---: |
| 172.31.28.11 | 7828 | 25.2% |
| 172.31.18.72 | 7749 | 25.0% |
| 172.31.25.123 | 7746 | 25.0% |
| 172.31.27.171 | 7717 | 24.9% |

## Crawl-delay by required delay

| Architecture | Required Delay | Domains | Minimum Observed | Violations | Retry pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| A-distributed-x4-p2-c32-run1 | 1 | 995 | 1.032 | 0 | 40 |
| A-distributed-x4-p2-c32-run1 | excluded (robots.txt not 200) | 5 | - | - | - |

※ p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。クライアント側から見た応答時間ではない（R2.5）。

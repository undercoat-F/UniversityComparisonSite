# A-distributed-x1-p2-c32-run1

- scenario: A-scaling
- total time: 997.6 sec (seed phase 78.2 sec + crawl phase 919.3 sec)
- URLs: 31000 / expected 31000 (coverage 100.0%)
- throughput: 31.08 URLs/sec (31.12 requests/sec), crawl phase only 33.72 URLs/sec
- requests: pages 31040, robots 4412, sitemap 1000, undefined 0
- error rate: URL 0.1% (40 URLs), request 0.3%, retried URLs 40
- unexpected failures: 0
- server latency: p50 11 ms, p95 14 ms, max 2003 ms
- mock overhead: p50 1.3 ms, p95 3.7 ms
- Crawl-delay: 0 violations in 995 domains (tolerance 50 ms, min margin +0.039 sec, excluded 5 domains, retry pairs 40)
- resources: CPU avg 65% / max 76%, process CPU (1 core=100%) avg 61% / max 72%, RSS max 116 MB (2 processes: UniversityComparison-worker-1__etl_resource_log_20261018_075516_3df657ffb7a7, UniversityComparison-worker-1__etl_resource_log_20261018_075517_d41b4af965e5)

## Workers

| worker | page requests | share |
| --- | ---: | ---: |
| 172.31.27.171 | 31040 | 100.0% |

## Crawl-delay by required delay

| Architecture | Required Delay | Domains | Minimum Observed | Violations | Retry pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| A-distributed-x1-p2-c32-run1 | 1 | 995 | 1.039 | 0 | 40 |
| A-distributed-x1-p2-c32-run1 | excluded (robots.txt not 200) | 5 | - | - | - |

※ p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。クライアント側から見た応答時間ではない（R2.5）。

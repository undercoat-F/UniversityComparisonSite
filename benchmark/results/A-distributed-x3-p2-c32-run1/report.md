# A-distributed-x3-p2-c32-run1

- scenario: A-scaling
- total time: 462.1 sec (seed phase 86.3 sec + crawl phase 375.8 sec)
- URLs: 31000 / expected 31000 (coverage 100.0%)
- throughput: 67.08 URLs/sec (67.17 requests/sec), crawl phase only 82.49 URLs/sec
- requests: pages 31040, robots 6871, sitemap 1000, undefined 0
- error rate: URL 0.1% (40 URLs), request 0.3%, retried URLs 40
- unexpected failures: 0
- server latency: p50 12 ms, p95 30 ms, max 2058 ms
- mock overhead: p50 1.5 ms, p95 18.5 ms
- Crawl-delay: 0 violations in 995 domains (tolerance 50 ms, min margin +0.032 sec, excluded 5 domains, retry pairs 40)
- resources: CPU avg 55% / max 74%, process CPU (1 core=100%) avg 52% / max 72%, RSS max 124 MB (6 processes: UniversityComparison-worker-1__etl_resource_log_20261018_082428_4d983e975026, UniversityComparison-worker-1__etl_resource_log_20261018_082428_d35b889d009d, UniversityComparison-worker-2__etl_resource_log_20261018_082425_bb86a555f414, UniversityComparison-worker-2__etl_resource_log_20261018_082426_66ef426dfe51, UniversityComparison-worker-3__etl_resource_log_20261018_082425_8e525659e35a, UniversityComparison-worker-3__etl_resource_log_20261018_082425_e00b2796bf65)

## Workers

| worker | page requests | share |
| --- | ---: | ---: |
| 172.31.25.123 | 10506 | 33.8% |
| 172.31.27.171 | 10379 | 33.4% |
| 172.31.18.72 | 10155 | 32.7% |

## Crawl-delay by required delay

| Architecture | Required Delay | Domains | Minimum Observed | Violations | Retry pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| A-distributed-x3-p2-c32-run1 | 1 | 995 | 1.032 | 0 | 40 |
| A-distributed-x3-p2-c32-run1 | excluded (robots.txt not 200) | 5 | - | - | - |

※ p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。クライアント側から見た応答時間ではない（R2.5）。

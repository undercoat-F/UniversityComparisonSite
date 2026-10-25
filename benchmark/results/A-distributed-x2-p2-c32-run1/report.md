# A-distributed-x2-p2-c32-run1

- scenario: A-scaling
- total time: 533.5 sec (seed phase 56.7 sec + crawl phase 476.8 sec)
- URLs: 31000 / expected 31000 (coverage 100.0%)
- throughput: 58.11 URLs/sec (58.18 requests/sec), crawl phase only 65.01 URLs/sec
- requests: pages 31040, robots 5188, sitemap 1000, undefined 0
- error rate: URL 0.1% (40 URLs), request 0.3%, retried URLs 40
- unexpected failures: 0
- server latency: p50 11 ms, p95 12 ms, max 2003 ms
- mock overhead: p50 1.1 ms, p95 2.1 ms
- Crawl-delay: 0 violations in 995 domains (tolerance 50 ms, min margin +0.031 sec, excluded 5 domains, retry pairs 40)
- resources: CPU avg 64% / max 79%, process CPU (1 core=100%) avg 61% / max 73%, RSS max 111 MB (4 processes: UniversityComparison-worker-1__etl_resource_log_20261017_132934_659708fa8a65, UniversityComparison-worker-1__etl_resource_log_20261017_132936_0b47180a9d12, UniversityComparison-worker-2__etl_resource_log_20261017_132937_3241f1d9d44e, UniversityComparison-worker-2__etl_resource_log_20261017_132937_4b1832472eb8)

## Workers

| worker | page requests | share |
| --- | ---: | ---: |
| 172.31.27.171 | 15566 | 50.1% |
| 172.31.18.72 | 15474 | 49.9% |

## Crawl-delay by required delay

| Architecture | Required Delay | Domains | Minimum Observed | Violations | Retry pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| A-distributed-x2-p2-c32-run1 | 1 | 995 | 1.031 | 0 | 40 |
| A-distributed-x2-p2-c32-run1 | excluded (robots.txt not 200) | 5 | - | - | - |

※ p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。クライアント側から見た応答時間ではない（R2.5）。

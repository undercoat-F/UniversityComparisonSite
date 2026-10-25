# C-distributed-x1-c16-run1

- scenario: C-capacity
- total time: 727.0 sec (seed phase 20.4 sec + crawl phase 706.6 sec)
- URLs: 10500 / expected 10500 (coverage 100.0%)
- throughput: 14.44 URLs/sec (14.46 requests/sec)
- requests: pages 10515, robots 1000, sitemap 500, undefined 0
- error rate: URL 0.1% (15 URLs), request 0.3%, retried URLs 15
- unexpected failures: 0
- server latency: p50 11 ms, p95 12 ms, max 2004 ms
- mock overhead: p50 0.7 ms, p95 1.9 ms
- Crawl-delay: 0 violations in 497 domains (tolerance 50 ms, min margin +0.032 sec, excluded 3 domains, retry pairs 15)
- resources: CPU avg 21% / max 39%, RSS max 102 MB (1 hosts: UniversityComparison-worker-1)

## Workers

| worker | page requests | share |
| --- | ---: | ---: |
| 172.31.27.171 | 10515 | 100.0% |

## Crawl-delay by required delay

| Architecture | Required Delay | Domains | Minimum Observed | Violations | Retry pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| C-distributed-x1-c16-run1 | 1 | 497 | 1.032 | 0 | 15 |
| C-distributed-x1-c16-run1 | excluded (robots.txt not 200) | 3 | - | - | - |

※ p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。クライアント側から見た応答時間ではない（R2.5）。

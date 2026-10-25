# C-distributed-x1-c64-run1

- scenario: C-capacity
- total time: 548.6 sec (seed phase 29.5 sec + crawl phase 519.0 sec)
- URLs: 10500 / expected 10500 (coverage 100.0%)
- throughput: 19.14 URLs/sec (19.17 requests/sec)
- requests: pages 10515, robots 1000, sitemap 500, undefined 0
- error rate: URL 0.1% (15 URLs), request 0.3%, retried URLs 15
- unexpected failures: 0
- server latency: p50 11 ms, p95 14 ms, max 2003 ms
- mock overhead: p50 1.3 ms, p95 3.5 ms
- Crawl-delay: 0 violations in 497 domains (tolerance 50 ms, min margin +0.044 sec, excluded 3 domains, retry pairs 15)
- resources: CPU avg 30% / max 41%, RSS max 124 MB (1 hosts: UniversityComparison-worker-1)

## Workers

| worker | page requests | share |
| --- | ---: | ---: |
| 172.31.27.171 | 10515 | 100.0% |

## Crawl-delay by required delay

| Architecture | Required Delay | Domains | Minimum Observed | Violations | Retry pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| C-distributed-x1-c64-run1 | 1 | 497 | 1.044 | 0 | 15 |
| C-distributed-x1-c64-run1 | excluded (robots.txt not 200) | 3 | - | - | - |

※ p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。クライアント側から見た応答時間ではない（R2.5）。

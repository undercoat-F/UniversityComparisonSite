# B-legacy-x1-run1

- scenario: B-crawl-delay
- total time: 90.2 sec (seed phase 0.1 sec + crawl phase 90.0 sec)
- URLs: 93 / expected 93 (coverage 100.0%)
- throughput: 1.03 URLs/sec (1.03 requests/sec)
- requests: pages 93, robots 6, sitemap 3, undefined 0
- error rate: URL 0.0% (0 URLs), request 0.0%, retried URLs 0
- unexpected failures: 0
- server latency: p50 11 ms, p95 12 ms, max 15 ms
- mock overhead: p50 0.8 ms, p95 2.5 ms
- Crawl-delay: 0 violations in 3 domains (tolerance 50 ms, min margin -0.043 sec, excluded 0 domains, retry pairs 0)
- resources: CPU avg 1% / max 4%, RSS max 58 MB (1 hosts: UniversityComparison-legacy)

## Workers

| worker | page requests | share |
| --- | ---: | ---: |
| 172.31.27.67 | 93 | 100.0% |

## Crawl-delay by required delay

| Architecture | Required Delay | Domains | Minimum Observed | Violations | Retry pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| B-legacy-x1-run1 | 1 | 1 | 0.992 | 0 | 0 |
| B-legacy-x1-run1 | 2 | 1 | 1.990 | 0 | 0 |
| B-legacy-x1-run1 | 3 | 1 | 2.957 | 0 | 0 |

※ p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。クライアント側から見た応答時間ではない（R2.5）。

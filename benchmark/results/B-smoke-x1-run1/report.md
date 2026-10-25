# B-smoke-x1-run1

- scenario: B-crawl-delay
- total time: 170.8 sec
- URLs: 93 / expected 93 (coverage 100.0%)
- throughput: 0.54 URLs/sec (0.54 requests/sec)
- requests: pages 93, robots 6, sitemap 3, undefined 4
- error rate: URL 0.0% (0 URLs), request 0.0%, retried URLs 0
- unexpected failures: 0
- server latency: p50 11 ms, p95 11 ms, max 14 ms
- mock overhead: p50 0.7 ms, p95 0.9 ms
- Crawl-delay: 0 violations in 3 domains (tolerance 50 ms, min margin -0.043 sec, excluded 0 domains, retry pairs 0)

## Workers

| worker | page requests | share |
| --- | ---: | ---: |
| 172.31.27.171 | 93 | 100.0% |

## Crawl-delay by required delay

| Architecture | Required Delay | Domains | Minimum Observed | Violations | Retry pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| B-smoke-x1-run1 | 1 | 1 | 0.957 | 0 | 0 |
| B-smoke-x1-run1 | 2 | 1 | 1.993 | 0 | 0 |
| B-smoke-x1-run1 | 3 | 1 | 2.960 | 0 | 0 |

## Warnings

- 4 requests to hosts/paths not defined in the scenario

※ p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。クライアント側から見た応答時間ではない（R2.5）。

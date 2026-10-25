# A-distributed-x1-run1

- scenario: A-scaling
- total time: 1611.2 sec (seed phase 299.9 sec + crawl phase 1311.3 sec)
- URLs: 5099 / expected 5099 (coverage 100.0%)
- throughput: 3.16 URLs/sec (3.18 requests/sec)
- requests: pages 5123, robots 200, sitemap 100, undefined 0
- error rate: URL 0.5% (24 URLs), request 0.9%, retried URLs 24
- unexpected failures: 0
- server latency: p50 11 ms, p95 12 ms, max 2003 ms
- mock overhead: p50 0.7 ms, p95 1.3 ms
- Crawl-delay: 1 violations in 99 domains (tolerance 50 ms, min margin -0.084 sec, excluded 1 domains, retry pairs 24)
- resources: CPU avg 5% / max 21%, RSS max 86 MB (1 hosts: UniversityComparison-worker-1)

## Workers

| worker | page requests | share |
| --- | ---: | ---: |
| 172.31.27.171 | 5123 | 100.0% |

## Crawl-delay by required delay

| Architecture | Required Delay | Domains | Minimum Observed | Violations | Retry pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| A-distributed-x1-run1 | 1 | 99 | 0.916 | 1 | 24 |
| A-distributed-x1-run1 | excluded (robots.txt not 200) | 1 | - | - | - |

## Domains with violations

| host | required | min interval | violations | pairs | clients |
| --- | ---: | ---: | ---: | ---: | ---: |
| d009.bench.internal | 1 | 0.916 | 1 | 50 | 1 |

## Warnings

- 1 Crawl-delay violations in 1 domains

※ p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。クライアント側から見た応答時間ではない（R2.5）。

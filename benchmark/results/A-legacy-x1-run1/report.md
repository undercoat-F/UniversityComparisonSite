# A-legacy-x1-run1

- scenario: A-scaling
- total time: 1460.4 sec (seed phase 27.6 sec + crawl phase 1432.8 sec)
- URLs: 30994 / expected 31000 (coverage 100.0%)
- throughput: 21.22 URLs/sec (21.25 requests/sec), crawl phase only 21.63 URLs/sec
- requests: pages 31034, robots 2000, sitemap 1000, undefined 0
- error rate: URL 0.1% (40 URLs), request 0.3%, retried URLs 40
- unexpected failures: 0
- server latency: p50 11 ms, p95 13 ms, max 2003 ms
- mock overhead: p50 1.2 ms, p95 2.9 ms
- Crawl-delay: 24 violations in 995 domains (tolerance 50 ms, min margin -0.337 sec, excluded 5 domains, retry pairs 40)
- resources: CPU avg 25% / max 97%, process CPU (1 core=100%) avg 48% / max 184%, RSS max 78 MB (1 processes: UniversityComparison-legacy__etl_resource_log_20261018_100012)

## Workers

| worker | page requests | share |
| --- | ---: | ---: |
| 172.31.27.67 | 31034 | 100.0% |

## Crawl-delay by required delay

| Architecture | Required Delay | Domains | Minimum Observed | Violations | Retry pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| A-legacy-x1-run1 | 1 | 995 | 0.663 | 24 | 40 |
| A-legacy-x1-run1 | excluded (robots.txt not 200) | 5 | - | - | - |

## Domains with violations

| host | required | min interval | violations | pairs | clients |
| --- | ---: | ---: | ---: | ---: | ---: |
| d0001.bench.internal | 1 | 0.897 | 1 | 30 | 1 |
| d0060.bench.internal | 1 | 0.897 | 1 | 30 | 1 |
| d0447.bench.internal | 1 | 0.924 | 1 | 30 | 1 |
| d0448.bench.internal | 1 | 0.923 | 1 | 30 | 1 |
| d0680.bench.internal | 1 | 0.673 | 1 | 30 | 1 |
| d0681.bench.internal | 1 | 0.673 | 1 | 30 | 1 |
| d0682.bench.internal | 1 | 0.663 | 1 | 30 | 1 |
| d0683.bench.internal | 1 | 0.663 | 1 | 30 | 1 |
| d0685.bench.internal | 1 | 0.942 | 1 | 30 | 1 |
| d0686.bench.internal | 1 | 0.900 | 1 | 30 | 1 |
| d0687.bench.internal | 1 | 0.921 | 1 | 30 | 1 |
| d0688.bench.internal | 1 | 0.877 | 1 | 30 | 1 |
| d0739.bench.internal | 1 | 0.866 | 1 | 30 | 1 |
| d0741.bench.internal | 1 | 0.930 | 1 | 30 | 1 |
| d0742.bench.internal | 1 | 0.931 | 1 | 30 | 1 |
| d0743.bench.internal | 1 | 0.931 | 1 | 30 | 1 |
| d0744.bench.internal | 1 | 0.930 | 1 | 30 | 1 |
| d0829.bench.internal | 1 | 0.832 | 1 | 30 | 1 |
| d0830.bench.internal | 1 | 0.801 | 1 | 30 | 1 |
| d0831.bench.internal | 1 | 0.824 | 1 | 30 | 1 |
| d0960.bench.internal | 1 | 0.943 | 1 | 30 | 1 |
| d0961.bench.internal | 1 | 0.928 | 1 | 30 | 1 |
| d0962.bench.internal | 1 | 0.900 | 1 | 30 | 1 |
| d0963.bench.internal | 1 | 0.853 | 1 | 30 | 1 |

## Warnings

- coverage 100.0%: 6 reachable URLs were not fetched
- 24 Crawl-delay violations in 24 domains

※ p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。クライアント側から見た応答時間ではない（R2.5）。

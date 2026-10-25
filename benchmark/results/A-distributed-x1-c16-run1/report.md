# A-distributed-x1-c16-run1

- scenario: A-scaling
- total time: 375.0 sec (seed phase 12.0 sec + crawl phase 363.0 sec)
- URLs: 5099 / expected 5099 (coverage 100.0%)
- throughput: 13.60 URLs/sec (13.66 requests/sec)
- requests: pages 5123, robots 200, sitemap 100, undefined 0
- error rate: URL 0.5% (24 URLs), request 0.9%, retried URLs 24
- unexpected failures: 0
- server latency: p50 11 ms, p95 12 ms, max 2003 ms
- mock overhead: p50 0.8 ms, p95 2.0 ms
- Crawl-delay: 82 violations in 99 domains (tolerance 50 ms, min margin -0.668 sec, excluded 1 domains, retry pairs 24)
- resources: - (resources/*.csv なし。python -m benchmark.collect_resources で回収する)

## Workers

| worker | page requests | share |
| --- | ---: | ---: |
| 172.31.27.171 | 5123 | 100.0% |

## Crawl-delay by required delay

| Architecture | Required Delay | Domains | Minimum Observed | Violations | Retry pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| A-distributed-x1-c16-run1 | 1 | 99 | 0.332 | 82 | 24 |
| A-distributed-x1-c16-run1 | excluded (robots.txt not 200) | 1 | - | - | - |

## Domains with violations

| host | required | min interval | violations | pairs | clients |
| --- | ---: | ---: | ---: | ---: | ---: |
| d006.bench.internal | 1 | 0.917 | 1 | 50 | 1 |
| d007.bench.internal | 1 | 0.933 | 1 | 50 | 1 |
| d012.bench.internal | 1 | 0.934 | 1 | 50 | 1 |
| d013.bench.internal | 1 | 0.932 | 1 | 50 | 1 |
| d014.bench.internal | 1 | 0.917 | 1 | 50 | 1 |
| d017.bench.internal | 1 | 0.867 | 3 | 50 | 1 |
| d018.bench.internal | 1 | 0.896 | 1 | 50 | 1 |
| d019.bench.internal | 1 | 0.917 | 1 | 50 | 1 |
| d020.bench.internal | 1 | 0.931 | 1 | 50 | 1 |
| d021.bench.internal | 1 | 0.900 | 1 | 50 | 1 |
| d022.bench.internal | 1 | 0.919 | 1 | 50 | 1 |
| d023.bench.internal | 1 | 0.332 | 1 | 50 | 1 |
| d024.bench.internal | 1 | 0.599 | 1 | 50 | 1 |
| d025.bench.internal | 1 | 0.721 | 1 | 50 | 1 |
| d026.bench.internal | 1 | 0.779 | 2 | 50 | 1 |
| d028.bench.internal | 1 | 0.781 | 2 | 50 | 1 |
| d029.bench.internal | 1 | 0.903 | 1 | 50 | 1 |
| d030.bench.internal | 1 | 0.917 | 2 | 50 | 1 |
| d032.bench.internal | 1 | 0.772 | 1 | 50 | 1 |
| d033.bench.internal | 1 | 0.780 | 1 | 50 | 1 |
| d034.bench.internal | 1 | 0.822 | 1 | 50 | 1 |
| d035.bench.internal | 1 | 0.917 | 2 | 50 | 1 |
| d038.bench.internal | 1 | 0.922 | 2 | 50 | 1 |
| d040.bench.internal | 1 | 0.872 | 2 | 50 | 1 |
| d041.bench.internal | 1 | 0.907 | 2 | 50 | 1 |
| d042.bench.internal | 1 | 0.943 | 2 | 50 | 1 |
| d043.bench.internal | 1 | 0.874 | 1 | 50 | 1 |
| d044.bench.internal | 1 | 0.816 | 2 | 50 | 1 |
| d045.bench.internal | 1 | 0.917 | 2 | 50 | 1 |
| d046.bench.internal | 1 | 0.935 | 1 | 50 | 1 |
| d047.bench.internal | 1 | 0.809 | 4 | 50 | 1 |
| d048.bench.internal | 1 | 0.844 | 4 | 50 | 1 |
| d049.bench.internal | 1 | 0.933 | 1 | 50 | 1 |
| d050.bench.internal | 1 | 0.940 | 1 | 50 | 1 |
| d051.bench.internal | 1 | 0.914 | 2 | 50 | 1 |
| d052.bench.internal | 1 | 0.797 | 2 | 50 | 1 |
| d053.bench.internal | 1 | 0.845 | 2 | 50 | 1 |
| d054.bench.internal | 1 | 0.783 | 2 | 50 | 1 |
| d055.bench.internal | 1 | 0.547 | 1 | 50 | 1 |
| d058.bench.internal | 1 | 0.919 | 1 | 50 | 1 |
| d059.bench.internal | 1 | 0.872 | 1 | 50 | 1 |
| d060.bench.internal | 1 | 0.906 | 3 | 50 | 1 |
| d061.bench.internal | 1 | 0.857 | 2 | 50 | 1 |
| d064.bench.internal | 1 | 0.941 | 1 | 50 | 1 |
| d066.bench.internal | 1 | 0.897 | 1 | 50 | 1 |
| d075.bench.internal | 1 | 0.900 | 1 | 50 | 1 |
| d082.bench.internal | 1 | 0.932 | 1 | 50 | 1 |
| d087.bench.internal | 1 | 0.925 | 1 | 50 | 1 |
| d090.bench.internal | 1 | 0.769 | 1 | 50 | 1 |
| d092.bench.internal | 1 | 0.890 | 1 | 50 | 1 |
| d095.bench.internal | 1 | 0.941 | 1 | 50 | 1 |
| d096.bench.internal | 1 | 0.559 | 1 | 50 | 1 |
| d097.bench.internal | 1 | 0.640 | 2 | 50 | 1 |
| d099.bench.internal | 1 | 0.866 | 3 | 50 | 1 |

## Warnings

- 82 Crawl-delay violations in 54 domains

※ p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。クライアント側から見た応答時間ではない（R2.5）。

# Benchmark comparison

baseline: A-legacy-x1 / 同一構成の複数回計測は中央値（Violations と Unexpected failures は合計）

## Performance

| Architecture | Runs | Workers | URLs | Coverage | Total Time (s) | URLs/sec | Crawl URLs/sec | vs baseline | Scale eff. | p50 (ms) | p95 (ms) | Error Rate | Unexpected failures | Crawl-delay violations | CPU avg | RSS max (MB) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A-legacy-x1 | 1 | 1 | 30994 | 100.0% | 1460.4 | 21.22 | 21.63 | 1.00x | 100% | 11 | 13 | 0.1% | 0 | 24 | 25% | 78 |
| A-distributed-x1-p2-c32 | 1 | 1 | 31000 | 100.0% | 997.6 | 31.08 | 33.72 | 1.46x | 146% | 11 | 14 | 0.1% | 0 | 0 | 65% | 116 |
| A-distributed-x2-p2-c32 | 1 | 2 | 31000 | 100.0% | 533.5 | 58.11 | 65.01 | 2.74x | 137% | 11 | 12 | 0.1% | 0 | 0 | 64% | 111 |
| A-distributed-x3-p2-c32 | 1 | 3 | 31000 | 100.0% | 462.1 | 67.08 | 82.49 | 3.16x | 105% | 12 | 30 | 0.1% | 0 | 0 | 55% | 124 |
| A-distributed-x4-p2-c32 | 1 | 4 | 31000 | 100.0% | 401.7 | 77.17 | 97.08 | 3.64x | 91% | 11 | 22 | 0.1% | 0 | 0 | 52% | 110 |

- vs baseline: URLs/sec の baseline 比。Scale eff.: vs baseline ÷ Worker数の比（100% で線形スケール）
- Crawl URLs/sec: 準備（robots.txt・sitemap の取得〜最初のページ取得）を除いたクロールの時間だけで割った値
- p50/p95 は Mock Server 側の処理時間（受信〜送信完了、意図的な遅延を含む）。クライアント側から見た応答時間ではない（R2.5）。
- CPU avg: 計測期間中のインスタンス全体の CPU 使用率の平均（複数台は台ごとの平均の平均）。RSS max: クローラーのプロセスのメモリ使用量の最大（複数台は最大の台）。resources/*.csv がない計測は -

## Crawl-delay

| Architecture | Required Delay | Domains | Minimum Observed | Violations | Retry pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| A-legacy-x1 | 1 | 995 | 0.663 | 24 | 40 |
| A-legacy-x1 | excluded (robots.txt not 200) | 5 | - | - | - |
| A-distributed-x1-p2-c32 | 1 | 995 | 1.039 | 0 | 40 |
| A-distributed-x1-p2-c32 | excluded (robots.txt not 200) | 5 | - | - | - |
| A-distributed-x2-p2-c32 | 1 | 995 | 1.031 | 0 | 40 |
| A-distributed-x2-p2-c32 | excluded (robots.txt not 200) | 5 | - | - | - |
| A-distributed-x3-p2-c32 | 1 | 995 | 1.032 | 0 | 40 |
| A-distributed-x3-p2-c32 | excluded (robots.txt not 200) | 5 | - | - | - |
| A-distributed-x4-p2-c32 | 1 | 995 | 1.032 | 0 | 40 |
| A-distributed-x4-p2-c32 | excluded (robots.txt not 200) | 5 | - | - | - |

## Warnings

- A-legacy-x1-run1: coverage 100.0%: 6 reachable URLs were not fetched
- A-legacy-x1-run1: 24 Crawl-delay violations in 24 domains

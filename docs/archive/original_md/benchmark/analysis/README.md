# Benchmark Analysis

Mock Server のアクセスログ（`benchmark/results/<run-label>/access.jsonl`）から性能指標を集計する。
設計は `benchmark/benchmark_concept_revisions.md`（R2.4, R4）を参照。コマンドはリポジトリのルートで実行する。

## run-label の付け方

`<試験>-<構成>-run<N>` とする（例：`A-legacy-run1`, `A-distributed-x2-run3`）。
`compare` は末尾の `-run<N>` を除いたラベルを「構成」とみなし、同一構成の複数回計測を中央値でまとめる。

## Worker の対応表（任意）

run ディレクトリに `workers.csv` を置くと、送信元IPの代わりに worker_id で表示する。

```csv
client_ip,worker_id
10.0.1.11,worker-1
10.0.1.12,worker-2
```

## 1回分の集計

```bash
python -m benchmark.analysis.report run benchmark/results/A-distributed-x2-run1
```

run ディレクトリに以下を出力する。

| ファイル | 内容 |
|---|---|
| `summary.json` | 全指標（下表） |
| `crawl_delay.csv` | ドメインごとの Crawl-delay 遵守状況 |
| `report.md` | 上記の要約（標準出力にも表示） |

## 複数の計測の比較

```bash
python -m benchmark.analysis.report compare benchmark/results/A-* \
  --baseline A-distributed-x1 --out benchmark/results/A-compare.md
```

`benchmark_concept.md` 11.1（Performance）と 11.2（Crawl-delay）に相当する表を出力する。
CPU / Memory の列は CloudWatch 等の値を手で記入する。

## 指標の定義

| 指標 | 定義 |
|---|---|
| Total Time | シナリオ上のドメインへの最初のリクエスト受信 〜 最後の応答送信完了 |
| URLs | 取得されたページURLの数（同一URLへの再取得は1件） |
| Coverage | 取得されたURL ÷ シナリオ上到達可能なURL（`pages.csv` の reachable）。100% 未満の計測は比較に使わない |
| URLs/sec | URLs ÷ Total Time（主要指標） |
| vs baseline / Scale eff. | baseline 構成との URLs/sec の比 / それを Worker 数の比で割った値（100% で線形） |
| Error Rate | URL単位：最後の応答が 2xx 以外のURLの割合。リクエスト単位の値は `summary.json` の `request_error_rate` |
| Unexpected failures | シナリオ上 200 を返すはずなのに失敗したURL（クライアント側タイムアウトなど）。0 であるべき |
| p50 / p95 | Mock Server 側の処理時間（意図的な遅延を含む）。クライアント側の応答時間ではない |
| mock overhead | 処理時間 − 意図的な遅延。p95 が 50ms を超えると Mock Server がボトルネックの疑いとして警告する |
| Crawl-delay violations | 同一ドメインへの連続する取得の受信間隔 < Crawl-delay − 許容誤差（既定 50ms, `--tolerance`）の件数 |

Crawl-delay 判定の扱い（R4.2〜R4.4）:

- 同一URLへの連続リクエスト（httpx → requests のフォールバック再取得）は1回の取得とみなし、間隔は各URLの最初のリクエストどうしで測る。再取得は `Retry pairs` として別に数える
- robots.txt が 200 でないドメインは、クローラーが Crawl-delay を取得できないため判定対象外とする

# 計測用環境の構成

試験A〜C（2026-10）で使った計測用の AWS 構成。2026-10-18 に片付け済み（R12）。

```
                 東京リージョン（ap-northeast-1）・同じ VPC                          シンガポール
┌────────────────────────────────────────────────────────────┐   ┌──────────────────┐
│ Control Plane（t3.micro）  producer: seed を読み、           │   │ Neon             │
│   robots.txt・sitemap を取得して SQS に投入 ──┐             │   │  bench-base（原本）│
│                                              ▼             │   │   └ bench-run    │
│              SQS crawl-tasks.fifo  ◀──▶  Worker 1〜4         │──▶│  （計測ごとに    │
│              Valkey（重複排除）    ◀──▶  （t3.micro、各2プロセス）│   │    リセット）    │
│                                              │ HTTPS        │   └──────────────────┘
│ Legacy（t3.micro）1台ですべて処理 ───────────┤              │
│                                              ▼             │
│ Route 53 プライベートゾーン *.bench.internal → Mock          │
│ Mock（t3.medium + EBS 100GB）1台で 1,000 ドメインになりすまし │
│   └ access.jsonl ＝ 計測結果の元データ                       │
└────────────────────────────────────────────────────────────┘
```

## 構成要素

| 構成要素 | 種類 | 役割 | 計測時の使い方 |
|---|---|---|---|
| Mock | t3.medium、EBS 100 GB（`/var/lib/benchmark`） | 架空の大学サイト。`d0001〜d1000.bench.internal` をすべて1台で返す | `bench-mock` サービス（`~/bench-mock`）を計測ごとに run-label を変えて起動し直す。受けたアクセスを `access.jsonl` に記録する |
| Route 53（プライベートゾーン） | DNS | `*.bench.internal` を Mock の IP に向ける | VPC の中でだけ有効 |
| 独自の CA | 証明書 | Mock の HTTPS をクローラーに信頼させる | `python -m benchmark.distribute_certs` で各 EC2 の `/etc/benchmark/ca-bundle.pem` に配り、`docker-compose.bench.yml` でコンテナに読み込ませる |
| Control Plane | t3.micro | producer。seed を読み、robots.txt・sitemap を取得して SQS にタスクを入れる | `bench run --rm controlplane` で1回実行すると終わる（常駐しない） |
| Worker 1〜4 | t3.micro（2 vCPU / 1 GB） | SQS からタスクを受け取りクロールし、Valkey で重複を除き、Neon に保存する | `~/bench-app`。1台あたり `--scale worker=2`（2プロセス）、窓口 `WORKER_MAX_CONCURRENCY=32` |
| Legacy | t3.micro | 旧方式。1台で seed の読み込み〜クロール〜保存まで行う | `~/legacy-app`（ブランチ `benchmark/legacy`）。SQS・Valkey は使わない |
| SQS `crawl-tasks.fifo` | FIFO キュー | タスクを Control Plane から Worker へ渡す | **本番と共用** |
| Valkey（ElastiCache Serverless） | キャッシュ | 処理済み URL の重複排除 | **本番と共用** |
| Neon `bench-base` → `bench-run` | DB（ap-southeast-1） | seed（`observer.bench_seed_urls`）の読み込み、抽出結果の保存 | 計測ごとに `python -m benchmark.neon_branch reset` で bench-run を bench-base の状態に戻す |

## 注意点

1. **SQS と Valkey は本番と共用**。計測前に `reset_state` で片付ける。計測中に本番の Worker が動いていると計測用のタスクを横取りするため、本番のコンテナは止めておく
2. **`--scale worker=2` は1台の中のプロセス数**。台数（x1〜x4）は、起動コマンドを実行する EC2 の数で決まる。使わない Worker は `bench ps` で何も動いていないことを確認する
3. **`LABEL` はシェルごとに設定する**。Mock の EC2 で設定しても手元には引き継がれない。手元で回収する前に `echo $LABEL` で確認する
4. **計測結果の元データは Mock 側のアクセスログ**。Throughput・取りこぼし・Crawl-delay 違反は Mock の受信時刻から計算する。クローラーの EC2 から回収するのは CPU・メモリの記録（`resources/`）だけ
5. **Neon は AWS の外（シンガポール）**。東京の EC2 から1往復 70〜80 ms かかるため、1件ずつの書き込みは遅い（runbook_A 付録の「準備の約 300 秒」の原因）
6. **Control Plane は常駐しない**。producer を1回実行して終わる。Worker が常駐してタスクを待つ
7. **t3.micro の 2 vCPU は独立した2コアではない可能性が高い**。1台2プロセスは1プロセスの 1.55 倍にとどまった（R12、未検証）
8. t3 は全台 CPU クレジット `unlimited`（使い切っても遅くならない）。計測中もクレジット残高は上限付近で、計測への影響はなかった

## 再構築・片付け

`terraform/terraform.tfvars` の値で切り替える（本番と同じ Terraform の構成のため、`terraform destroy` は使わない）。

| 変数 | 計測時 | 通常時 |
|---|---|---|
| `worker_count` | 4 | 2 |
| `benchmark_enabled`（Mock・EBS・Route 53・計測用セキュリティグループ） | true | false |
| `benchmark_legacy_enabled`（Legacy） | true | false |

- 再構築したら `runbook_A.md` の1章から準備し直す（Mock のコードとシナリオの配置、CA の配布、`~/bench-app` の clone と `.env` の配置）
- 計測用の `.env` は手元の `.env.bench.controlplane` / `.env.bench.worker`（gitignore 済み、Neon の API キー・DB のパスワードを含む）。Legacy 用は残っていないため、`runbook_legacy_B.md` に従って作り直す
- `benchmark_enabled=false` で apply すると Mock の EBS（計測結果）も削除される。片付け前に結果を手元に回収する（2026-10-18 分は `benchmark/results/_mock_ebs_backup_20261018.tgz`）
- 片付け後も Control Plane・Worker 1・2 には計測用の `~/bench-app` が残る。本番を起動する前に、計測用のコンテナ（`bench` プロジェクト）が止まっていることを確認する

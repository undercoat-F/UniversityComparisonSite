# 試験A（スケーリング）手順書

目的: 100 ドメイン（5,100 ページ、Crawl-delay 1 秒、理論上限 100 URLs/sec）で、Legacy と分散版 1〜4 台を同一条件で計測し、台数による Throughput の変化をグラフにする。

| run-label | 構成 | 見込み時間 |
|---|---|---|
| `A-legacy-x1-run1` | Legacy | 数分 |
| `A-distributed-x1-run1` | 分散版 Worker 1台 | 20 分前後 |
| `A-distributed-x2-run1` | 分散版 Worker 2台 | |
| `A-distributed-x3-run1` | 分散版 Worker 3台 | |
| `A-distributed-x4-run1` | 分散版 Worker 4台 | |

- 時間に余裕があれば、各構成の 2・3 回目（`-run2`, `-run3`）を取る。集計は自動で中央値にまとめる
- コマンドはすべて **Git Bash** で実行する（PowerShell はパイプで文字化けする）
- シナリオ: `benchmark/mock/scenarios/A_scaling.yaml`（100 ドメイン。失敗ドメインのタイムアウトは1ページ）。seed は bench-base に登録済み

---

## 1. 1回だけやる準備

### 1-1. Worker を 4 台にする（手元）

`terraform/terraform.tfvars` の `worker_count` を 4 にする。

```bash
terraform -chdir=terraform plan     # 「2 to add, 0 to change, 0 to destroy」（worker[2], worker[3]）であること
terraform -chdir=terraform apply
aws ec2 describe-instances --filters "Name=tag:Role,Values=worker" \
  --query "Reservations[].Instances[].[Tags[?Key=='Name']|[0].Value,State.Name,PublicIpAddress]" --output text
```

Worker 2 は停止中なので、AWS コンソールか `aws ec2 start-instances` で起動する（4台とも running にする）。

### 1-2. Worker 2〜4 の準備（各 Worker で）

```bash
cloud-init status --wait           # 新しい Worker（3・4）は Docker のインストール完了を待つ
sudo docker ps                     # 本番用の Worker コンテナが動いていたら止める:
                                   #   cd ~/UniversityComparisonSite && sudo docker compose --profile distributed stop worker
git clone -b sqs-distributed https://github.com/undercoat-F/UniversityComparisonSite.git ~/bench-app
```

`.env` は **Worker 1 の計測用のもの**（`PARENT_DB_OWNER_CONNECTION` が空）をそのままコピーする（手元から）:

```bash
W1=<Worker 1 の IP>
for ip in <Worker 2 の IP> <Worker 3 の IP> <Worker 4 の IP>; do
  scp -3 -i ~/UniversityComparisonKey.pem ubuntu@$W1:/home/ubuntu/bench-app/.env ubuntu@$ip:/home/ubuntu/bench-app/.env
done
python -m benchmark.distribute_certs --key ~/UniversityComparisonKey.pem            # 全 Worker に CA を配る
python -m benchmark.distribute_certs --key ~/UniversityComparisonKey.pem --check    # 全台 HTTP 404
```

### 1-3. コードを最新にする（Control Plane と全 Worker で）

```bash
cd ~/bench-app && git pull && git log --oneline -1
```

### 1-4. Legacy の設定を1つ変える（Legacy で）

Legacy は1ドメインを処理し始めてから `ETL_DOMAIN_MAX_SECONDS` 秒でそのドメインを打ち切る（本番は 300）。試験A では途中で打ち切られる可能性があり、分散版にはこの打ち切りがないため、計測では無効にする。

```bash
sed -i 's/^ETL_DOMAIN_MAX_SECONDS=.*/ETL_DOMAIN_MAX_SECONDS=0/' ~/legacy-app/.env
grep -E "^ETL_DOMAIN_MAX_SECONDS=" ~/legacy-app/.env     # =0 であること
```

### 1-5. 片付け用の URL 一覧を Control Plane に送る（手元）

```bash
ssh -i ~/UniversityComparisonKey.pem ubuntu@35.73.202.127 "mkdir -p ~/bench-app/benchmark/results"
scp -i ~/UniversityComparisonKey.pem -r benchmark/results/A-inspect ubuntu@35.73.202.127:~/bench-app/benchmark/results/
```

### 便利な定義（各マシンで、ターミナルを開くたびに）

```bash
# Control Plane・Worker（~/bench-app）
alias bench='sudo docker compose -p bench -f docker-compose.yml -f docker-compose.bench.yml --profile distributed'
```

---

## 2. 毎回の手順（分散版 N 台）

`LABEL=A-distributed-x<N>-run1` として進める。

### 2-1. 全 Worker の計測用コンテナを止める（全 Worker で）

```bash
cd ~/bench-app && bench stop worker
```

止めるのは、前回の計測の状態（処理済み URL、run_id）をメモリから消すため。止めるときに未書き込みの抽出結果も保存される。

### 2-2. 初期化（手元・Control Plane）

```bash
# 手元
python -m benchmark.neon_branch reset
python -m benchmark.reset_state --pages benchmark/results/A-inspect/pages.csv --skip-valkey --execute
```

```bash
# Control Plane
cd ~/bench-app
bench run --rm -v "$PWD/benchmark:/app/benchmark" controlplane \
  python -m benchmark.reset_state --pages /app/benchmark/results/A-inspect/pages.csv --skip-sqs --execute
```

### 2-3. Mock を新しい run-label で起動し直す（Mock で）

```bash
LABEL=A-distributed-x1-c8-run1     # 毎回変える
sudo systemctl stop bench-mock; sudo systemctl reset-failed bench-mock 2>/dev/null; cd ~/bench-mock
sudo systemd-run --unit bench-mock --working-directory "$HOME/bench-mock" "$HOME/bench-mock/.venv/bin/python" \
  -m benchmark.mock.server --scenario benchmark/mock/scenarios/A_scaling.yaml --run-label $LABEL \
  --results-dir /var/lib/benchmark/results --certfile /etc/benchmark/server.crt --keyfile /etc/benchmark/server.key
systemctl is-active bench-mock
```

### 2-4. 使う N 台の Worker を起動する（Worker 1〜N で）

```bash
cd ~/bench-app && bench up -d --build worker
bench logs worker | tail -3      # start ... max_concurrency=4 / waiting for messages...
```

使わない Worker（N+1 台目以降）は 2-1 で止めたままにする。

### 2-5. producer を実行する（Control Plane で）

```bash
cd ~/bench-app && bench run --rm --build controlplane     # targets=100 になること
```

### 2-6. 完了を待つ（手元）

```bash
python -m benchmark.wait_done --key ~/UniversityComparisonKey.pem --run-label $LABEL
```

Mock へのアクセスが 90 秒止まり、SQS が空になったら終わる。

### 2-7. Worker を止めて結果を回収する

```bash
# Worker 1〜N で
cd ~/bench-app && bench stop worker && bench logs worker | grep "RECORD_DB\] closed"    # dropped=0 であること
```

```bash
# 手元
scp -i ~/UniversityComparisonKey.pem -r ubuntu@13.196.23.137:/var/lib/benchmark/results/$LABEL benchmark/results/
python -m benchmark.collect_resources --key ~/UniversityComparisonKey.pem --run-dir benchmark/results/$LABEL --role worker
python -m benchmark.analysis.report run benchmark/results/$LABEL
```

`collect_resources` は起動中の全 Worker から最新の記録を集める。その計測で使っていない Worker の記録は、計測期間外のため集計時に自動で除外される。

確認ポイント: coverage 100%、unexpected failures 0、Crawl-delay violations 0。

---

## 3. 毎回の手順（Legacy）

```bash
LABEL=A-legacy-x1-run1
```

1. 手元: `python -m benchmark.neon_branch reset`（Legacy は SQS・Valkey を使わない）
2. Mock: 2-3 と同じ（`$LABEL` を変える）
3. 分散版の Worker は全台止めておく（2-1）
4. Legacy:
   ```bash
   cd ~/legacy-app
   sudo docker compose -p bench -f docker-compose.yml -f docker-compose.bench.yml --profile etl run --rm --build etl
   ```
   終わると自動で終了する（`Stage 3/3 complete` まで出ること）
5. 手元:
   ```bash
   scp -i ~/UniversityComparisonKey.pem -r ubuntu@13.196.23.137:/var/lib/benchmark/results/$LABEL benchmark/results/
   python -m benchmark.collect_resources --key ~/UniversityComparisonKey.pem --run-dir benchmark/results/$LABEL --role legacy
   python -m benchmark.analysis.report run benchmark/results/$LABEL
   ```

---

## 4. 全部終わったら（手元）

```bash
python -m benchmark.analysis.report compare benchmark/results/A-legacy-* benchmark/results/A-distributed-* --baseline A-legacy-x1 \
  --out benchmark/results/A-compare.md --csv benchmark/results/A-compare.csv
```

- `A-compare.csv` を Google スプレッドシートに読み込んでグラフにする（横軸 `workers`、縦軸 `urls_per_sec`。Legacy は基準線）
- `benchmark/results/` は git 管理外。README に載せる元データは `docs/` などに CSV をコピーしてコミットする
- 片付け: `terraform.tfvars` の `worker_count` を 2 に戻して apply、Mock・Legacy は `benchmark_enabled` / `benchmark_legacy_enabled` を false にして apply（`terraform destroy` は使わない）

---

## 付録: 想定外の遅れの見つけ方（接続先との往復による遅延の例）

2026-10-13、`A-distributed-x1-run1` の所要時間 1,611 秒のうち約 300 秒が、ページを1件も取得していない「準備」に使われていた。原因は producer が sitemap の候補（約 4,100 件）を1件ずつキューログとして Neon（シンガポール）に書き込んでいたこと（1行あたり約 78 ms の往復 × 約 4,100 行 ≒ 320 秒）。Legacy・Worker は `QUEUE_LOG_ENABLED=0` でログを止めていたが、producer だけがこの設定を見ていなかった（修正済み、R9）。

### 1. 時間を「準備」と「クロール」に分ける

```bash
python -m benchmark.analysis.report run benchmark/results/$LABEL | grep "total time"
# - total time: 1611.2 sec (seed phase 299.9 sec + crawl phase 1311.3 sec)
```

- seed phase: 最初のアクセス（robots.txt / sitemap）から最初のページ取得まで。producer（Legacy は起点の読み込み）の処理時間
- 数秒が正常。数十秒以上なら producer 側を疑う（試験B では 0.1〜0.5 秒）

### 2. 計測中に Mock のアクセスログで「何が来ているか」を見る（Mock で）

```bash
F=/var/lib/benchmark/results/$LABEL/access.jsonl
wc -l < $F                                                      # 増えているか
tail -3 $F | cut -c1-200                                        # 最後に来たリクエスト
python3 -c "
import json, collections
rows = [json.loads(l) for l in open('$F')]
print(collections.Counter('robots/sitemap' if r['path'] in ('/robots.txt', '/sitemap.xml') else 'page' for r in rows))
"
```

robots.txt / sitemap.xml だけが来ていて page が 0 のまま → producer が起点の準備中（SQS への投入前）。

### 3. producer の出力を見る（Control Plane）

`bench run --rm controlplane` の出力で、各段階の間隔を見る。

| 出力 | 意味 |
|---|---|
| `[PRODUCER] start targets=...` | 起点の読み込み完了 |
| `[PRODUCER] seeding sitemap candidates ...` → `sitemap seeding done` | sitemap の取得。ここが長い |
| `[QUEUE_LOG] flush ok events=200 ... elapsed_ms=... ms_per_row=...` | **キューログの書き込み。`ms_per_row` が数十 ms なら、1行ごとの DB 往復が遅れの原因** |
| `[PRODUCER] queued initial_tasks=...` | SQS への投入完了 |

計測時は `QUEUE_LOG_ENABLED=0` のため `[QUEUE_LOG] flush` は出ず、代わりに `queue state logging disabled` が出る。

### 4. 接続先との往復時間（RTT）を測る（EC2 で）

「回数 × 1回の往復時間」で見積もる。Neon は ap-southeast-1（シンガポール）、EC2 は ap-northeast-1（東京）にあり、1往復 70〜80 ms かかる。

```bash
# Control Plane / Worker（~/bench-app、alias bench 設定済み）: Neon への SELECT 1 を10回
bench run --rm controlplane python -c "
import os, time, psycopg2
c = psycopg2.connect(os.environ['PARENT_DB_OWNER_CONNECTION']); cur = c.cursor()
t = []
for _ in range(10):
    s = time.perf_counter(); cur.execute('SELECT 1'); cur.fetchone(); t.append((time.perf_counter() - s) * 1000)
print('Neon RTT ms: min %.0f / median %.0f' % (min(t), sorted(t)[5]))
"
```

| 接続先 | 目安（東京の EC2 から） |
|---|---|
| Neon（シンガポール） | 70〜80 ms |
| Valkey・SQS・Mock（同じ VPC / リージョン） | 数 ms |

1件ごとに Neon へ往復する処理が数千回あると、それだけで数分かかる。分散版の Worker の抽出結果の保存は、件数でまとめて書き込む（`[RECORD_DB] flushed records=... elapsed=...`）ため、この影響を受けにくい。

### 5. よくある原因と確認先

| 症状 | 疑う箇所 |
|---|---|
| seed phase が長い | producer のキューログ（`QUEUE_LOG_ENABLED`）、sitemap の取得、SQS への投入（1件ずつ送信） |
| crawl phase が長く CPU も低い | Worker の窓口数（`WORKER_MAX_CONCURRENCY`）。各窓口は Crawl-delay の待ち中もメッセージを持つ |
| crawl phase が長く CPU が高い | HTML の解析・抽出（t3.micro の CPU） |
| Worker のログに `[RECORD_DB] flush` が長い（数秒以上） | Neon への書き込み（往復時間 × 件数） |

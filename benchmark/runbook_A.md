# 試験A（スケーリング）手順書

目的: 1,000 ドメイン（31,000 ページ、Crawl-delay 1 秒、理論上限 1,000 URLs/sec）で、Legacy と分散版 1〜4 台を同一条件で計測し、台数による Throughput の変化をグラフにする。

試験C（5章）で、Worker 1プロセスは窓口32で約 20 URLs/sec が限界、EC2（t3.micro、2 vCPU）の2つ目のコアはほぼ空いていると分かった。そこで試験A は **1台あたり2プロセス（`--scale worker=2`）× 窓口32** で台数を増やす（2026-10-17 にシナリオを 100 ドメインから 1,000 ドメインに変更。旧版は窓口の合計が 100 を超える x2 以降でシナリオの上限に当たるため）。

| run-label | 構成 | 窓口の合計 | 見込み Throughput | 見込み時間 |
|---|---|---:|---:|---|
| `A-legacy-x1-run1` | Legacy | - | - | 未知（試験B では分散版1台と同程度） |
| `A-distributed-x1-p2-c32-run1` | Worker 1台 × 2プロセス | 64 | 約 40 | 約 13 分 |
| `A-distributed-x2-p2-c32-run1` | Worker 2台 × 2プロセス | 128 | 約 80 | 約 7 分 |
| `A-distributed-x3-p2-c32-run1` | Worker 3台 × 2プロセス | 192 | 約 120 | 約 5 分 |
| `A-distributed-x4-p2-c32-run1` | Worker 4台 × 2プロセス | 256 | 約 160 | 約 4 分 |

- 最初の x1-p2 が「2プロセスで約2倍」という予想の確認を兼ねる。試験C の1プロセス（20.04 URLs/sec）の 1.6 倍（約 32）を大きく下回るなら、先に進まず原因を調べる
- 準備（seed phase: 1,000 ドメインの robots.txt・sitemap の取得と SQS への投入）は台数を増やしても縮まないため、`report` の `crawl phase only` / `Crawl URLs/sec`（クロールの時間だけで割った値）も記録する

- 時間に余裕があれば、各構成の 2・3 回目（`-run2`, `-run3`）を取る。集計は自動で中央値にまとめる
- コマンドはすべて **Git Bash** で実行する（PowerShell はパイプで文字化けする）
- 手元の `python` はプロジェクトの `.venv` のものを使う（`source .venv/Scripts/activate`）。システムの Python には `dotenv` がなく、`NEON_API_KEY is not set` などで止まる
- シナリオ: `benchmark/mock/scenarios/A_scaling.yaml`（1,000 ドメイン × 31 ページ）。seed（1,000 件）は bench-base に登録済み（2026-10-17、`prepare_seeds --seed-urls benchmark/results/A-inspect/seed_urls.txt`）。Mock（`~/bench-mock`）と Control Plane（`~/bench-app/benchmark/results/A-inspect`）にも配置済み。旧版（100 ドメイン）の一覧は `A-inspect-v1-100domains`

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

1台で複数プロセスのときにリソースの記録が重ならない修正（ファイル名にコンテナの hostname を付ける）が入ったコードを使う。手元で commit・push してから:

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

### 1-6. 全 Worker の窓口数を 32 にする（手元）

5章の「全 Worker の窓口数をそろえる」を `N=32` で実行する（2026-10-17 に設定済み）。

### 便利な定義（各マシンで、ターミナルを開くたびに）

```bash
# Control Plane・Worker（~/bench-app）
alias bench='sudo docker compose -p bench -f docker-compose.yml -f docker-compose.bench.yml --profile distributed'
```

---

## 2. 毎回の手順（分散版 N 台）

`LABEL=A-distributed-x<N>-p2-c32-run1` として進める。

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
手元（結果を回収する PC）のシェルでも同じ LABEL を設定し直すこと。

```bash
LABEL=A-distributed-x1-p2-c32-run1     # 毎回変える（x1〜x4 の台数に合わせる）
sudo systemctl stop bench-mock; sudo systemctl reset-failed bench-mock 2>/dev/null; cd ~/bench-mock
sudo systemd-run --unit bench-mock --working-directory "$HOME/bench-mock" "$HOME/bench-mock/.venv/bin/python" \
  -m benchmark.mock.server --scenario benchmark/mock/scenarios/A_scaling.yaml --run-label $LABEL \
  --results-dir /var/lib/benchmark/results --certfile /etc/benchmark/server.crt --keyfile /etc/benchmark/server.key
systemctl is-active bench-mock                                  # active であること
sudo journalctl -u bench-mock -n 2 --no-pager | tail -1         # "[MOCK] scenario=A-scaling domains=1000 ... log=.../$LABEL/..." と出ること
```


**必ず上の2行で起動を確認してから先に進む。** `systemd-run` は起動に失敗しても画面にエラーを出さない（ファイル名の間違いや、既に使った run-label だと起動直後に止まる）。Mock が止まったまま producer を実行すると、起点の取得がすべて失敗し、Valkey の重複排除キーが残るため、片付け（2-1・2-2）からやり直しになる（2026-10-13 に発生）。

### 2-4. 使う N 台の Worker を起動する（Worker 1〜N で）

```bash
cd ~/bench-app && bench up -d --build --scale worker=2 worker
bench ps worker                                # bench-worker-1 と bench-worker-2 の2つが Up であること
bench logs worker | grep "start worker_id"     # 2行（プロセスごと）、どちらも max_concurrency=32 であること
```

`--scale worker=2` は **1台の EC2 の中のプロセス数**で、台数（x1〜x4）に関係なく常に 2（t3.micro の vCPU 数）。台数は、このコマンドを実行する EC2 の数で決まる（x1 は Worker 1 だけ、x2 は Worker 1・2、…）。


### 2-5. producer を実行する（Control Plane で）

```bash
cd ~/bench-app && bench run --rm --build controlplane     # targets=1000 になること
```

### 2-6. 完了を待つ（手元）

```bash
python -m benchmark.wait_done --key ~/UniversityComparisonKey.pem --run-label $LABEL
```

Mock へのアクセスが 90 秒止まり、SQS が空になったら終わる。

### 2-7. Worker を止めて結果を回収する

```bash
# Worker 1〜N で
cd ~/bench-app && bench stop worker && bench logs worker | grep "RECORD_DB\] closed"    # 2行（プロセスごと）、どちらも dropped=0 であること
```

```bash
# 手元
scp -i ~/UniversityComparisonKey.pem -r ubuntu@13.196.23.137:/var/lib/benchmark/results/$LABEL benchmark/results/
python -m benchmark.collect_resources --key ~/UniversityComparisonKey.pem --run-dir benchmark/results/$LABEL --role worker
python -m benchmark.analysis.report run benchmark/results/$LABEL
```

`collect_resources` は起動中の全 Worker から新しい順に4つまで（`--files`）の記録を集める（1プロセス1ファイル、`resources/<インスタンス名>__<ファイル名>.csv`）。前回以前の計測や、その計測で使っていない Worker の記録は、計測期間外のため集計時に自動で除外される。`report run` の `resources:` 行の `N processes` が「台数 × 2」になっていることを確認する。

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

- `A-compare.csv` を Google スプレッドシートに読み込んでグラフにする（横軸 `workers`、縦軸 `urls_per_sec` と `crawl_urls_per_sec`。Legacy は基準線）。`workers` は Mock から見た IP の数（= EC2 の台数）で、プロセス数ではない
- `benchmark/results/` は git 管理外。README に載せる元データは `docs/` などに CSV をコピーしてコミットする
- 片付け: `terraform.tfvars` の `worker_count` を 2 に戻して apply、Mock・Legacy は `benchmark_enabled` / `benchmark_legacy_enabled` を false にして apply（`terraform destroy` は使わない）

---

## 5. 試験C（Worker 1台の限界 → 台数を増やす）

試験A（100 ドメイン）は窓口の合計が 100 に近づくとシナリオの上限（100 URLs/sec）で頭打ちになるため、1台の限界は試験C（`benchmark/mock/scenarios/C_capacity.yaml`: 500 ドメイン × 21 ページ = 10,500 ページ、上限 500 URLs/sec）で測る。

| 段階 | run-label | 内容 |
|---|---|---|
| ① 1台の限界 | `C-distributed-x1-c16-run1`, `-c32-`, `-c64-`（伸びれば `-c128-`） | Worker 1台で窓口数を変える |
| ② 台数を増やす | `C-distributed-x2-c<N>-run1`, `-x3-`, `-x4-` | ①で決めた窓口数 N で台数を変える |
| ③ 基準線 | `C-legacy-x1-run1` | Legacy |

試験A との違い（それ以外は 2・3 章と同じ）:

- seed は C の 500 件を登録し直す（`prepare_seeds --seed-urls benchmark/results/C-inspect/seed_urls.txt`。2026-10-17 以降の bench-base は試験A の 1,000 件）。片付けは `benchmark/results/C-inspect/pages.csv` を使う（Control Plane にも送付済み）
- Mock の起動: `--scenario benchmark/mock/scenarios/C_capacity.yaml`
- 窓口数は各 Worker の `~/bench-app/.env` の `WORKER_MAX_CONCURRENCY` で変え、`bench up -d --build worker` で起動し直す。ログの `max_concurrency=` で確認する
- Mock の起動例:

```bash
LABEL=C-distributed-x1-c32-run1
sudo systemctl stop bench-mock; sudo systemctl reset-failed bench-mock 2>/dev/null; cd ~/bench-mock
sudo systemd-run --unit bench-mock --working-directory "$HOME/bench-mock" "$HOME/bench-mock/.venv/bin/python"   -m benchmark.mock.server --scenario benchmark/mock/scenarios/C_capacity.yaml --run-label $LABEL   --results-dir /var/lib/benchmark/results --certfile /etc/benchmark/server.crt --keyfile /etc/benchmark/server.key
systemctl is-active bench-mock                                  # active であること
sudo journalctl -u bench-mock -n 2 --no-pager | tail -1         # "[MOCK] scenario=C-capacity domains=500" と出ること
```

```bash
# 手元から全 Worker の窓口数をそろえる（N を変える）
N=32
for ip in <Worker の IP ...>; do
  ssh -i ~/UniversityComparisonKey.pem ubuntu@$ip "cd ~/bench-app && (grep -q '^WORKER_MAX_CONCURRENCY=' .env && sed -i 's/^WORKER_MAX_CONCURRENCY=.*/WORKER_MAX_CONCURRENCY=$N/' .env || echo 'WORKER_MAX_CONCURRENCY=$N' >> .env) && grep '^WORKER_MAX_CONCURRENCY=' .env"
done
```

①で見るもの（`report run` の出力）:

| 指標 | 限界の目安 |
|---|---|
| Throughput | 窓口を倍にしても 1.5 倍未満しか伸びない |
| CPU avg / max | 平均 70〜80% 以上（t3.micro、2 vCPU） |
| RSS max | 1GB に近づく |
| Crawl-delay violations / unexpected failures | 増え始める（処理が詰まっている） |
| mock overhead p95 | 数十 ms 以上なら Mock（t3.medium）側の限界を疑う |


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

---

## 付録2: 頭打ちの原因が「Python のプロセス1つ（イベントループ）」だと分かった理由

2026-10-14、試験C で Worker 1台の窓口数を 16 → 32 → 64 と増やしたところ、Throughput が 32 で頭打ちになった（R11）。EC2 全体の CPU は約 30% と余裕があるように見えたが、原因はマシンではなく Python のプロセス1つの限界だった。以下の順に、他の候補を消していって特定した。

| 窓口 | Throughput | 窓口1つあたり | プロセスの CPU（1コア=100%）平均 / 最大 | EC2 全体の CPU 平均 | RSS 最大 | 違反 |
|---:|---:|---:|---:|---:|---:|---:|
| 16 | 14.44 | 0.90 | 40% / 67% | 21% | 102 MB | 0 |
| 32 | 20.04 | 0.63 | 59% / 78% | 31% | 124 MB | 0 |
| 64 | 19.14 | 0.30 | 58% / 72% | 30% | 124 MB | 0 |

### 1. 「窓口1つあたり」の性能で、頭打ちを見つける

各窓口は「1ドメインを担当し、Crawl-delay（1秒）ごとに1件取得する」ため、窓口1つあたりの上限は 1 件/秒。

- 窓口16: 0.90 件/秒（上限に近い。窓口数に比例して伸びている）
- 窓口32: 0.63、窓口64: 0.30 件/秒（窓口を増やした分だけ、1つあたりが下がっている）

窓口を増やしても全体が約 20 URLs/秒で変わらない = 窓口の外にある「共有の何か」が詰まっている。

### 2. 共有の候補を1つずつ消す

| 候補 | 確認した値 | 判断 |
|---|---|---|
| シナリオの上限 | 理論上限 500 URLs/sec（`inspect_scenario` の theoretical max） | 20 は遠く及ばない。違う |
| Mock（t3.medium） | mock overhead p95 = 2.8〜3.5 ms | Mock は余裕がある。違う |
| メモリ | RSS 最大 124 MB（t3.micro は 1 GB） | 余裕がある。違う |
| Crawl-delay の待ち・失敗の再取得 | 違反 0、想定外の失敗 0、エラーはシナリオの失敗ページ分だけ | 違う |
| producer の準備 | seed phase 20〜30 秒（crawl phase は約 500 秒） | 主因ではない |
| EC2 全体の CPU | 平均 30% | 一見余裕がある → 3 で詳しく見る |

### 3. 「EC2 全体の CPU」と「プロセスの CPU」を分けて見る

CPU の記録（`resources/*.csv`）には2種類の値がある。

| 列 | 100% の意味 |
|---|---|
| `system_cpu_percent` | EC2 の全コア（t3.micro は 2 vCPU）がすべて使用中 |
| `process_cpu_percent` | クローラーのプロセスが **1コア分**を使い切っている |

- Python のプロセスは、GIL（同時に1スレッドしか Python のコードを実行できない仕組み）があるため、基本的に1コアしか使えない。Worker はすべての窓口を1つのイベントループ（1スレッド）で動かしている
- プロセスの CPU は 1コアの 60〜80%（最大 78%）。EC2 全体の 30% は「2コアのうち、1コアの6割」とほぼ同じで、**2つ目のコアはほとんど使われていない**
- つまり「CPU に余裕がある」のは EC2 全体の話で、クローラーのプロセスから見ると、自分が使える1コアはほぼ埋まっている

計測期間中のプロセスの CPU は次のように確認できる（手元）。

```bash
python - <<'EOF'
import csv, json
from datetime import datetime, timezone
L = "C-distributed-x1-c32-run1"
s = json.load(open(f"benchmark/results/{L}/summary.json", encoding="utf-8"))
t0, t1 = (datetime.fromtimestamp(s[k], tz=timezone.utc).replace(tzinfo=None) for k in ("start_ts", "end_ts"))
rows = [r for r in csv.DictReader(open(f"benchmark/results/{L}/resources/UniversityComparison-worker-1.csv", encoding="utf-8"))
        if t0 <= datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S") <= t1]
p = [float(r["process_cpu_percent"]) for r in rows]
print(f"process CPU (1 core = 100%): avg {sum(p)/len(p):.0f}% max {max(p):.0f}%")
EOF
```

`report run` の `resources: CPU avg ...` は EC2 全体（`system_cpu_percent`）の値。2026-10-17 から同じ行に `process CPU (1 core=100%) avg / max`（`process_cpu_percent`）も出るので、1プロセスの限界はそちらで見られる（例: c32 は avg 58% / max 78%）。

### 4. CPU に表れない「待ち」も、イベントループを止めている

プロセスの CPU が 100% に届かなくても頭打ちになるのは、イベントループを止める同期呼び出しがあるため。

- `crawler/distributed/worker.py` の SQS のメッセージ削除（`task_queue.delete_task(...)`）と、見つけたリンクの送信（`_forward_discovered_tasks` 内の `task_queue.send_task(...)`）は、boto3（同期処理）を `await` せずに直接呼んでいる
- この呼び出しが SQS の応答を待つ間（数〜数十 ms）、イベントループは止まり、他の全窓口も待たされる。待っている間は CPU を使わないため、CPU 使用率には表れない
- 窓口が増えるほど1秒あたりの呼び出し回数が増え、「CPU 処理（HTML の解析・抽出）＋同期呼び出しの待ち」でイベントループの時間がほぼ埋まる

（受信 `receive_tasks` は R7 で専用スレッドに移しているため、イベントループを止めない）

### 5. 結論と、確かめるための実験

- 頭打ちは「EC2 1台」ではなく「Python のプロセス1つ（1つのイベントループ）」の限界（約 20 URLs/sec）
- CPU 処理と同期呼び出しの待ちの割合はまだ測っていない。次の実験で確かめる

| 実験 | 結果がこうなら | 分かること |
|---|---|---|
| 同じ EC2 で Worker を2プロセスにする（`bench up -d --scale worker=2 worker`） | 約 2 倍（約 40 URLs/sec）になる | 限界はプロセス単位。コアの数だけプロセスを並べれば伸びる |
| 同上 | ほとんど伸びない | EC2 全体（CPU クレジット・ネットワーク等）の別の限界 |
| SQS の削除・送信を別スレッドで行うように直す | 1プロセスの頭打ちが上がる | 同期呼び出しの待ちが効いていた |

### 6. 同じ種類の問題を見つけるときの手順

1. 並列数（窓口・プロセス・台数）を変えて、**1つあたり**の性能を見る。1つあたりが下がり始めたら、共有の何かが詰まっている
2. 共有の候補（シナリオの上限・Mock・DB・メモリ・CPU）を、値を見て1つずつ消す
3. CPU は「マシン全体」と「プロセス単位」を分けて見る。Python は1プロセス1コアが基本
4. CPU に表れない待ち（同期 I/O、DB・外部 API への往復）がイベントループの中にないか、コードで確認する

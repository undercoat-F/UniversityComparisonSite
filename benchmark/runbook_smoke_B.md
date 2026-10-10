# 試験B（Worker 1台）の小規模実行 手順書

目的: AWS 上で、分散版が Mock を最後までクロールでき、Crawl-delay を守れることを確認する（本計測の前の動作確認）。

登場するマシン:

| マシン | 役割 | 操作のしかた |
|---|---|---|
| 手元の PC | コードの管理、Neon の操作、結果の集計 | Git Bash（リポジトリのルート） |
| Mock（`13.196.23.137`） | Mock Server。`bench-mock` サービスとして起動済み | `ssh -i ~/UniversityComparisonKey.pem ubuntu@13.196.23.137` |
| Control Plane（`35.73.202.127`） | producer（起点を SQS に投入）、片付け | `ssh -i ~/UniversityComparisonKey.pem ubuntu@35.73.202.127` |
| Worker 1 | クロール | 起動後に IP を確認して ssh |

> Worker 2 は停止したままにする（2台目が動くと「1台」の計測にならない）。

---

## 0. 手元の PC での準備

### 0-1. コードを GitHub に push する

EC2 は GitHub からコードを取得する。Crawl-delay の修正などが未コミットのため、`sqs-distributed` ブランチにコミットして push する。

```bash
git status
git add <変更ファイル>
git commit -m "..."
git push origin sqs-distributed
```

### 0-2. 片付け用の URL 一覧（pages.csv）を作る

```bash
python -m benchmark.mock.inspect_scenario benchmark/mock/scenarios/B_crawl_delay.yaml --out benchmark/results/B-inspect
```

### 0-3. Neon の bench-run を初期状態に戻す

```bash
python -m benchmark.neon_branch reset
```

> 手元の `.env` の `DB_HOST` は**本番のまま**にしておく（`neon_branch` が本番ブランチの特定に使う）。

---

## 1. Worker 1 を起動する

### 1-1. EC2 を起動して IP を確認する（手元）

```bash
ID=$(aws ec2 describe-instances --filters "Name=tag:Name,Values=UniversityComparison-worker-1" \
  --query "Reservations[].Instances[].InstanceId" --output text)
aws ec2 start-instances --instance-ids $ID
aws ec2 wait instance-running --instance-ids $ID
aws ec2 describe-instances --instance-ids $ID --query "Reservations[].Instances[].PublicIpAddress" --output text
```

### 1-2. 本番用の Worker コンテナを止める（Worker 1）

本番用の Worker コンテナは `restart: unless-stopped` のため、EC2 の起動と同時に本番設定で動き出している。計測用と混ざらないよう止める。

```bash
cd ~/UniversityComparisonSite
sudo docker ps                                        # 動いているコンテナを確認
sudo docker compose --profile distributed stop worker
```

---

## 2. 証明書を配る（手元）

```bash
python -m benchmark.distribute_certs --key ~/UniversityComparisonKey.pem
python -m benchmark.distribute_certs --key ~/UniversityComparisonKey.pem --check   # HTTP 404 になること（シナリオにないホストへの確認。000 なら証明書か名前解決の失敗）
```

---

## 3. 計測用のコードと env を用意する（Control Plane と Worker 1 の両方）

本番用のフォルダ（`~/UniversityComparisonSite`、CD が `main` を pull する）には手を付けず、**別フォルダ `~/bench-app`** に計測用のコードを置く。`.env` もそのフォルダ用のコピーを編集するので、本番の `.env` は変わらない。

```bash
cd ~/UniversityComparisonSite
git fetch origin sqs-distributed
git worktree add ~/bench-app origin/sqs-distributed   # 2回目以降は: cd ~/bench-app && git checkout --detach origin/sqs-distributed
cp ~/UniversityComparisonSite/.env ~/bench-app/.env
nano ~/bench-app/.env
```

### 編集する項目（`benchmark/env_checklist.md` の要約）

| 変数 | Control Plane | Worker 1 |
|---|---|---|
| `DB_HOST` | `ep-aged-rice-aovzws60.c-2.ap-southeast-1.aws.neon.tech`（bench-run） | 同左 |
| `PARENT_DB_OWNER_CONNECTION` | `@` の後ろのホストを bench-run に置き換えたもの | **空**（`PARENT_DB_OWNER_CONNECTION=`） |
| `SEED_URLS_TABLE` | `observer.bench_seed_urls` | 同左 |
| `ETL_RECENT_SKIP_MONTHS` | `0` | `0` |
| `QUEUE_LOG_ENABLED` | `0` | `0` |
| `ETL_TAG_CLASS_LOG_URL_LIMIT_PER_DOMAIN` | `0` | `0` |
| `CRAWL_RESOURCE_SAMPLES_TABLE` | **空** | **空** |
| `RECORD_DB_ENABLED` | `1` | `1` |
| `ETL_WORKER_MAX_DEPTH` | （不要） | `5` |
| `SQS_VISIBILITY_TIMEOUT_SEC` | （不要） | `180` |
| `WORKER_ID` | （不要） | `worker-1` |

- 値の後ろにコメントを書かない（`RECORD_DB_ENABLED=1#...` は値が `1#...` になる）。コメントは別の行に書く
- `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` は書かなくてよい（`docker-compose.bench.yml` が設定する）

確認（パスワードは表示しない）:

```bash
grep -E "^(DB_HOST|SEED_URLS_TABLE|ETL_RECENT_SKIP_MONTHS|QUEUE_LOG_ENABLED|ETL_TAG_CLASS_LOG_URL_LIMIT_PER_DOMAIN|CRAWL_RESOURCE_SAMPLES_TABLE|RECORD_DB_ENABLED|ETL_WORKER_MAX_DEPTH|WORKER_ID)=" ~/bench-app/.env
grep -E "^PARENT_DB_OWNER_CONNECTION=" ~/bench-app/.env | sed -E 's#//[^@]*@#//***@#'
```

### compose の書き方（このあと何度も使う）

```bash
cd ~/bench-app
alias bench='sudo docker compose -p bench -f docker-compose.yml -f docker-compose.bench.yml --profile distributed'
```

`-p bench` は必須。`docker-compose.yml` にプロジェクト名が書かれているため、付けないと本番用のコンテナと同じ名前になり置き換わってしまう。

---

## 4. Valkey・SQS を片付ける

Valkey は VPC 内からしか接続できないため Control Plane で、SQS は EC2 の IAM ロールにキューを空にする権限（`sqs:PurgeQueue`）がないため手元の PC で片付ける。

```bash
# 手元の PC: pages.csv を Control Plane に送り、SQS を片付ける
# benchmark/results/ は .gitignore 対象で clone では作られないため、先に作る（ないと scp が失敗する）
ssh -i ~/UniversityComparisonKey.pem ubuntu@35.73.202.127 "mkdir -p ~/bench-app/benchmark/results"
scp -i ~/UniversityComparisonKey.pem -r benchmark/results/B-inspect ubuntu@35.73.202.127:~/bench-app/benchmark/results/
ssh -i ~/UniversityComparisonKey.pem ubuntu@35.73.202.127 "ls ~/bench-app/benchmark/results/B-inspect"   # pages.csv があること
python -m benchmark.reset_state --pages benchmark/results/B-inspect/pages.csv --skip-valkey             # 確認のみ
python -m benchmark.reset_state --pages benchmark/results/B-inspect/pages.csv --skip-valkey --execute   # 実行

# Control Plane（~/bench-app、alias bench を設定済み）: Valkey を片付ける
bench run --rm --build -v "$PWD/benchmark:/app/benchmark" controlplane \
  python -m benchmark.reset_state --pages /app/benchmark/results/B-inspect/pages.csv --skip-sqs              # 確認のみ
bench run --rm -v "$PWD/benchmark:/app/benchmark" controlplane \
  python -m benchmark.reset_state --pages /app/benchmark/results/B-inspect/pages.csv --skip-sqs --execute    # 実行
```

初回は Valkey・SQS とも空のはずなので、件数が 0 なら「実行」は省略してよい。

---

## 5. Worker 1 で計測用 Worker を起動する（Worker 1）

```bash
cd ~/bench-app   # alias bench を設定
bench up -d --build worker
bench logs -f worker      # Ctrl+C で抜ける（コンテナは動き続ける）
```

確認ポイント:

- `[WORKER] start worker_id=worker-1:...` が出る
- `[RATE_LIMIT][WARN]` が**出ない**（出たら Valkey につながっていない）
- `[WORKER][WARN] record DB writing disabled` が**出ない**
- `waiting for messages...` が繰り返される（まだタスクがないので正常）

---

## 6. producer を実行する（Control Plane）

```bash
cd ~/bench-app
bench run --rm --build controlplane
```

確認ポイント:

- `[PRODUCER] start targets=3`（0 なら seed テーブルか `DB_HOST` が違う）
- `[PRODUCER] record DB prepared`
- `sitemap seeding done candidates=...`（0 より大きい）
- `[PRODUCER] queued initial_tasks=...`

---

## 7. 様子を見る

```bash
# Mock: リクエストが届いているか
sudo tail -f /var/lib/benchmark/results/B-smoke-x1-run1/access.jsonl

# Worker 1: 処理の様子
bench logs -f worker

# 手元: SQS の残り
aws sqs get-queue-attributes --queue-url https://sqs.ap-northeast-1.amazonaws.com/429056788124/crawl-tasks.fifo \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

理論上の最短時間は約 90 秒（domain-c: 30 ページ × 3 秒）。

## 8. 終了を判断して止める

SQS の 2 つの値が 0 のまま 1 分ほど経ち、Mock のログも増えなくなったら完了。

```bash
# Worker 1
bench stop worker
bench logs worker | grep RECORD_DB      # "closed written=..." で DB への書き込み件数を確認

# Mock
sudo systemctl stop bench-mock
```

## 9. 結果を回収して集計する（手元）

```bash
scp -i ~/UniversityComparisonKey.pem -r ubuntu@13.196.23.137:/var/lib/benchmark/results/B-smoke-x1-run1 benchmark/results/
python -m benchmark.analysis.report run benchmark/results/B-smoke-x1-run1
```

期待する結果: coverage 100%、Crawl-delay violations 0、unexpected failures 0。

#-------------------
1.片付け用のURL一覧を作る
python -m benchmark.mock.inspect_scenario benchmark/mock/scenarios/B_crawl_delay.yaml --out benchmark/results/B-inspect

2.: Neon の bench-run を初期状態に戻す
python -m benchmark.neon_branch reset
仮想環境を有効にしないと起動できない(スクリプトのライブラリが入っていないから)

3.sshで各EC2へ入り、git clone,.envのコピー

4.4：Valkey と SQS の片付け

片付けは 2 か所で行います。SQS は、キューを空にする権限がある手元の PC から行います。Valkey は VPC の中からしか接続できないので、Control Plane から行います。どちらも、まず確認だけ（何も消さない）で実行してください。

① 手元の PC（Git Bash、リポジトリのルート）
python -m benchmark.reset_state --pages benchmark/results/B-inspect/pages.csv --skip-valkey

② Control Plane（pages.csv を送ってから、~/bench-app で
python -m benchmark.reset_state --pages benchmark/results/B-inspect/pages.csv --skip-valkey

② Control Plane（pages.csv を送ってから、~/bench-app で
# 手元の PC から送る
scp -i ~/UniversityComparisonKey.pem -r benchmark/results/B-inspect ubuntu@35.73.202.127:~/bench-app/benchmark/results/

# Control Plane で実行
cd ~/bench-app
alias bench='sudo docker compose -p bench -f docker-compose.yml -f docker-compose.bench.yml --profile distributed'
bench run --rm --build -v "$PWD/benchmark:/app/benchmark" controlplane \
  python -m benchmark.reset_state --pages /app/benchmar --skip-sqs

5.Worker 1 で計測用の Worker を起動

Worker 1 に SSH で入って

cd ~/bench-app
alias bench='sudo docker compose -p bench -f docker-compose.yml -f docker-compose.bench.yml --profile distributed'
bench up -d --build worker
bench logs -f worker

- 初回はビルドがあるので、t3.micro だと数分かかるかもしれません。
- logs -f はログを流し続けます。Ctrl+C で抜けてもコンテナは止まりません。

 6：producer の実行（Control Plane で）

Worker 1 のログは別のターミナルで流したままにしておくと、両方を同時に見られて分かりやすいです。

cd ~/bench-app
alias bench='sudo docker compose -p bench -f docker-compose.yml -f docker-compose.bench.yml --profile distributed'
bench run --rm controlplane

レコード集計
Worker を止める（Worker 1）
bench stop worker
bench logs worker | grep RECORD_DB

 Mock を止めて結果を回収・集計する（手順書のステップ 8・9）
   - Mock の停止：sudo systemctl stop bench-mock
   - 手元の PC で実行：
scp -i ~/UniversityComparisonKey.pem -r ubuntu@13.196.23.137:/var/lib/benchmark/results/B-smoke-x1-run1 benchmark/results/
python -m benchmark.analysis.report run benchmark/results/B-smoke-x1-run1